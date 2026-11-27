"""Word-level transcription with faster-whisper (auto language, Thai + English)."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.errors import AppError, TranscriptionUnavailable

ProgressCb = Callable[[float, str], None]


def _clean_word(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _synthesize_words(start: float, end: float, text: str) -> list[dict[str, Any]]:
    tokens = [t for t in re.split(r"(\s+)", text) if t and not t.isspace()]
    if not tokens:
        return []
    span = max(0.2, end - start)
    step = span / len(tokens)
    return [
        {"start": round(start + i * step, 3), "end": round(start + (i + 1) * step, 3), "word": token, "probability": 0.5}
        for i, token in enumerate(tokens)
    ]


def _load_model():
    settings = get_settings()
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # noqa: BLE001 - ctranslate2 can raise on unsupported Python
        raise TranscriptionUnavailable(str(exc)) from exc
    compute = settings.whisper_compute_type
    if compute in {"", "auto"}:
        compute = "default"
    try:
        return WhisperModel(settings.whisper_model, device=settings.whisper_device, compute_type=compute)
    except Exception as exc:  # noqa: BLE001
        raise TranscriptionUnavailable(
            f"Could not load model '{settings.whisper_model}': {exc}"
        ) from exc


def _mock_transcript(duration: float) -> dict[str, Any]:
    lines = [
        "This is a mock transcript because TRANSCRIBE_BACKEND is set to mock.",
        "The quick brown fox jumps over the lazy dog every single morning.",
        "Why does nobody talk about this simple editing trick?",
        "It saves hours of work and makes every clip feel intentional.",
        "Here is the part that actually matters for retention.",
        "Most people get this wrong, and it costs them the view.",
        "Follow for part two, where I break down the full workflow.",
    ]
    words: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    t = 0.4
    i = 0
    while t < max(5.0, duration - 1.0):
        text = lines[i % len(lines)]
        tokens = text.split(" ")
        span = min(len(tokens) * 0.32, max(1.0, duration - t))
        step = span / max(1, len(tokens))
        seg_words = []
        for j, token in enumerate(tokens):
            w = {"start": round(t + j * step, 3), "end": round(t + (j + 1) * step, 3), "word": token + " ", "probability": 0.9}
            seg_words.append(w)
            words.append(w)
        segments.append({"start": round(t, 3), "end": round(t + span, 3), "text": text, "words": seg_words})
        t += span + 0.5
        i += 1
    return {
        "language": "en",
        "language_probability": 1.0,
        "duration": duration,
        "backend": "mock",
        "segments": segments,
        "words": words,
    }


def transcribe(video_path: Path, work_dir: Path, progress: ProgressCb | None = None) -> dict[str, Any]:
    settings = get_settings()
    backend = (os.getenv("TRANSCRIBE_BACKEND") or "faster-whisper").strip().lower()
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = work_dir / "transcript.json"

    if progress:
        progress(2.0, "Loading speech model…")

    if backend == "mock":
        from services.media import probe

        duration = float(probe(video_path).get("duration") or 30.0)
        data = _mock_transcript(duration)
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        if progress:
            progress(100.0, "Transcript ready (mock)")
        return data

    # ctranslate2's native build for Python 3.14 crashes the interpreter with
    # an access violation while constructing WhisperModel (observed on
    # Windows). Fail immediately with an explanation instead of leaving the
    # job stuck at ~31% with no further updates.
    if sys.version_info >= (3, 14):
        raise TranscriptionUnavailable(
            "Speech transcription is not available on Python 3.14 with this "
            "build of ctranslate2 — loading the Whisper model crashes. "
            "Use Python 3.11 (or the included Docker image) for real "
            "transcription, or set TRANSCRIBE_BACKEND=mock in .env to test "
            "the rest of the app."
        )

    model = _load_model()
    if progress:
        progress(8.0, "Detecting speech…")

    try:
        segments_iter, info = model.transcribe(
            str(video_path),
            language=None,
            task="transcribe",
            beam_size=settings.whisper_beam_size,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise TranscriptionUnavailable(str(exc)) from exc

    duration = float(getattr(info, "duration", 0.0) or 0.0)
    language = getattr(info, "language", "") or ""
    probability = float(getattr(info, "language_probability", 0.0) or 0.0)
    segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    total = max(duration, 1.0)

    for seg in segments_iter:
        seg_words = []
        raw_words = getattr(seg, "words", None) or []
        for word in raw_words:
            text = _clean_word(getattr(word, "word", ""))
            if not text:
                continue
            start = float(getattr(word, "start", 0.0) or 0.0)
            end = float(getattr(word, "end", start) or start)
            if end <= start:
                end = start + 0.08
            item = {
                "start": round(start, 3),
                "end": round(end, 3),
                "word": text,
                "probability": round(float(getattr(word, "probability", 0.0) or 0.0), 3),
            }
            seg_words.append(item)
            words.append(item)
        if not seg_words:
            seg_words = _synthesize_words(float(seg.start), float(seg.end), seg.text or "")
            words.extend(seg_words)
        if seg_words:
            segments.append(
                {
                    "start": round(float(seg.start), 3),
                    "end": round(float(seg.end), 3),
                    "text": (seg.text or "").strip(),
                    "words": seg_words,
                }
            )
        if progress:
            pct = min(98.0, 8.0 + (float(seg.end) / total) * 90.0)
            progress(pct, f"Transcribing… {pct:0.0f}%")

    words.sort(key=lambda w: w["start"])
    if len(words) < 3 or (words[-1]["end"] - words[0]["start"]) < 1.0:
        raise AppError(
            "No speech was detected in this video.",
            code="no_speech",
            hint="Try a video with clear spoken audio, or turn off the VAD by using a different source.",
        )

    data = {
        "language": language,
        "language_probability": round(probability, 3),
        "duration": round(duration or words[-1]["end"], 3),
        "backend": "faster-whisper",
        "model": settings.whisper_model,
        "segments": segments,
        "words": words,
    }
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if progress:
        progress(100.0, f"Transcript ready ({language or 'auto'})")
    return data
