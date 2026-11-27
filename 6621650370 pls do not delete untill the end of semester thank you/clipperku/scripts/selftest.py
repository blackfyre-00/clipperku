"""Offline end-to-end self test: synthetic video -> real rendered short.

This exercises ffmpeg tightening, reframing, caption burn, SRT/ASS output and
thumbnail creation without any network access or API keys.

Usage:
    python scripts/selftest.py [--keep]

Exit code 0 means the whole render pipeline produced a valid 9:16 MP4.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # Windows consoles are often cp874/cp1252; never crash on a status glyph.
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except (AttributeError, OSError):
    pass

from app.config import ensure_dirs, get_settings  # noqa: E402
from app.schemas import ClipSettings  # noqa: E402
from services import edit, media  # noqa: E402
from scripts.make_demo_video import make_demo  # noqa: E402


def _fail(message: str) -> int:
    print(f"FAIL  {message}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="keep the generated files")
    parser.add_argument("--duration", type=float, default=40.0)
    args = parser.parse_args()

    ensure_dirs()
    settings = get_settings()
    work = settings.data_dir / "selftest"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    print("1/6  checking ffmpeg...")
    print(f"     ffmpeg  {media.ffmpeg_path()}")
    print(f"     ffprobe {media.ffprobe_path()}")

    print("2/6  building synthetic source...")
    started = time.time()
    video, transcript_path = make_demo(work, args.duration)
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    info = media.probe(video)
    print(f"     {info['width']}x{info['height']} | {info['duration']:.1f}s | {len(transcript['words'])} words")

    print("3/6  rendering a 12s vertical clip with burned captions...")
    clip_settings = ClipSettings(
        aspect="9:16",
        captions_enabled=True,
        caption_preset="graphic",
        caption_size=82,
        color_grade="cinematic",
        caption_position=24,
        caption_max_words=2,
        silence_removal=True,
        remove_fillers=True,
        zoom_cuts=True,
        reframe="auto",
        broll=False,
        key_point=True,
        outro_enabled=True,
        outro_text="Follow for part 2",
        loop_ending=False,
        ab_variants=False,
        denoise=True,
        loudnorm=True,
        translate_to="",
    )
    clip = {
        "start": 2.0,
        "end": 14.0,
        "hook_text": "Nobody tells you this",
        "key_point": "Hook first, trim the pause",
        "broll_terms": [],
    }
    out_dir = work / "render"
    result = edit.render_clip(
        video,
        transcript["words"],
        clip,
        clip_settings,
        out_dir,
        language=transcript.get("language", "en"),
        music=None,
        progress=lambda pct, msg: print(f"     {pct:5.1f}%  {msg}"),
    )

    print("4/6  validating the output...")
    final = Path(result["files"]["video"])
    if not final.exists():
        return _fail("no final.mp4 was produced")
    size_mb = final.stat().st_size / (1024 * 1024)
    if size_mb < 0.05:
        return _fail(f"final.mp4 is suspiciously small ({size_mb:.2f} MB)")
    out_info = media.probe(final)
    checks = [
        (out_info["has_video"], "video stream present"),
        (out_info["has_audio"], "audio stream present"),
        ((out_info["width"], out_info["height"]) == (1080, 1920), f"9:16 dimensions (got {out_info['width']}x{out_info['height']})"),
        (abs(out_info["fps"] - 30.0) < 1.0, f"30 fps (got {out_info['fps']:.2f})"),
    ]
    for ok, label in checks:
        print(f"     {'ok  ' if ok else 'FAIL'} {label}")
        if not ok:
            return 1

    print("5/6  checking caption sidecars...")
    for key in ("ass", "srt", "thumb", "credits"):
        path = Path(result["files"].get(key, ""))
        if not path.exists() or path.stat().st_size == 0:
            return _fail(f"missing {key}: {path}")
        print(f"     ok   {key:8s} {path.name} ({path.stat().st_size} bytes)")
    srt = Path(result["files"]["srt"]).read_text(encoding="utf-8")
    if "-->" not in srt:
        return _fail("SRT has no timed cues")
    if len(result["beats"]) < 3:
        return _fail("too few caption beats")

    print("6/6  summary")
    elapsed = time.time() - started
    print(f"     clip duration : {result['duration']:.2f}s")
    print(f"     file size     : {size_mb:.2f} MB")
    print(f"     caption beats : {len(result['beats'])}")
    print(f"     warnings      : {result['warnings'] or 'none'}")
    print(f"     elapsed       : {elapsed:.1f}s")
    print(f"     output        : {final}")

    if not args.keep:
        print("     (pass --keep to keep the generated files)")
    print("PASS  render pipeline works end to end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
