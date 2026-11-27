"""FastAPI application for ShortsClipper."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import uuid
import zipfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.config import ensure_dirs, get_settings
from app.errors import AppError
from app.jobs import JobManager
from app.schemas import (
    AnalyzeRequest,
    CaptionEditRequest,
    CaptionTranslateRequest,
    ClipSettings,
    RenderRequest,
    UploadRequest,
    ZipRequest,
)
from services import cleanup, llm, media, metadata, youtube
from services.captions import preset_payload

ensure_dirs()
SETTINGS = get_settings()
BASE_DIR = SETTINGS.base_dir
STATIC_DIR = BASE_DIR / "static"
MANAGER = JobManager(workers=2)
_cleanup_task: asyncio.Task[None] | None = None


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(max(5, SETTINGS.cleanup_interval_minutes) * 60)
        try:
            await run_in_threadpool(cleanup.cleanup_once)
            MANAGER.cleanup_old(SETTINGS.cleanup_hours)
        except Exception:  # noqa: BLE001 - the janitor must never kill the app
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _cleanup_task
    _cleanup_task = asyncio.create_task(_cleanup_loop())
    yield
    if _cleanup_task:
        _cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await _cleanup_task
    MANAGER.shutdown()


app = FastAPI(title="ShortsClipper", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------- errors


@app.exception_handler(AppError)
async def _app_error(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": exc.to_dict()})


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    messages = []
    for error in exc.errors():
        location = ".".join(str(p) for p in error.get("loc", []) if p not in {"body", "query"})
        messages.append(f"{location}: {error.get('msg')}" if location else str(error.get("msg")))
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation",
                "message": "Please check the form values.",
                "hint": "; ".join(messages)[:500],
            }
        },
    )


# ---------------------------------------------------------------- helpers


def _file_url(path: str | Path | None) -> str:
    if not path:
        return ""
    try:
        resolved = Path(path).resolve()
        relative = resolved.relative_to(SETTINGS.outputs_dir.resolve())
    except (ValueError, OSError):
        return ""
    try:
        stamp = int(resolved.stat().st_mtime)
    except OSError:
        stamp = 0
    # The query stamp busts the browser cache after a caption re-burn, which
    # writes a new file to the same final.mp4 path.
    return f"/media/{relative.as_posix()}?v={stamp}"


def _decorate(job: dict[str, Any]) -> dict[str, Any]:
    for clip in job.get("clips", []):
        files = clip.pop("files", {}) or {}
        clip["file_urls"] = {key: _file_url(value) for key, value in files.items() if value}
        if clip.get("file_urls", {}).get("video"):
            clip["download_url"] = f"/api/jobs/{job['id']}/clips/{clip['id']}/download"
        for variant in clip.get("variants", []) or []:
            variant_files = variant.pop("files", {}) or {}
            variant["file_urls"] = {key: _file_url(value) for key, value in variant_files.items()}
        clip.pop("translated", None)
    done = [c for c in job.get("clips", []) if c.get("status") == "done"]
    job["zip_url"] = f"/api/jobs/{job['id']}/zip" if done else ""
    job["rendered_count"] = len(done)
    job["candidate_count"] = len(job.get("clips", []))
    return job


def _public(job_id: str) -> dict[str, Any]:
    return _decorate(MANAGER.public(job_id))


def _safe_name(text: str, fallback: str = "clip") -> str:
    text = re.sub(r"[^\w\-]+", "_", (text or "").strip(), flags=re.UNICODE).strip("_")
    return (text[:48] or fallback)


def _clip_for(job_id: str, clip_id: str) -> dict[str, Any]:
    return MANAGER.find_clip(job_id, clip_id)


def _video_path(clip: dict[str, Any]) -> Path:
    path = (clip.get("files") or {}).get("video")
    if not path or not Path(path).exists():
        raise AppError(
            "This clip is not rendered yet.",
            code="clip_not_rendered",
            hint="Render the clip first.",
        )
    return Path(path)


# ---------------------------------------------------------------- app / meta


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    path = STATIC_DIR / "index.html"
    if not path.exists():
        raise AppError("The UI is missing.", code="ui_missing", hint="Expected static/index.html")
    return FileResponse(path)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> JSONResponse:
    return JSONResponse(status_code=204, content=None)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    try:
        ffmpeg_ok = bool(media.ffmpeg_path())
    except AppError:
        ffmpeg_ok = False
    provider = llm.resolve_provider()
    return {
        "ok": True,
        "ffmpeg": ffmpeg_ok,
        "llm": {"provider": provider, "configured": llm.is_configured()},
        "pexels": bool(SETTINGS.pexels_api_key),
        "youtube": youtube.status(),
        "transcribe_backend": os.getenv("TRANSCRIBE_BACKEND", "faster-whisper"),
        "whisper_model": SETTINGS.whisper_model,
        "cleanup_hours": SETTINGS.cleanup_hours,
        "max_batch": SETTINGS.max_batch,
    }


@app.get("/api/presets")
async def presets() -> dict[str, Any]:
    return {
        "captions": preset_payload(),
        "aspects": [
            {"id": "9:16", "label": "9:16 · 1080×1920", "width": 1080, "height": 1920},
            {"id": "16:9", "label": "16:9 · 1920×1080", "width": 1920, "height": 1080},
            {"id": "1:1", "label": "1:1 · 1080×1080", "width": 1080, "height": 1080},
        ],
        "disclaimer": "Only use videos you own or have permission to use.",
    }


# ---------------------------------------------------------------- jobs


@app.post("/api/analyze")
async def analyze(request: AnalyzeRequest) -> dict[str, Any]:
    if len(request.urls) > SETTINGS.max_batch:
        raise AppError(
            f"Batch mode supports up to {SETTINGS.max_batch} URLs at a time.",
            code="batch_limit",
            hint="Split the list into smaller batches.",
        )
    job = MANAGER.create(request.urls, request.settings)
    return _public(job["id"])


@app.get("/api/jobs")
async def list_jobs() -> dict[str, Any]:
    items = []
    for job_id in MANAGER.all_ids():
        job = MANAGER.get(job_id)
        items.append(
            {
                "id": job_id,
                "created_at": job.get("created_at"),
                "status": job.get("status"),
                "progress": job.get("progress", 0),
                "stage": job.get("stage", ""),
                "sources": [
                    {"url": s.get("url"), "title": s.get("title"), "status": s.get("status")}
                    for s in job.get("sources", [])
                ],
                "clip_count": len(job.get("clips", [])),
                "rendered_count": sum(1 for c in job.get("clips", []) if c.get("status") == "done"),
                "thumb": next(
                    (
                        _file_url((c.get("files") or {}).get("thumb"))
                        for c in job.get("clips", [])
                        if c.get("status") == "done"
                    ),
                    "",
                ),
            }
        )
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"jobs": items}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    return _public(job_id)


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> dict[str, Any]:
    MANAGER.delete(job_id)
    shutil.rmtree(SETTINGS.outputs_dir / job_id, ignore_errors=True)
    shutil.rmtree(SETTINGS.work_dir / job_id, ignore_errors=True)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/render")
async def render(job_id: str, request: RenderRequest) -> dict[str, Any]:
    job = MANAGER.get(job_id)
    known = {clip["id"] for clip in job.get("clips", [])}
    missing = [clip_id for clip_id in request.clip_ids if clip_id not in known]
    if missing:
        raise AppError("One of the selected clips no longer exists.", code="not_found")
    settings = request.settings
    if settings is not None and request.ab_variants is not None:
        settings = settings.model_copy(update={"ab_variants": request.ab_variants})
    MANAGER.update(job_id, status="rendering", stage="Queued for rendering…", progress=0, error=None)
    MANAGER.submit_render(job_id, request.clip_ids, settings)
    return _public(job_id)


@app.post("/api/jobs/{job_id}/clips/{clip_id}/captions")
async def edit_captions(job_id: str, clip_id: str, request: CaptionEditRequest) -> dict[str, Any]:
    clip = _clip_for(job_id, clip_id)
    if not clip.get("beats"):
        raise AppError("Render the clip before editing captions.", code="no_captions")
    lines = {line.id: line.text for line in request.lines}
    MANAGER.update(job_id, status="rendering", stage="Updating captions...")
    MANAGER.update_clip(job_id, clip_id, status="rendering", stage="Updating captions...")
    MANAGER.submit_captions(job_id, clip_id, lines, request.settings)
    return _public(job_id)


@app.get("/api/jobs/{job_id}/clips/{clip_id}/captions")
async def get_captions(job_id: str, clip_id: str) -> dict[str, Any]:
    clip = _clip_for(job_id, clip_id)
    return {
        "beats": clip.get("beats") or [],
        "language": clip.get("language", ""),
        "translated": clip.get("translated") or {},
        "settings": clip.get("settings") or MANAGER.get(job_id).get("settings"),
        "credits": clip.get("credits") or [],
    }


@app.post("/api/jobs/{job_id}/clips/{clip_id}/translate")
async def translate_captions(
    job_id: str, clip_id: str, request: CaptionTranslateRequest
) -> dict[str, Any]:
    clip = _clip_for(job_id, clip_id)
    if not clip.get("beats"):
        raise AppError("Render the clip before translating captions.", code="no_captions")
    MANAGER.update(job_id, status="rendering", stage=f"Translating to {request.language}...")
    MANAGER.update_clip(job_id, clip_id, status="rendering", stage=f"Translating to {request.language}...")
    MANAGER.submit_translate(job_id, clip_id, request.language, request.settings)
    return _public(job_id)


@app.post("/api/jobs/{job_id}/clips/{clip_id}/metadata")
async def clip_metadata(job_id: str, clip_id: str) -> dict[str, Any]:
    """Write a click-ready title, description, hashtags and pinned comment."""
    clip = _clip_for(job_id, clip_id)
    data = metadata.generate_metadata(
        clip,
        language=clip.get("language", ""),
        beats=clip.get("beats") or [],
    )
    MANAGER.update_clip(job_id, clip_id, metadata=data)
    return data


@app.post("/api/jobs/{job_id}/clips/{clip_id}/resync")
async def resync_captions(job_id: str, clip_id: str) -> dict[str, Any]:
    """Re-time captions to the speech in the clip's own audio track."""
    clip = _clip_for(job_id, clip_id)
    if not clip.get("beats"):
        raise AppError("Render the clip before syncing captions.", code="no_captions")
    MANAGER.update(job_id, status="rendering", stage="Syncing captions to audio...")
    MANAGER.update_clip(job_id, clip_id, status="rendering", stage="Syncing captions to audio...")
    MANAGER.submit_resync(job_id, clip_id, None)
    return _public(job_id)


