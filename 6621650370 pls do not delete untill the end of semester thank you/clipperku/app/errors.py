"""Typed application errors with actionable hints for the UI."""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """An error safe to show to the end user."""

    def __init__(self, message: str, *, code: str = "error", hint: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


class FFmpegMissing(AppError):
    def __init__(self) -> None:
        super().__init__(
            "ffmpeg was not found on this machine.",
            code="ffmpeg_missing",
            hint="Install ffmpeg and add it to PATH, or set FFMPEG_BIN / FFPROBE_BIN in .env. "
            "Windows: winget install Gyan.FFmpeg. macOS: brew install ffmpeg. "
            "Docker: use the provided Dockerfile.",
        )


class TranscriptionUnavailable(AppError):
    def __init__(self, detail: str = "") -> None:
        super().__init__(
            "Speech transcription is unavailable." + (f" {detail}" if detail else ""),
            code="transcription_unavailable",
            hint="Install faster-whisper (pip install faster-whisper) and use Python 3.11. "
            "For a quick UI test without speech, set TRANSCRIBE_BACKEND=mock.",
        )


def friendly_ytdlp_error(raw: str) -> AppError:
    text = (raw or "").lower()
    if "private video" in text or "sign in to confirm" in text:
        return AppError(
            "This video is private or requires sign-in.",
            code="video_private",
            hint="Use a public video, or export YouTube cookies to a file and set YTDLP_COOKIES_FILE.",
        )
    if "age" in text and ("restrict" in text or "confirm" in text):
        return AppError(
            "This video is age-restricted.",
            code="video_age_restricted",
            hint="Sign-in is required. Export cookies to a file and set YTDLP_COOKIES_FILE.",
        )
    if "not available" in text or "unavailable" in text or "removed" in text:
        return AppError(
            "This video is unavailable in your region or was removed.",
            code="video_unavailable",
            hint="Try another public URL.",
        )
    if "live" in text and ("stream" in text or "premiere" in text):
        return AppError(
            "Live streams cannot be clipped.",
            code="video_live",
            hint="Wait until the stream ends, or use a regular upload.",
        )
    if "unsupported url" in text or "is not a valid url" in text:
        return AppError(
            "That does not look like a supported video URL.",
            code="bad_url",
            hint="Paste a full YouTube link such as https://www.youtube.com/watch?v=...",
        )
    if "javascript" in text or "nsig" in text or "player" in text:
        return AppError(
            "YouTube blocked the download (player signature challenge).",
            code="download_blocked",
            hint="Update yt-dlp (pip install -U yt-dlp), install Deno/Node, "
            "or export cookies and set YTDLP_COOKIES_FILE.",
        )
    return AppError(
        "Download failed.",
        code="download_failed",
        hint=raw.strip()[-500:] or "Check the URL and your internet connection.",
    )
