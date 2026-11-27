"""Request models and validation for the ShortsClipper API."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

Aspect = Literal["9:16", "16:9", "1:1"]
CaptionPreset = Literal["graphic", "cinema", "karaoke", "bold_pop", "minimal"]
ReframeMode = Literal["auto", "face", "center", "blur"]
ColorGrade = Literal["none", "punch", "cinematic", "neon"]

ASPECT_SIZES: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}

_HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _clean_hex(value: str) -> str:
    if not _HEX.match(value or ""):
        raise ValueError("Color must be a hex value like #FFFFFF")
    return value if value.startswith("#") else f"#{value}"


class ClipSettings(BaseModel):
    clip_count: int = Field(5, ge=1, le=12)
    min_length: float = Field(20, ge=8, le=60)
    max_length: float = Field(60, ge=12, le=90)

    aspect: Aspect = "9:16"
    captions_enabled: bool = True
    caption_preset: CaptionPreset = "graphic"
    caption_font: str = Field("", max_length=80)
    caption_size: int = Field(82, ge=40, le=160)
    caption_color: str = "#FFFFFF"
    highlight_color: str = "#FFE14D"
    caption_position: int = Field(24, ge=16, le=38, description="% from bottom")
    caption_max_words: int = Field(2, ge=1, le=3)
    uppercase: bool = True
    translate_to: str = Field("", max_length=12)
    bilingual: bool = True

    silence_removal: bool = True
    remove_fillers: bool = True
    pause_threshold: float = Field(0.4, ge=0.2, le=1.5)

    zoom_cuts: bool = True
    zoom_interval: float = Field(2.5, ge=2.0, le=8.0)
    zoom_amount: float = Field(0.15, ge=0.0, le=0.30)
    color_grade: ColorGrade = "punch"

    reframe: ReframeMode = "auto"
    broll: bool = False
    broll_max: int = Field(2, ge=1, le=4)
    key_point: bool = True
    outro_enabled: bool = True
    outro_text: str = Field("Follow for part 2", max_length=60)
    loop_ending: bool = False
    ab_variants: bool = False

    denoise: bool = True
    loudnorm: bool = True
    music_file: str = Field("", max_length=200)
    music_volume: float = Field(0.14, ge=0.0, le=1.0)

    @field_validator("caption_color", "highlight_color")
    @classmethod
    def _hex(cls, value: str) -> str:
        return _clean_hex(value)

    @field_validator("translate_to")
    @classmethod
    def _lang(cls, value: str) -> str:
        value = (value or "").strip()
        if value and not re.match(r"^[a-zA-Z]{2,3}(-[a-zA-Z]{2,4})?$", value):
            raise ValueError("Use a language code like en, th, es or pt-BR")
        return value

    @field_validator("music_file")
    @classmethod
    def _music(cls, value: str) -> str:
        value = (value or "").strip()
        if value and (("/" in value) or ("\\" in value) or ".." in value):
            raise ValueError("Invalid music file reference")
        return value

    @field_validator("outro_text")
    @classmethod
    def _outro(cls, value: str) -> str:
        return (value or "").strip()

    @model_validator(mode="after")
    def _lengths(self) -> "ClipSettings":
        if self.max_length < self.min_length:
            raise ValueError("max_length must be greater than or equal to min_length")
        if self.max_length - self.min_length < 4:
            raise ValueError("Give the selector at least a 4 second length range")
        return self

    @property
    def width(self) -> int:
        return ASPECT_SIZES[self.aspect][0]

    @property
    def height(self) -> int:
        return ASPECT_SIZES[self.aspect][1]


class AnalyzeRequest(BaseModel):
    urls: list[str] | str
    settings: ClipSettings = Field(default_factory=ClipSettings)

    @field_validator("urls", mode="before")
    @classmethod
    def _split(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        return value

    @model_validator(mode="after")
    def _validate(self) -> "AnalyzeRequest":
        cleaned: list[str] = []
        for raw in self.urls:
            url = raw.strip()
            if not url:
                continue
            parsed = urlparse(url if "://" in url else f"https://{url}")
            host = (parsed.hostname or "").lower().removeprefix("www.")
            if host not in {"youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}:
                raise ValueError(f"Not a YouTube link: {url}")
            if host == "youtube.com" and not (
                parsed.path.startswith("/watch")
                or parsed.path.startswith("/shorts")
                or parsed.path.startswith("/live")
            ):
                raise ValueError(f"Unsupported YouTube link: {url}")
            if host == "youtu.be" and len(parsed.path.strip("/")) < 5:
                raise ValueError(f"Missing video id: {url}")
            if host == "youtube.com" and parsed.path.startswith("/watch"):
                if not parse_qs(parsed.query).get("v"):
                    raise ValueError(f"Missing video id: {url}")
            if url not in cleaned:
                cleaned.append(url)
        if not cleaned:
            raise ValueError("Paste at least one YouTube URL")
        self.urls = cleaned
        return self


class RenderRequest(BaseModel):
    clip_ids: list[str] = Field(min_length=1, max_length=24)
    settings: ClipSettings | None = None
    ab_variants: bool | None = None


class CaptionLine(BaseModel):
    id: str
    text: str = Field(max_length=240)


class CaptionEditRequest(BaseModel):
    lines: list[CaptionLine]
    settings: ClipSettings | None = None


class CaptionTranslateRequest(BaseModel):
    language: str = Field(min_length=2, max_length=12)
    settings: ClipSettings | None = None


class UploadRequest(BaseModel):
    clip_id: str
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=4800)
    privacy: Literal["public", "unlisted", "private"] = "unlisted"
    publish_at: str = Field(default="", max_length=40)
    rights_confirmed: bool = False

    @field_validator("publish_at")
    @classmethod
    def _when(cls, value: str) -> str:
        value = (value or "").strip()
        if value:
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Use a valid date/time, e.g. 2026-12-01T18:00") from exc
        return value

    @field_validator("description")
    @classmethod
    def _shorts(cls, value: str) -> str:
        value = value.strip()
        if "#shorts" not in value.lower():
            value = (value + "\n\n#Shorts").strip()
        return value


class ZipRequest(BaseModel):
    clip_ids: list[str] | None = None