# ---------------------------------------------------------------- files


@app.get("/api/jobs/{job_id}/clips/{clip_id}/download")
async def download_clip(job_id: str, clip_id: str) -> FileResponse:
    clip = _clip_for(job_id, clip_id)
    path = _video_path(clip)
    index = int(clip.get("index", 0)) + 1
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"shorts_{index:02d}_{_safe_name(clip.get('hook_text', ''))}.mp4",
    )


@app.get("/api/jobs/{job_id}/clips/{clip_id}/captions.{kind}")
async def download_captions(job_id: str, clip_id: str, kind: str) -> FileResponse:
    clip = _clip_for(job_id, clip_id)
    if kind not in {"srt", "ass"}:
        raise AppError("Unknown caption format.", code="bad_format")
    path = (clip.get("files") or {}).get(kind)
    if not path or not Path(path).exists():
        raise AppError("Captions are not ready.", code="no_captions")
    index = int(clip.get("index", 0)) + 1
    return FileResponse(
        path,
        media_type="text/plain" if kind == "srt" else "text/x-ssa",
        filename=f"shorts_{index:02d}.{kind}",
    )


@app.post("/api/jobs/{job_id}/zip")
async def download_zip(job_id: str, request: ZipRequest) -> FileResponse:
    job = MANAGER.get(job_id)
    clips = [clip for clip in job.get("clips", []) if clip.get("status") == "done"]
    if request.clip_ids:
        wanted = set(request.clip_ids)
        clips = [clip for clip in clips if clip["id"] in wanted]
    clips = [clip for clip in clips if (clip.get("files") or {}).get("video")]
    if not clips:
        raise AppError(
            "No rendered clips to zip yet.",
            code="nothing_to_zip",
            hint="Render at least one clip first.",
        )
    zip_path = SETTINGS.outputs_dir / job_id / "shorts_export.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        readme = [
            "ShortsClipper export",
            "===================",
            "",
            "Only use videos you own or have permission to use.",
            "Captions are burned in; editable .srt/.ass files are included.",
            "",
        ]
        for index, clip in enumerate(clips, start=1):
            files = clip.get("files") or {}
            base = f"clip_{index:02d}_{_safe_name(clip.get('hook_text', ''))}"
            video = Path(files.get("video", ""))
            if video.exists():
                archive.write(video, f"{base}.mp4")
            for key, suffix in (("srt", ".srt"), ("ass", ".ass"), ("credits", "_credits.txt")):
                path = files.get(key)
                if path and Path(path).exists():
                    archive.write(path, f"{base}{suffix}")
            if (clip.get("credits") or []) and index == 1:
                readme.append("B-roll credits:")
                for asset in clip["credits"]:
                    readme.append(f"- {asset.get('credit')} — {asset.get('source_url')}")
        archive.writestr("README.txt", "\n".join(readme) + "\n")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"shortclipper_{job_id[-6:]}.zip",
    )


