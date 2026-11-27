from __future__ import annotations

import re

from app.schemas import ClipSettings
from services import captions


def make_words(text: str, start: float = 0.0, step: float = 0.32) -> list[dict]:
    words = []
    t = start
    for token in text.split():
        words.append({"start": round(t, 3), "end": round(t + step * 0.9, 3), "word": token, "probability": 0.9})
        t += step
    return words


def test_ass_and_srt_timestamps() -> None:
    assert captions.ass_time(0) == "0:00:00.00"
    assert captions.ass_time(61.239) == "0:01:01.24"
    assert captions.srt_time(61.239) == "00:01:01,239"


def test_beats_group_one_or_two_words() -> None:
    words = make_words("Nobody tells you this about short clips")
    beats = captions.beats_from_words(words, 0.0, 10.0, max_words=2, max_chars=40)
    assert beats
    for beat in beats:
        assert 1 <= len(beat["words"]) <= 2
        assert beat["end"] > beat["start"]
    # Timing must be monotonic.
    for a, b in zip(beats, beats[1:]):
        assert b["start"] >= a["start"]


def test_apply_edits_replaces_text_and_words() -> None:
    words = make_words("hello wrold from the studio")
    beats = captions.beats_from_words(words, 0.0, 10.0, max_words=2)
    first = beats[0]
    edited = captions.apply_edits(beats, {first["id"]: "Hello world"})
    assert edited[0]["text"] == "Hello world"
    assert [w["text"] for w in edited[0]["words"]] == ["Hello", "world"]


def test_safe_margin_never_enters_bottom_15_percent() -> None:
    settings = ClipSettings(caption_position=16)
    margin = captions.safe_margin(settings, 1920, outline=8)
    assert margin >= round(1920 * 0.16) + 16
    high = captions.safe_margin(ClipSettings(caption_position=38), 1920, outline=8)
    assert high >= round(1920 * 0.38)


def test_make_ass_contains_highlight_safe_margin_and_overlays() -> None:
    words = make_words("Why does this simple trick work so well")
    beats = captions.beats_from_words(words, 0.0, 10.0, max_words=2)
    settings = ClipSettings(
        caption_position=24,
        highlight_color="#FFE14D",
        caption_color="#FFFFFF",
        caption_preset="bold_pop",
    )
    ass = captions.make_ass(
        beats,
        settings,
        1080,
        1920,
        language="en",
        duration=12.0,
        key_point="Simple editing trick",
        outro_text="Follow for part 2",
        credit="B-roll: Pexels / Someone",
    )
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    assert "Style: Caption" in ass
    assert "Dialogue:" in ass
    assert "&H4DE1FF&" in ass  # BGR inline highlight for #FFE14D
    assert "Simple editing trick" in ass
    assert "Follow for part 2" in ass
    assert "B-roll: Pexels / Someone" in ass
    # MarginV on the Caption style must stay above the safe zone.
    match = re.search(r"Style: Caption,[^\n]*?,(\d+),1$", ass, re.MULTILINE)
    assert match, "caption style line not found"
    assert int(match.group(1)) >= round(1920 * 0.16)


def test_graphic_preset_is_uppercase_bubble_without_karaoke() -> None:
    words = make_words("Nobody tells you this about short clips")
    beats = captions.beats_from_words(words, 0.0, 10.0, max_words=2)
    settings = ClipSettings(caption_preset="graphic")
    ass = captions.make_ass(beats, settings, 1080, 1920, duration=12.0)
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue: 1,")]
    assert dialogues
    assert "\\c&H" not in ass  # no active-word colour
    assert "NOBODY TELLS" in dialogues[0]
    style = next(line for line in ass.splitlines() if line.startswith("Style: Caption,"))
    # Dark rounded pill: BorderStyle 3, box alpha 190/255 -> ASS alpha 65 (0x41).
    assert ",3," in style
    assert "&H41000000" in style


def test_cinema_preset_is_lower_left_label() -> None:
    words = make_words("Magnus Carlsen wins again")
    beats = captions.beats_from_words(words, 0.0, 10.0, max_words=2)
    ass = captions.make_ass(beats, ClipSettings(caption_preset="cinema"), 1080, 1920, duration=12.0)
    style = next(line for line in ass.splitlines() if line.startswith("Style: Caption,"))
    parts = style.split(",")
    assert parts[18] == "1"  # alignment: bottom-left
    assert int(parts[19]) > 0  # left margin pushes it in from the edge


def test_karaoke_preset_uses_bold_box_and_word_pop() -> None:
    words = make_words("Why does this simple trick work so well")
    beats = captions.beats_from_words(words, 0.0, 10.0, max_words=2)
    settings = ClipSettings(caption_preset="karaoke", caption_size=100)
    ass = captions.make_ass(beats, settings, 1080, 1920, duration=12.0)
    assert "\\fscx112" in ass  # active word grows
    assert "\\t(0,90,\\fscx108" in ass  # punchy beat entrance
    style = next(line for line in ass.splitlines() if line.startswith("Style: Caption,"))
    # 140/255 opacity -> ASS alpha 115 (0x73); 0 = opaque.
    assert "&H73000000" in style


def test_make_srt_has_timed_cues() -> None:
    words = make_words("The quick brown fox jumps over the lazy dog")
    beats = captions.beats_from_words(words, 0.0, 10.0, max_words=2)
    srt = captions.make_srt(beats)
    assert "-->" in srt
    assert re.search(r"^\d+$", srt, re.MULTILINE)
