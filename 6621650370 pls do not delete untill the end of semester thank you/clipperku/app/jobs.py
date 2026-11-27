"""Job store, background execution and polling payloads."""
from __future__ import annotations

import copy
import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import ensure_dirs, get_settings
from app.errors import AppError
from app.schemas import ClipSettings


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(7)}"


class JobManager:
    def __init__(self, workers: int = 2) -> None:
        ensure_dirs()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="clipper")
        self._last_persist: dict[str, float] = {}
        self._load_existing()

    # ---------------------------------------------------------------- loading
    def _load_existing(self) -> None:
        settings = get_settings()
        for path in settings.jobs_dir.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(job, dict) or "id" not in job:
                continue
            if job.get("status") in {
                "queued",
                "processing",
                "downloading",
                "transcribing",
                "selecting",
                "rendering",
            }:
                error = {
                    "code": "server_restarted",
                    "message": "The server restarted while this job was running.",
                    "hint": "Start a new job. Any clips that already finished are still available.",
                }
                job["status"] = "error"
                job["error"] = error
                # Sources and clips kept their in-progress labels on disk;
                # clear them too so the UI does not show a zombie job.
                for source in job.get("sources", []) or []:
                    if source.get("status") in {"queued", "downloading", "transcribing", "selecting"}:
                        source["status"] = "error"
                        source["stage"] = "Failed"
                        source["error"] = error
                for clip in job.get("clips", []) or []:
                    if clip.get("status") in {"queued", "rendering"}:
                        clip["status"] = "error"
                        clip["stage"] = "Failed"
                        clip["error"] = error
            self._jobs[job["id"]] = job

    # ---------------------------------------------------------------- create
    def create(self, urls: list[str], settings: ClipSettings) -> dict[str, Any]:
        job_id = _id("job")
        job = {
            "id": job_id,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "status": "queued",
            "stage": "Queued",
            "progress": 0,
            "error": None,
            "warnings": [],
            "settings": settings.model_dump(),
            "sources": [
                {
                    "id": _id("src"),
                    "url": url,
                    "title": "",
                    "duration": 0,
                    "language": "",
                    "status": "queued",
                    "stage": "Queued",
                    "progress": 0,
                    "error": None,
                    "word_count": 0,
                }
                for url in urls
            ],
            "clips": [],
            "logs": [],
        }
        with self._lock:
            self._jobs[job_id] = job
        self.log(job_id, f"Queued {len(urls)} video(s)")
        self._persist(job_id, force=True)
        from services.pipeline import analyze_job

        self._pool.submit(self._guard, job_id, analyze_job, self, job_id)
        return copy.deepcopy(job)

    def submit_render(self, job_id: str, clip_ids: list[str], settings: ClipSettings | None) -> None:
        from services.pipeline import render_job

        self._pool.submit(self._guard, job_id, render_job, self, job_id, clip_ids, settings)

    def submit_translate(self, job_id: str, clip_id: str, language: str, settings: ClipSettings | None) -> None:
        from services.pipeline import translate_clip

        self._pool.submit(self._guard, job_id, translate_clip, self, job_id, clip_id, language, settings)

    def submit_captions(
        self,
        job_id: str,
        clip_id: str,
        lines: dict[str, str],
        settings: ClipSettings | None,
    ) -> None:
        from services.pipeline import update_captions

        self._pool.submit(self._guard, job_id, update_captions, self, job_id, clip_id, lines, settings)

    def submit_resync(self, job_id: str, clip_id: str, settings: ClipSettings | None) -> None:
        from services.pipeline import resync_captions

        self._pool.submit(self._guard, job_id, resync_captions, self, job_id, clip_id, settings)

    def _guard(self, job_id: str, fn, *args) -> None:
        try:
            fn(*args)
        except AppError as exc:
            self.set_error(job_id, exc.to_dict())
        except Exception as exc:  # noqa: BLE001 - surface unexpected failures to the UI
            self.set_error(
                job_id,
                {
                    "code": "internal",
                    "message": "Something went wrong while processing this job.",
                    "hint": f"{type(exc).__name__}: {exc}",
                },
            )
        finally:
            self._persist(job_id, force=True)

    # ---------------------------------------------------------------- mutation
    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise AppError("Job not found.", code="not_found", hint="It may have been cleaned up.")
            return job

    def find_clip(self, job_id: str, clip_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        with self._lock:
            for clip in job["clips"]:
                if clip["id"] == clip_id:
                    return clip
        raise AppError("Clip not found.", code="not_found")

    def find_source(self, job_id: str, source_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        with self._lock:
            for source in job["sources"]:
                if source["id"] == source_id:
                    return source
        raise AppError("Source not found.", code="not_found")

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self.get(job_id)
            job.update(fields)
            job["updated_at"] = now_iso()
        self._persist(job_id)

    def update_source(self, job_id: str, source_id: str, **fields: Any) -> None:
        with self._lock:
            job = self.get(job_id)
            for source in job["sources"]:
                if source["id"] == source_id:
                    source.update(fields)
                    break
            job["updated_at"] = now_iso()
        self._persist(job_id)

    def update_clip(self, job_id: str, clip_id: str, **fields: Any) -> None:
        with self._lock:
            job = self.get(job_id)
            for clip in job["clips"]:
                if clip["id"] == clip_id:
                    clip.update(fields)
                    break
            job["updated_at"] = now_iso()
        self._persist(job_id)

    def add_clip(self, job_id: str, clip: dict[str, Any]) -> None:
        with self._lock:
            self.get(job_id)["clips"].append(clip)
        self._persist(job_id, force=True)

    def log(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.setdefault("logs", []).append({"t": now_iso(), "msg": message})
            job["logs"] = job["logs"][-60:]
        self._persist(job_id)

    def warn(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if message not in job["warnings"]:
                job["warnings"].append(message)
        self._persist(job_id)

    def set_error(self, job_id: str, error: dict[str, str]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["status"] = "error"
            job["error"] = error
            job["stage"] = "Failed"
            job["updated_at"] = now_iso()
            for source in job["sources"]:
                if source["status"] in {"queued", "downloading", "transcribing", "selecting"}:
                    source["status"] = "error"
                    source["error"] = error
            for clip in job["clips"]:
                if clip["status"] in {"rendering", "queued"}:
                    clip["status"] = "error"
                    clip["error"] = error
        self._persist(job_id, force=True)

    # ---------------------------------------------------------------- serialize
    def public(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = copy.deepcopy(self.get(job_id))
        clips = []
        for clip in job.get("clips", []):
            public_clip = {k: v for k, v in clip.items() if k not in {"beats", "words", "translated_beats"}}
            public_clip["captions_ready"] = bool(clip.get("beats"))
            public_clip["warnings"] = clip.get("warnings", [])
            clips.append(public_clip)
        job["clips"] = clips
        job["files_ready"] = any(c.get("status") == "done" for c in clips)
        return job

    def all_ids(self) -> list[str]:
        with self._lock:
            return list(self._jobs.keys())

    # ---------------------------------------------------------------- persist
    def _persist(self, job_id: str, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_persist.get(job_id, 0) < 1.5:
            return
        self._last_persist[job_id] = now
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            payload = json.dumps(job, ensure_ascii=False)
        try:
            settings = get_settings()
            path = settings.jobs_dir / f"{job_id}.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass

    def cleanup_old(self, hours: float) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        removed = 0
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                try:
                    created = datetime.fromisoformat(job.get("created_at", ""))
                except ValueError:
                    continue
                if created < cutoff:
                    self._jobs.pop(job_id, None)
                    removed += 1
        return removed

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
        path = get_settings().jobs_dir / f"{job_id}.json"
        path.unlink(missing_ok=True)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