# ---------------------------------------------------------------- music


@app.post("/api/music")
async def upload_music(file: UploadFile = File(...)) -> dict[str, Any]:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}:
        raise AppError(
            "Unsupported music format.",
            code="bad_music",
            hint="Use mp3, m4a, wav, aac, ogg or flac.",
        )
    name = f"{uuid.uuid4().hex[:12]}{extension}"
    destination = SETTINGS.uploads_dir / name
    size = 0
    try:
        with destination.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > SETTINGS.max_upload_mb * 1024 * 1024:
                    raise AppError(
                        f"Music file is larger than {SETTINGS.max_upload_mb} MB.",
                        code="music_too_large",
                    )
                handle.write(chunk)
    except AppError:
        destination.unlink(missing_ok=True)
        raise
    return {"name": name, "url": f"/api/music/{name}", "size": size}


@app.get("/api/music")
async def list_music() -> dict[str, Any]:
    files = []
    for path in sorted(SETTINGS.uploads_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.suffix.lower() in {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}:
            files.append({"name": path.name, "url": f"/api/music/{path.name}"})
    return {"files": files[:20]}


@app.get("/api/music/{name}")
async def get_music(name: str) -> FileResponse:
    if "/" in name or "\\" in name or ".." in name:
        raise AppError("Invalid music file.", code="bad_music")
    path = SETTINGS.uploads_dir / name
    if not path.exists():
        raise AppError("Music file not found.", code="not_found")
    return FileResponse(path, media_type="audio/mpeg", filename=name)


# ---------------------------------------------------------------- YouTube


@app.get("/api/youtube/status")
async def youtube_status() -> dict[str, Any]:
    return youtube.status()


@app.get("/api/youtube/auth")
async def youtube_auth() -> dict[str, Any]:
    return {"url": youtube.auth_url()}


@app.get("/api/youtube/callback", response_class=HTMLResponse)
async def youtube_callback(code: str = "", state: str = "", error: str = "") -> HTMLResponse:
    if error or not code:
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;background:#0d0d10;color:#fff;padding:40px'>"
            f"<h2>YouTube connection cancelled</h2><p>{error or 'No authorization code.'}</p>"
            f"<p>You can close this tab.</p></body></html>"
        )
    try:
        await run_in_threadpool(youtube.exchange_code, code, state)
        body = "<h2>YouTube connected</h2><p>You can close this tab and upload your clips.</p>"
    except AppError as exc:
        body = f"<h2>Connection failed</h2><p>{exc.message}</p><p>{exc.hint}</p>"
    return HTMLResponse(
        f"<html><body style='font-family:sans-serif;background:#0d0d10;color:#fff;padding:40px'>"
        f"{body}<script>setTimeout(()=>window.close(),1800)</script></body></html>"
    )


