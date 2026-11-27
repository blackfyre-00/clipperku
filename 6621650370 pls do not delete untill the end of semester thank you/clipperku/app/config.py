"""Application configuration loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    data_dir: Path
    outputs_dir: Path
    uploads_dir: Path
    work_dir: Path
    broll_dir: Path
    jobs_dir: Path

    llm_provider: str
    llm_api_key: str
    llm_model: str
    llm_base_url: str

    pexels_api_key: str

    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    whisper_beam_size: int

    yt_client_secrets: str
    yt_token_file: str
    yt_client_id: str
    yt_client_secret: str
    yt_redirect_uri: str

    cleanup_hours: float
    cleanup_interval_minutes: int
    max_batch: int
    ffmpeg_bin: str
    ffprobe_bin: str
    caption_font: str
    fonts_dir: str
    max_upload_mb: int
    ytdlp_cookies_file: str
    ytdlp_extra_args: str

    @property
    def yt_configured(self) -> bool:
        if self.yt_client_id and self.yt_client_secret:
            return True
        return bool(self.yt_client_secrets and Path(self.yt_client_secrets).exists())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(_env("DATA_DIR", str(BASE_DIR / "data"))).resolve()
    outputs_dir = Path(_env("OUTPUTS_DIR", str(BASE_DIR / "outputs"))).resolve()
    return Settings(
        base_dir=BASE_DIR,
        data_dir=data_dir,
        outputs_dir=outputs_dir,
        uploads_dir=data_dir / "uploads",
        work_dir=data_dir / "work",
        broll_dir=data_dir / "broll",
        jobs_dir=data_dir / "jobs",
        llm_provider=_env("LLM_PROVIDER", "auto").lower(),
        llm_api_key=_env("LLM_API_KEY"),
        llm_model=_env("LLM_MODEL"),
        llm_base_url=_env("LLM_BASE_URL"),
        pexels_api_key=_env("PEXELS_API_KEY"),
        whisper_model=_env("WHISPER_MODEL", "small"),
        whisper_device=_env("WHISPER_DEVICE", "auto"),
        whisper_compute_type=_env("WHISPER_COMPUTE_TYPE", "auto"),
        whisper_beam_size=_env_int("WHISPER_BEAM_SIZE", 5),
        yt_client_secrets=_env("YT_CLIENT_SECRETS"),
        yt_token_file=_env("YT_TOKEN_FILE", str(data_dir / "youtube_token.json")),
        yt_client_id=_env("YT_CLIENT_ID"),
        yt_client_secret=_env("YT_CLIENT_SECRET"),
        yt_redirect_uri=_env("YT_REDIRECT_URI", "http://localhost:8000/api/youtube/callback"),
        cleanup_hours=_env_float("CLEANUP_HOURS", 24),
        cleanup_interval_minutes=_env_int("CLEANUP_INTERVAL_MINUTES", 30),
        max_batch=_env_int("MAX_BATCH", 10),
        ffmpeg_bin=_env("FFMPEG_BIN"),
        ffprobe_bin=_env("FFPROBE_BIN"),
        caption_font=_env("CAPTION_FONT"),
        fonts_dir=_env("FONTS_DIR", str(BASE_DIR / "static" / "fonts")),
        max_upload_mb=_env_int("MAX_UPLOAD_MB", 25),
        ytdlp_cookies_file=_env("YTDLP_COOKIES_FILE"),
        ytdlp_extra_args=_env("YTDLP_EXTRA_ARGS"),
    )


def ensure_dirs() -> None:
    s = get_settings()
    for path in (
        s.data_dir,
        s.outputs_dir,
        s.uploads_dir,
        s.work_dir,
        s.broll_dir,
        s.jobs_dir,
        s.base_dir / "static",
    ):
        path.mkdir(parents=True, exist_ok=True)
