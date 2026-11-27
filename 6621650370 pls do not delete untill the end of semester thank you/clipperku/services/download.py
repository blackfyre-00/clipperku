"""YouTube download via yt-dlp with live progress and friendly errors."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.errors import AppError, friendly_ytdlp_error
from services import media

ProgressCb = Callable[[float, str], None]


def _find_output(folder: Path) -> Path | None:
    preferred = folder / "source.mp4"
    if preferred.exists() and preferred.stat().st_size > 0:
        return preferred
    candidates = [
        p
        for p in folder.glob("source.*")
        if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"} and p.stat().st_size > 0
    ]
    if not candidates:
        candidates = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".webm"}]
    return max(candidates, key=lambda p: p.stat().st_size) if candidates else None


def download(url: str, work_dir: Path, progress: ProgressCb | None = None) -> dict[str, Any]:
    """Download the best mp4 and return {path, title, duration, uploader, id}."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise AppError(
            "yt-dlp is not installed.",
            code="ytdlp_missing",
            hint="Run: pip install -r requirements.txt",
        ) from exc

    settings = get_settings()
    work_dir.mkdir(parents=True, exist_ok=True)
    last_update = 0.0

    def hook(data: dict[str, Any]) -> None:
        nonlocal last_update
        if not progress:
            return
        now = time.time()
        if now - last_update < 0.6 and data.get("status") != "finished":
            return
        last_update = now
        if data.get("status") == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            done = data.get("downloaded_bytes") or 0
            pct = (done / total * 100.0) if total else 0.0
            speed = data.get("speed") or 0
            mbps = f"{speed / 1_000_000:.1f} MB/s" if speed else ""
            progress(min(99.0, pct), f"Downloading {pct:0.0f}% {mbps}".strip())
        elif data.get("status") == "finished":
            progress(100.0, "Download complete, merging…")

    opts: dict[str, Any] = {
        "outtmpl": str(work_dir / "source.%(ext)s"),
        "format": (
            "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/"
            "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
        ),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "restrictfilenames": True,
        "windowsfilenames": True,
        "overwrites": True,
        "postprocessors": [
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
        ],
    }
    # yt-dlp needs its own ffmpeg pointer for format merging; without this it
    # reports "ffmpeg is not installed" even when the app can find ffmpeg via
    # PATH, FFMPEG_BIN or the Windows winget directory.
    try:
        opts["ffmpeg_location"] = str(Path(media.ffmpeg_path()).parent)
    except AppError:
        pass
    if settings.ytdlp_cookies_file and Path(settings.ytdlp_cookies_file).exists():
        opts["cookiefile"] = settings.ytdlp_cookies_file
    if settings.ytdlp_extra_args:
        for token in settings.ytdlp_extra_args.split():
            if token.startswith("--") and "=" in token:
                key, value = token[2:].split("=", 1)
                opts[key.replace("-", "_")] = value

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise friendly_ytdlp_error(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises many types
        raise friendly_ytdlp_error(str(exc)) from exc

    if info and info.get("is_live"):
        raise AppError(
            "This is a live stream.",
            code="video_live",
            hint="Wait until the stream ends before clipping it.",
        )

    path = _find_output(work_dir)
    if not path:
        raise AppError(
            "The download finished but no video file was produced.",
            code="download_empty",
            hint="Try again, or update yt-dlp (pip install -U yt-dlp).",
        )

    meta = {
        "path": str(path),
        "title": (info or {}).get("title") or path.stem,
        "duration": float((info or {}).get("duration") or 0.0),
        "uploader": (info or {}).get("uploader") or "",
        "id": (info or {}).get("id") or "",
        "webpage_url": (info or {}).get("webpage_url") or url,
        "description": ((info or {}).get("description") or "")[:2000],
    }
    (work_dir / "info.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress:
        progress(100.0, "Downloaded")
    return meta
