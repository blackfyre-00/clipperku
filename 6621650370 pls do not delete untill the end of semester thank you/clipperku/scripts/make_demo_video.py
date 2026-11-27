"""Create a synthetic source video + word-level transcript for offline testing.

Usage:
    python scripts/make_demo_video.py [--out data/demo] [--duration 40]

Requires ffmpeg on PATH (or FFMPEG_BIN). No network access is used.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import media  # noqa: E402

LINES = [
    "Nobody tells you this about editing short videos.",
    "The first three seconds decide whether anyone watches.",
    "Why does a simple zoom keep people in the frame?",
    "Because the brain notices motion before it notices words.",
    "Here is the part that actually matters for retention.",
    "Most people leave a long pause at the start of the clip.",
    "That pause tells the viewer nothing is about to happen.",
    "Cut it, tighten the sentence, and front load the payoff.",
    "The result is a clip that feels intentional from frame one.",
    "Follow for part two where I break down the full workflow.",
]


def build_transcript(duration: float, word_seconds: float = 0.34) -> dict:
    words: list[dict] = []
    segments: list[dict] = []
    t = 0.6
    index = 0
    while t < duration - 1.0:
        text = LINES[index % len(LINES)]
        tokens = text.split()
        seg_words = []
        for token in tokens:
            if t >= duration - 0.5:
                break
            end = min(duration - 0.2, t + word_seconds)
            item = {
                "start": round(t, 3),
                "end": round(end, 3),
                "word": token + " ",
                "probability": 0.92,
            }
            seg_words.append(item)
            words.append(item)
            t = end + 0.06
        if seg_words:
            segments.append(
                {
                    "start": seg_words[0]["start"],
                    "end": seg_words[-1]["end"],
                    "text": text,
                    "words": seg_words,
                }
            )
        t += 0.55  # a deliberate pause so silence removal has something to do
        index += 1
    return {
        "language": "en",
        "language_probability": 1.0,
        "duration": round(duration, 3),
        "backend": "synthetic",
        "segments": segments,
        "words": words,
    }


def make_demo(out_dir: Path, duration: float = 40.0) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / "demo_source.mp4"
    transcript_path = out_dir / "transcript.json"
    media.ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=1280x720:rate=30:duration={duration:.2f}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=48000:duration={duration:.2f}",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(video),
        ],
        timeout=300,
        context="creating the demo video",
    )
    transcript = build_transcript(media.probe(video).get("duration") or duration)
    transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=1), encoding="utf-8")
    return video, transcript_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "data" / "demo"), help="output directory")
    parser.add_argument("--duration", type=float, default=40.0, help="seconds of synthetic video")
    args = parser.parse_args()
    video, transcript = make_demo(Path(args.out), args.duration)
    print(f"video      {video}")
    print(f"transcript {transcript}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
