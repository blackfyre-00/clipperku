"""Small, explicit wrappers around ffmpeg / ffprobe."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from app.config import get_settings
from app.errors import AppError, FFmpegMissing

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _win_winget_search(name: str) -> str:
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return ""
    root = Path(local) / "Microsoft" / "WinGet" / "Packages"
    if not root.exists():
        return ""
    for candidate in root.glob(f"**/bin/{name}.exe"):
        return str(candidate)
    return ""


@lru_cache(maxsize=4)
def resolve_binary(name: str, override: str = "") -> str:
    if override:
        path = Path(override)
        if path.is_file():
            return str(path)
        raise FFmpegMissing()
    found = shutil.which(name) or shutil.which(f"{name}.exe")
    if found:
        return found
    if os.name == "nt":
        found = _win_winget_search(name)
        if found:
            return found
    raise FFmpegMissing()


def ffmpeg_path() -> str:
    return resolve_binary("ffmpeg", get_settings().ffmpeg_bin)


def ffprobe_path() -> str:
    return resolve_binary("ffprobe", get_settings().ffprobe_bin)


def run(cmd: Sequence[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise FFmpegMissing() from exc
    except subprocess.TimeoutExpired as exc:
        raise AppError(
            "Video processing timed out.",
            code="ffmpeg_timeout",
            hint="Try a shorter clip or a lower resolution source.",
        ) from exc


def ffmpeg(args: Sequence[str], *, timeout: int | None = None, context: str = "") -> None:
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", *args]
    proc = run(cmd, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-12:]
        raise AppError(
            f"ffmpeg failed{(' while ' + context) if context else ''}.",
            code="ffmpeg_failed",
            hint="\n".join(tail) or "Check that the source file is valid.",
        )


def probe(path: str | Path) -> dict[str, Any]:
    proc = run(
        [
            ffprobe_path(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=120,
    )
    if proc.returncode != 0:
        raise AppError(
            "Could not read the video file.",
            code="probe_failed",
            hint=(proc.stderr or "")[-400:],
        )
    data = json.loads(proc.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = 0.0
    try:
        duration = float(data.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if not duration and video:
        try:
            duration = float(video.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
    fps = 30.0
    if video and video.get("r_frame_rate"):
        try:
            num, den = video["r_frame_rate"].split("/")
            fps = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            fps = 30.0
    return {
        "duration": duration,
        "width": int(video.get("width") or 0) if video else 0,
        "height": int(video.get("height") or 0) if video else 0,
        "fps": fps or 30.0,
        "has_video": video is not None,
        "has_audio": audio is not None,
        "video_codec": (video or {}).get("codec_name", ""),
        "audio_codec": (audio or {}).get("codec_name", ""),
    }


def escape_filter_path(path: str | Path) -> str:
    """Escape an absolute path for use inside an ffmpeg filter argument."""
    text = str(Path(path).resolve()).replace("\\", "/")
    text = text.replace(":", r"\:").replace("'", r"\'")
    return text


def subtitles_filter(ass_path: Path) -> str:
    s = get_settings()
    fonts_dir = Path(s.fonts_dir)
    parts = [f"filename='{escape_filter_path(ass_path)}'"]
    if fonts_dir.is_dir() and any(fonts_dir.iterdir()):
        parts.append(f"fontsdir='{escape_filter_path(fonts_dir)}'")
    return "subtitles=" + ":".join(parts)


def thumbnail(video: Path, out: Path, at: float = 0.8) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-ss",
            f"{max(0.0, at):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            "scale=540:-2",
            "-q:v",
            "4",
            str(out),
        ],
        timeout=120,
        context="creating a thumbnail",
    )


def replace_audio(video: Path, audio: Path, out: Path) -> None:
    ffmpeg(
        [
            "-i",
            str(video),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(out),
        ],
        context="muxing audio",
    )


def duration_of(path: Path) -> float:
    try:
        return float(probe(path).get("duration") or 0.0)
    except AppError:
        return 0.0
