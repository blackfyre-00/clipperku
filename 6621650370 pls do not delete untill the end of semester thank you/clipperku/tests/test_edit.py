from __future__ import annotations

from app.schemas import ClipSettings
from services import edit


def words(*pairs: tuple[float, float, str]) -> list[dict]:
    return [{"start": s, "end": e, "word": w, "probability": 0.9} for s, e, w in pairs]


def test_filler_detection_english_and_thai() -> None:
    assert edit._is_filler("um")
    assert edit._is_filler("เอ่อ")
    assert edit._is_filler("อ่า")
    assert not edit._is_filler("strategy")


def test_plan_keep_ranges_drops_long_pause_and_filler() -> None:
    settings = ClipSettings(silence_removal=True, remove_fillers=True, pause_threshold=0.4)
    seq = words(
        (1.0, 1.3, "why"),
        (1.3, 1.6, "does"),
        (1.6, 1.9, "this"),
        (2.9, 3.2, "um"),
        (4.2, 4.5, "work"),
        (4.5, 4.8, "every"),
        (4.8, 5.1, "time"),
        (5.1, 5.4, "today"),
    )
    ranges = edit.plan_keep_ranges(seq, 0.0, 7.0, settings)
    assert ranges
    total = sum(b - a for a, b in ranges)
    assert total < 7.0
    # The filler word's own timestamp must not be retained.
    assert not any(a <= 3.0 <= b for a, b in ranges)


def test_plan_keep_ranges_is_identity_when_disabled() -> None:
    settings = ClipSettings(silence_removal=False)
    seq = words((1.0, 1.2, "hello"), (4.0, 4.4, "world"))
    assert edit.plan_keep_ranges(seq, 0.0, 6.0, settings) == [(0.0, 6.0)]


def test_remap_words_is_clip_relative_and_monotonic() -> None:
    seq = words((10.0, 10.4, "a"), (10.4, 10.8, "b"), (20.0, 20.4, "c"))
    remapped = edit.remap_words(seq, [(10.0, 11.0), (20.0, 21.0)])
    assert remapped
    assert remapped[0]["start"] == 0.0
    starts = [w["start"] for w in remapped]
    assert starts == sorted(starts)
    assert remapped[-1]["start"] >= 1.0  # after the first range's 1s duration


def test_color_grade_filters() -> None:
    assert edit._grade_filter(ClipSettings(color_grade="none")) == ""
    assert "eq=contrast" in edit._grade_filter(ClipSettings(color_grade="punch"))
    cinematic = edit._grade_filter(ClipSettings(color_grade="cinematic"))
    assert "colorbalance" in cinematic and "vignette" in cinematic
    assert "saturation=1.30" in edit._grade_filter(ClipSettings(color_grade="neon"))


def test_crop_geometry_stays_inside_source() -> None:
    bw, bh, bx, by, xr, yr = edit._crop_geometry(1920, 1080, 1080, 1920, face_cx=0.5)
    assert bx >= 0 and by >= 0
    assert bx + bw <= 1920
    assert by + bh <= 1080
    assert abs((bw / bh) - (1080 / 1920)) < 0.01
    assert 0.0 <= xr <= 1.0