@app.post("/api/youtube/upload")
async def youtube_upload(request: UploadRequest) -> dict[str, Any]:
    job_id = ""
    for candidate in MANAGER.all_ids():
        job = MANAGER.get(candidate)
        if any(clip["id"] == request.clip_id for clip in job.get("clips", [])):
            job_id = candidate
            break
    if not job_id:
        raise AppError("Clip not found.", code="not_found")
    if not request.rights_confirmed:
        raise AppError(
            "Confirm you own this video or have permission to publish it.",
            code="rights_required",
            hint="Tick the ownership checkbox before uploading.",
        )
    clip = _clip_for(job_id, request.clip_id)
    path = _video_path(clip)
    result = await run_in_threadpool(
        youtube.upload,
        path,
        request.title,
        request.description,
        request.privacy,
        request.publish_at,
    )
    MANAGER.update_clip(job_id, request.clip_id, youtube=result)
    return result


# ---------------------------------------------------------------- janitor


@app.post("/api/cleanup")
async def run_cleanup() -> dict[str, Any]:
    result = await run_in_threadpool(cleanup.cleanup_once)
    removed_jobs = MANAGER.cleanup_old(SETTINGS.cleanup_hours)
    return {**result, "jobs": removed_jobs}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/media", StaticFiles(directory=str(SETTINGS.outputs_dir)), name="media")
