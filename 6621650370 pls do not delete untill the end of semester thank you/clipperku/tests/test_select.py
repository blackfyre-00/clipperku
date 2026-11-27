from __future__ import annotations

from app.schemas import ClipSettings
from services import select


def transcript_from(lines: list[str], step: float = 0.3) -> dict:
    words = []
    segments = []
    t = 0.5
    for line in lines:
        seg_words = []
        for token in line.split():
            item = {"start": round(t, 3), "end": round(t + step, 3), "word": token, "probability": 0.9}
            seg_words.append(item)
            words.append(item)
            t += step + 0.04
        segments.append({"start": seg_words[0]["start"], "end": seg_words[-1]["end"], "text": line, "words": seg_words})
        t += 0.4
    return {"language": "en", "duration": t + 1.0, "segments": segments, "words": words}


def test_heuristic_select_respects_lengths_and_overlap() -> None:
    lines = [
        "Hey guys welcome back to the channel.",
        "Why does nobody talk about this simple editing mistake?",
        "It costs you the first three seconds and the whole view.",
        "Here is the fix that changed my retention overnight.",
        "Stop adding a long pause before the first sentence.",
        "Cut straight to the promise and front load the payoff.",
        "The result is a clip that feels intentional from frame one.",
        "Follow for part two where I break down the workflow.",
    ]
    transcript = transcript_from(lines)
    settings = ClipSettings(clip_count=3, min_length=8, max_length=18)
    clips = select.heuristic_select(transcript, settings)
    assert 1 <= len(clips) <= 3
    for clip in clips:
        assert settings.min_length - 0.6 <= clip["duration"] <= settings.max_length + 0.6
        assert set(clip["scores"]) == {"hook", "clarity", "emotion", "payoff"}
        assert 0 <= clip["score"] <= 40
        assert clip["hook_text"]
        assert clip["id"].startswith("clip_")
    for i, a in enumerate(clips):
        for b in clips[i + 1 :]:
            assert select._overlap(a, b) <= 0.35


def test_normalize_clip_snaps_to_sentence_and_strips_greeting() -> None:
    transcript = transcript_from(
        [
            "Hey guys welcome back.",
            "Why does this happen every single time?",
            "Because nobody checks the first frame.",
            "Here is the simple fix that works.",
        ]
    )
    settings = ClipSettings(clip_count=1, min_length=8, max_length=14)
    raw = {
        "start": 0.0,
        "end": 99.0,
        "hook_text": "Why does this happen every single time?",
        "hook_type": "question",
        "scores": {"hook": 9, "standalone_clarity": 8, "emotional_intensity": 7, "payoff": 9},
        "reason": "Strong question hook.",
        "key_point": "Check the first frame",
        "broll_terms": ["frame"],
    }
    clip = select._normalize_clip(raw, transcript, settings)
    assert clip is not None
    # The greeting sentence must be skipped.
    greeting_end = transcript["segments"][0]["end"]
    assert clip["start"] >= greeting_end - 0.01
    assert clip["score"] == 33
    assert clip["hook_type"] == "question"
    assert clip["broll_terms"] == ["frame"]


def test_long_clip_warning() -> None:
    settings = ClipSettings()
    clip = {"duration": 72.0, "start": 0, "end": 72}
    warnings = select._clip_warnings(clip, settings)
    assert warnings and "60s" in warnings[0]
