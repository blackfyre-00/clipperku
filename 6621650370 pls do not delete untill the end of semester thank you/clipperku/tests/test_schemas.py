from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import AnalyzeRequest, ClipSettings, UploadRequest


def test_settings_defaults_match_the_plan() -> None:
    settings = ClipSettings()
    assert settings.clip_count == 5
    assert (settings.min_length, settings.max_length) == (20, 60)
    assert settings.aspect == "9:16"
    assert settings.captions_enabled is True
    assert settings.caption_preset == "graphic"
    assert settings.caption_size == 82
    assert settings.uppercase is True
    assert settings.caption_position == 24
    assert settings.silence_removal is True
    assert settings.remove_fillers is True
    assert settings.pause_threshold == 0.4
    assert settings.zoom_cuts is True
    assert settings.zoom_interval == 2.5
    assert settings.zoom_amount == 0.15
    assert settings.color_grade == "punch"
    assert settings.reframe == "auto"
    assert settings.broll is False
    assert settings.loop_ending is False
    assert settings.ab_variants is False
    assert settings.denoise is True
    assert settings.loudnorm is True
    assert (settings.width, settings.height) == (1080, 1920)


def test_hex_colors_are_normalised() -> None:
    settings = ClipSettings(caption_color="ff00aa", highlight_color="#00ff00")
    assert settings.caption_color == "#ff00aa"
    assert settings.highlight_color == "#00ff00"
    with pytest.raises(ValidationError):
        ClipSettings(caption_color="red")


def test_length_range_is_validated() -> None:
    with pytest.raises(ValidationError):
        ClipSettings(min_length=50, max_length=40)
    with pytest.raises(ValidationError):
        ClipSettings(min_length=20, max_length=22)


def test_analyze_request_splits_and_validates_urls() -> None:
    request = AnalyzeRequest(
        urls="https://www.youtube.com/watch?v=abc123\nhttps://youtu.be/def456\n\n",
    )
    assert request.urls == ["https://www.youtube.com/watch?v=abc123", "https://youtu.be/def456"]
    with pytest.raises(ValidationError):
        AnalyzeRequest(urls="https://vimeo.com/12345")
    with pytest.raises(ValidationError):
        AnalyzeRequest(urls="https://www.youtube.com/watch")
    with pytest.raises(ValidationError):
        AnalyzeRequest(urls="")


def test_upload_request_requires_rights_and_validates_schedule() -> None:
    upload = UploadRequest(clip_id="clip_1", title="A hook")
    assert upload.rights_confirmed is False
    assert upload.publish_at == ""
    scheduled = UploadRequest(
        clip_id="clip_1", title="A hook", publish_at="2026-12-01T18:00", rights_confirmed=True
    )
    assert scheduled.publish_at == "2026-12-01T18:00"
    with pytest.raises(ValidationError):
        UploadRequest(clip_id="clip_1", title="A hook", publish_at="tomorrow")


def test_upload_request_adds_shorts_tag() -> None:
    upload = UploadRequest(clip_id="clip_1", title="A hook", description="Great clip")
    assert "#Shorts" in upload.description
    already = UploadRequest(clip_id="clip_1", title="A hook", description="Already #shorts")
    assert already.description.lower().count("#shorts") == 1
