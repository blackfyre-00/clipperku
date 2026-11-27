from __future__ import annotations

import json
import os
import shutil

import pytest

from app.config import ensure_dirs, get_settings
from app.schemas import ClipSettings
from services import edit, media
from scripts.make_demo_video import make_demo


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("RUN_RENDER_TEST") != "1", reason="set RUN_RENDER_TEST=1 to render real video")
def test_render_clip_end_to_end() -> None:
    ensure_dirs()
    work = get_settings().data_dir / "pytest_render"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    video, transcript_path = make_demo(work, duration=18.0)
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    settings = ClipSettings(
        aspect="9:16",
        caption_preset="karaoke",
        caption_max_words=2,
        zoom_cuts=True,
        reframe="center",
        broll=False,
        key_point=True,
        outro_enabled=True,
        denoise=True,
        loudnorm=True,
    )
    clip = {"start": 1.0, "end": 9.0, "hook_text": "Nobody tells you this", "key_point": "Hook first"}
    result = edit.render_clip(
        video,
        transcript["words"],
        clip,
        settings,
        work / "out",
        language="en",
        music=None,
    )

    final = result["files"]["video"]
    info = media.probe(final)
    assert info["has_video"] and info["has_audio"]
    assert (info["width"], info["height"]) == (1080, 1920)
    assert result["beats"]
    assert "-->" in open(result["files"]["srt"], encoding="utf-8").read()
    assert os.path.getsize(final) > 50_000
