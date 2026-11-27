from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from services import align, media


def test_speech_spans_from_silencedetect(monkeypatch) -> None:
    monkeypatch.setattr(media, "probe", lambda path: {"duration": 10.0})
    stderr = (
        "[silencedetect] silence_start: 1.000\n"
        "[silencedetect] silence_end: 2.000 | silence_duration: 1.000\n"
        "[silencedetect] silence_start: 5.000\n"
        "[silencedetect] silence_end: 6.000 | silence_duration: 1.000\n"
    )
    monkeypatch.setattr(media, "run", lambda cmd, **kwargs: SimpleNamespace(stderr=stderr))
    assert align.speech_spans(Path("x.mp4")) == [(0.0, 1.0), (2.0, 5.0), (6.0, 10.0)]


def test_align_preserves_text_and_is_monotonic(monkeypatch) -> None:
    monkeypatch.setattr(media, "probe", lambda path: {"duration": 10.0})
    monkeypatch.setattr(media, "run", lambda cmd, **kwargs: SimpleNamespace(stderr=""))
    beats = [
        {"id": f"b{i}", "text": word, "start": i * 0.6, "end": i * 0.6 + 0.5,
         "words": [{"text": word, "start": i * 0.6, "end": i * 0.6 + 0.5}]}
        for i, word in enumerate(["One", "Two", "Three", "Four"])
    ]
    aligned, notes = align.align_beats_to_audio(Path("x.mp4"), beats)
    assert notes == []
    assert [beat["text"] for beat in aligned] == ["One", "Two", "Three", "Four"]
    starts = [beat["start"] for beat in aligned]
    assert starts == sorted(starts)
    for beat in aligned:
        assert beat["end"] > beat["start"]
        assert beat["words"][0]["start"] >= beat["start"] - 0.001
