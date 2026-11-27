"""Retention janitor: remove generated media older than N hours."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from app.config import get_settings


def _older_than(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def cleanup_once(hours: float | None = None) -> dict[str, int]:
    settings = get_settings()
    hours = settings.cleanup_hours if hours is None else hours
    cutoff = time.time() - hours * 3600
    removed_files = 0
    removed_dirs = 0

    for root in (settings.work_dir, settings.outputs_dir):
        if not root.exists():
            continue
        for child in list(root.iterdir()):
            if child.name.startswith("."):
                continue
            if child.is_dir() and _older_than(child, cutoff):
                shutil.rmtree(child, ignore_errors=True)
                removed_dirs += 1
            elif child.is_file() and _older_than(child, cutoff):
                child.unlink(missing_ok=True)
                removed_files += 1

    for folder in (settings.uploads_dir, settings.broll_dir, settings.jobs_dir):
        if not folder.exists():
            continue
        for child in list(folder.iterdir()):
            if child.name.startswith("."):
                continue
            if child.is_file() and _older_than(child, cutoff):
                child.unlink(missing_ok=True)
                removed_files += 1
            elif child.is_dir() and _older_than(child, cutoff):
                shutil.rmtree(child, ignore_errors=True)
                removed_dirs += 1

    # Remove now-empty job folders.
    for root in (settings.work_dir, settings.outputs_dir):
        if not root.exists():
            continue
        for child in list(root.iterdir()):
            if child.is_dir() and not any(child.iterdir()):
                child.rmdir()
    return {"files": removed_files, "dirs": removed_dirs, "hours": int(hours)}
