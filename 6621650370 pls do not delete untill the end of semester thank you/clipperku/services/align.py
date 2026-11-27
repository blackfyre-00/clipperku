"""Re-time existing captions to the actual speech activity in the audio.

Uses ffmpeg's silencedetect on the clip's own audio track, then slides each
beat into the nearest span of real speech while keeping its text and order.
This is a fallback for clips whose captions came from a mock or a rough
transcript: it cannot change the words, but it makes them land on speech.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from services import media

_START = re.compile(r"silence_start:\s*([0-9.]+)")
_END = re.compile(r"silence_end:\s*([0-9.]+)")


def speech_spans(
    video: Path,
    *,
    noise_db: float = -35.0,
    min_silence: float = 0.25,
) -> list[tuple[float, float]]:
    """Return (start, end) seconds of non-silent audio in the clip."""
    duration = float(media.probe(video).get("duration") or 0.0)
    if duration <= 0:
        return []
    cmd = [
        media.ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-i",
        str(video),
        "-af",
        f"silencedetect=noise={noise_db:g}dB:d={min_silence:g}",
        "-f",
        "null",
        "-",
    ]
    proc = media.run(cmd, timeout=600)
    text = proc.stderr or ""
    starts = [float(value) for value in _START.findall(text)]
    ends = [float(value) for value in _END.findall(text)]
    if not starts:
        return [(0.0, duration)]

    spans: list[tuple[float, float]] = []
    cursor = 0.0
    for index, start in enumerate(starts):
        if start - cursor >= 0.12:
            spans.append((cursor, start))
        cursor = ends[index] if index < len(ends) else duration
    if duration - cursor >= 0.12:
        spans.append((cursor, duration))
    return [(round(a, 3), round(b, 3)) for a, b in spans if b - a >= 0.12]


def align_beats_to_audio(
    video: Path,
    beats: list[dict[str, Any]],
    *,
    noise_db: float = -35.0,
    min_silence: float = 0.25,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return re-timed beats (same ids/text) and any warnings."""
    if not beats:
        return [], []
    warnings: list[str] = []
    duration = float(media.probe(video).get("duration") or 0.0)
    spans = speech_spans(video, noise_db=noise_db, min_silence=min_silence)
    if not spans:
        return beats, ["No speech was detected; caption timing was left unchanged."]
    speech_total = sum(end - start for start, end in spans)
    original_total = max(0.1, float(beats[-1].get("end", 0.0)))
    scale = max(0.3, min(1.6, speech_total / original_total))

    aligned: list[dict[str, Any]] = []
    span_index = 0
    offset = 0.0
    previous_end = -1.0
    for beat in beats:
        span_start, span_end = spans[span_index]
        wanted = max(0.08, min(3.0, (float(beat.get("end", 0.0)) - float(beat.get("start", 0.0))) * scale))
        if span_end - offset < wanted + 0.02 and span_index + 1 < len(spans):
            span_index += 1
            span_start, span_end = spans[span_index]
            offset = 0.0
        start = max(previous_end + 0.01, span_start + offset)
        end = min(span_end, start + wanted)
        if end <= start:
            end = min(duration, start + 0.08)
        previous_end = end
        offset = max(0.0, end - span_start)

        old_start = float(beat.get("start", 0.0))
        old_span = max(0.01, float(beat.get("end", 0.0)) - old_start)
        new_words: list[dict[str, Any]] = []
        for word in beat.get("words") or []:
            ws = float(word.get("start", old_start))
            we = float(word.get("end", ws))
            fs = max(0.0, min(1.0, (ws - old_start) / old_span))
            fe = max(fs, min(1.0, (we - old_start) / old_span))
            new_words.append(
                {
                    **word,
                    "start": round(start + fs * (end - start), 3),
                    "end": round(start + fe * (end - start), 3),
                }
            )
        aligned.append({**beat, "start": round(start, 3), "end": round(end, 3), "words": new_words})

    if scale < 0.75:
        warnings.append("Captions were tightened to match the speech in this clip.")
    return aligned, warnings
