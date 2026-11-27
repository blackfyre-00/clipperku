"""Clip rendering: tighten speech, reframe, zoom, B-roll, audio and burned captions."""
from __future__ import annotations

import math
import os
import re
import statistics
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.errors import AppError
from app.schemas import ClipSettings
from services import broll, captions, media

ProgressCb = Callable[[float, str], None]

FILLER_WORDS = {
    "um", "umm", "uh", "uhh", "uhm", "er", "erm", "hmm", "mm", "mhm",
    "เอ่อ", "เออ", "อ่า", "อืม", "อ่ะ", "อืมม", "เอิ่ม",
}
SOFT_FILLERS = {"like", "you know", "i mean", "basically", "actually", "sort of", "kind of"}


def _norm_word(text: str) -> str:
    return re.sub(r"^[^\w\u0E00-\u0E7F]+|[^\w\u0E00-\u0E7F]+$", "", (text or "").lower())


def _is_filler(text: str) -> bool:
    token = _norm_word(text)
    if token in FILLER_WORDS:
        return True
    if token in SOFT_FILLERS and len(token) <= 8:
        return True
    return False


def plan_keep_ranges(
    words: list[dict[str, Any]],
    start: float,
    end: float,
    settings: ClipSettings,
) -> list[tuple[float, float]]:
    """Cut pauses > threshold and filler words. Returns source-time ranges."""
    if not settings.silence_removal:
        return [(start, end)]
    clip_words = [
        w
        for w in words
        if float(w.get("end", 0)) > start + 0.03 and float(w.get("start", 0)) < end - 0.03
    ]
    if len(clip_words) < 3:
        return [(start, end)]

    ranges: list[tuple[float, float]] = []
    cur_start: float | None = None
    cur_end = 0.0
    for idx, word in enumerate(clip_words):
        w_start = max(start, float(word["start"]))
        w_end = min(end, float(word["end"]))
        if w_end <= w_start:
            continue
        token = _norm_word(str(word.get("word", "")))
        before_gap = w_start - float(clip_words[idx - 1]["end"]) if idx > 0 else 1.0
        after_gap = float(clip_words[idx + 1]["start"]) - w_end if idx + 1 < len(clip_words) else 1.0
        isolated_like = token == "like" and (before_gap > 0.14 or after_gap > 0.14)
        if settings.remove_fillers and (_is_filler(str(word.get("word", ""))) or isolated_like):
            if cur_start is not None:
                ranges.append((cur_start, cur_end))
                cur_start = None
            continue
        if cur_start is None:
            cur_start, cur_end = w_start, w_end
            continue
        gap = w_start - cur_end
        if gap > settings.pause_threshold:
            ranges.append((cur_start, cur_end))
            cur_start, cur_end = w_start, w_end
        else:
            cur_end = max(cur_end, w_end)
    if cur_start is not None:
        ranges.append((cur_start, cur_end))

    if not ranges:
        return [(start, end)]
    merged: list[tuple[float, float]] = []
    for a, b in ranges:
        if merged and a - merged[-1][1] < 0.08:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    merged[0] = (max(start, merged[0][0] - 0.04), merged[0][1])
    merged[-1] = (merged[-1][0], min(end, merged[-1][1] + 0.07))
    total = sum(b - a for a, b in merged)
    if total < 2.0 or total < (end - start) * 0.25:
        return [(start, end)]
    return merged


def remap_words(
    words: list[dict[str, Any]],
    ranges: list[tuple[float, float]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0.0
    for a, b in ranges:
        for word in words:
            w_start = float(word.get("start", 0.0))
            w_end = float(word.get("end", w_start))
            if w_end <= a + 0.01 or w_start >= b - 0.01:
                continue
            start = offset + max(0.0, w_start - a)
            end = offset + max(0.0, min(b, w_end) - a)
            if end <= start:
                end = start + 0.06
            out.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "word": word.get("word", ""),
                    "probability": word.get("probability", 0),
                }
            )
        offset += max(0.0, b - a)
    return out


# ---------------------------------------------------------------- audio / tighten


def _audio_chain(denoise: bool, loudnorm: bool, music: Path | None, music_volume: float) -> tuple[str, list[str], int]:
    """Returns (filter fragment ending in [aout], extra inputs, music index)."""
    parts: list[str] = []
    if music:
        parts.append(
            "[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"volume={music_volume:.3f}[music]"
        )
        speech = "[acat]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
        if denoise:
            speech += ",afftdn=nf=-25"
        parts.append(f"{speech}[speechsrc]")
        parts.append("[speechsrc]asplit=2[speech][sidechain]")
        parts.append(
            "[music][sidechain]sidechaincompress=threshold=0.03:ratio=9:attack=20:release=400[duck]"
        )
        chain = "[speech][duck]amix=inputs=2:duration=first:dropout_transition=0"
        has_filter = True
    else:
        chain = "[acat]"
        has_filter = False
        if denoise:
            chain += "afftdn=nf=-25"
            has_filter = True
    if loudnorm:
        chain += ("," if has_filter else "") + "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
    parts.append(f"{chain}[aout]")
    return ";".join(parts), [], 1 if music else 0


def tighten(
    source: Path,
    out: Path,
    ranges: list[tuple[float, float]],
    settings: ClipSettings,
    music: Path | None,
    progress: ProgressCb | None = None,
) -> float:
    n = len(ranges)
    parts: list[str] = []
    for i, (a, b) in enumerate(ranges):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
    concat_in = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{concat_in}concat=n={n}:v=1:a=1[vcat][acat]")
    audio_fragment, _, _ = _audio_chain(settings.denoise, settings.loudnorm, music, settings.music_volume)
    parts.append(audio_fragment)
    filter_complex = ";".join(parts)

    args = ["-i", str(source)]
    if music:
        args += ["-stream_loop", "-1", "-i", str(music)]
    args += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[vcat]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(out),
    ]
    if progress:
        progress(15, "Removing pauses and fillers...")
    media.ffmpeg(args, timeout=1800, context="tightening the clip")
    return sum(b - a for a, b in ranges)


# ---------------------------------------------------------------- reframe


def detect_face(video: Path) -> dict[str, Any] | None:
    try:
        import cv2  # type: ignore
    except Exception:  # noqa: BLE001 - optional dependency
        return None
    cap = None
    try:
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration = frames / fps if frames else 0.0
        if duration <= 0:
            return None
        cascade = cv2.CascadeClassifier(
            os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        )
        if cascade.empty():
            return None
        xs: list[float] = []
        widths: list[float] = []
        samples = 12
        for i in range(samples):
            t = duration * (i + 0.5) / samples
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(
                gray,
                scaleFactor=1.12,
                minNeighbors=5,
                minSize=(max(32, w // 14), max(32, h // 14)),
            )
            if len(faces) == 0:
                continue
            x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            xs.append((x + fw / 2) / w)
            widths.append(fw / w)
        if not xs:
            return None
        return {
            "cx": round(statistics.median(xs), 4),
            "face_width": round(statistics.median(widths), 4),
            "samples": len(xs),
        }
    except Exception:  # noqa: BLE001
        return None
    finally:
        if cap is not None:
            cap.release()


def _crop_geometry(sw: int, sh: int, tw: int, th: int, face_cx: float | None) -> tuple[int, int, int, int, float, float]:
    target_ar = tw / th
    source_ar = sw / sh if sh else target_ar
    if source_ar > target_ar:
        base_w, base_h = round(sh * target_ar), sh
    else:
        base_w, base_h = sw, round(sw / target_ar)
    base_w = max(2, min(sw, base_w))
    base_h = max(2, min(sh, base_h))
    if face_cx is None:
        xr = 0.5
    else:
        xr = (face_cx * sw - base_w / 2) / max(1, sw - base_w)
        xr = max(0.0, min(1.0, xr))
    yr = 0.42 if face_cx is not None else 0.5
    base_x = round((sw - base_w) * xr)
    base_y = round((sh - base_h) * yr)
    return base_w, base_h, base_x, base_y, xr, yr


def _reframe_chain(sw: int, sh: int, tw: int, th: int, face_cx: float | None, settings: ClipSettings, duration: float) -> str:
    if settings.reframe == "blur":
        return (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},"
            f"boxblur=42:2,eq=brightness=-0.07[bg2];"
            f"[fg]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg2];"
            f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30[base]"
        )
    base_w, base_h, base_x, base_y, xr, yr = _crop_geometry(sw, sh, tw, th, face_cx)
    if settings.zoom_cuts and settings.zoom_amount > 0:
        frames = max(1, round(settings.zoom_interval * 30))
        # Punch in quickly, hold the zoom, then snap back on the beat. This
        # reads as a deliberate jump-cut rather than a slow continuous creep.
        ramp = max(1, round(frames * 0.4))
        z = f"1+{settings.zoom_amount:.4f}*min(1,mod(on,{frames})/{ramp})"
        return (
            f"[0:v]crop={base_w}:{base_h}:{base_x}:{base_y},"
            f"scale={tw}:{th}:flags=lanczos,setsar=1,"
            f"zoompan=z='{z}':x='(iw-iw/zoom)*{xr:.4f}':y='(ih-ih/zoom)*{yr:.4f}'"
            f":d=1:s={tw}x{th}:fps=30[base]"
        )
    return (
        f"[0:v]crop={base_w}:{base_h}:{base_x}:{base_y},"
        f"scale={tw}:{th}:flags=lanczos,setsar=1,fps=30[base]"
    )


# ---------------------------------------------------------------- B-roll


def _kenburns(image: Path, out: Path, tw: int, th: int, seconds: float) -> None:
    media.ffmpeg(
        [
            "-loop",
            "1",
            "-t",
            f"{seconds:.2f}",
            "-i",
            str(image),
            "-vf",
            f"scale={tw * 2}:{th * 2}:force_original_aspect_ratio=increase,"
            f"crop={tw * 2}:{th * 2},"
            f"zoompan=z='min(zoom+0.0012,1.16)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={tw}x{th}:fps=30,setsar=1",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(out),
        ],
        timeout=300,
        context="animating a still image",
    )


def _broll_placements(
    assets: list[dict[str, Any]],
    words: list[dict[str, Any]],
    duration: float,
    settings: ClipSettings,
) -> list[tuple[dict[str, Any], float]]:
    placements: list[tuple[dict[str, Any], float]] = []
    used: list[float] = []
    for asset in assets[: settings.broll_max]:
        term = (asset.get("term") or "").lower()
        match = next(
            (
                w
                for w in words
                if term and term in str(w.get("word", "")).lower()
                and 1.0 <= float(w.get("start", 0)) <= max(1.0, duration - 3.0)
            ),
            None,
        )
        if match:
            start = float(match["start"])
        else:
            start = duration * (0.24 + 0.28 * len(placements))
        start = max(0.8, min(start, max(0.8, duration - 2.7)))
        if any(abs(start - other) < 2.2 for other in used):
            start = min(max(0.8, duration - 2.7), start + 2.4)
        if any(abs(start - other) < 1.2 for other in used):
            continue
        used.append(start)
        placements.append((asset, round(start, 2)))
    return placements


def _grade_filter(settings: ClipSettings) -> str:
    """Lightweight look grades that match the reference shorts."""
    grade = getattr(settings, "color_grade", "none")
    if grade == "punch":
        return "eq=contrast=1.08:saturation=1.18:brightness=0.01"
    if grade == "cinematic":
        return (
            "eq=contrast=1.14:saturation=0.82,"
            "colorbalance=rs=0.10:gs=0.02:bs=-0.08,"
            "vignette=PI/5"
        )
    if grade == "neon":
        return (
            "eq=contrast=1.10:saturation=1.30,"
            "colorbalance=rs=-0.06:bs=0.12:bm=0.05,"
            "vignette=PI/6"
        )
    return ""


def _visual_pass(
    tight: Path,
    out: Path,
    info: dict[str, Any],
    face: dict[str, Any] | None,
    settings: ClipSettings,
    duration: float,
    assets: list[dict[str, Any]],
    words: list[dict[str, Any]],
    temp_dir: Path,
    progress: ProgressCb | None,
) -> tuple[list[tuple[dict[str, Any], float]], list[str]]:
    tw, th = settings.width, settings.height
    sw, sh = int(info.get("width") or tw), int(info.get("height") or th)
    warnings: list[str] = []
    face_cx = None
    if settings.reframe in {"auto", "face"}:
        if face:
            face_cx = float(face["cx"])
        elif settings.reframe == "face":
            warnings.append("No face detected — used a center crop.")
    if settings.reframe == "blur":
        face_cx = None

    parts = [_reframe_chain(sw, sh, tw, th, face_cx, settings, duration)]
    current = "[base]"
    placements = _broll_placements(assets, words, duration, settings)
    inputs: list[str] = []
    input_index = 1
    for label, (asset, start) in enumerate(placements, start=1):
        path = Path(asset["path"])
        if asset.get("kind") == "photo":
            still = temp_dir / f"broll_{label}.mp4"
            try:
                _kenburns(path, still, tw, th, 2.6)
                path = still
            except AppError:
                warnings.append("A still B-roll image could not be animated; skipped.")
                continue
        try:
            if not media.probe(path).get("has_video"):
                continue
        except AppError:
            continue
        inputs += ["-stream_loop", "-1", "-i", str(path)]
        end = start + 2.6
        parts.append(
            f"[{input_index}:v]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},"
            f"setsar=1,fps=30,trim=duration=2.6,setpts=PTS-STARTPTS+{start:.3f}/TB,"
            f"format=yuva420p,fade=t=in:st={start:.3f}:d=0.22:alpha=1,"
            f"fade=t=out:st={end - 0.24:.3f}:d=0.24:alpha=1[b{label}]"
        )
        parts.append(
            f"{current}[b{label}]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'"
            f":eof_action=pass[v{label}]"
        )
        current = f"[v{label}]"
        input_index += 1

    grade = _grade_filter(settings)
    if grade:
        parts.append(f"{current}{grade}[vgraded]")
        current = "[vgraded]"

    args = ["-i", str(tight), *inputs, "-filter_complex", ";".join(parts), "-map", current]
    if info.get("has_audio"):
        args += ["-map", "0:a:0", "-c:a", "copy"]
    args += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-movflags",
        "+faststart",
        str(out),
    ]
    if progress:
        progress(48, "Reframing and adding B-roll...")
    media.ffmpeg(args, timeout=1800, context="reframing the clip")
    return placements, warnings


# ---------------------------------------------------------------- captions / output


def _burn(
    visual: Path,
    out: Path,
    ass_path: Path,
    has_audio: bool,
    progress: ProgressCb | None = None,
) -> None:
    args = [
        "-i",
        str(visual),
        "-vf",
        media.subtitles_filter(ass_path),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-movflags",
        "+faststart",
    ]
    if has_audio:
        args += ["-c:a", "copy"]
    else:
        args += ["-an"]
    args.append(str(out))
    if progress:
        progress(78, "Burning animated captions...")
    media.ffmpeg(args, timeout=1800, context="burning captions")


def loop_ending(final: Path, out: Path, seconds: float = 1.2) -> None:
    seconds = max(0.4, seconds)
    media.ffmpeg(
        [
            "-i",
            str(final),
            "-filter_complex",
            (
                f"[0:v]trim=start=0:end={seconds:.2f},setpts=PTS-STARTPTS[vl];"
                f"[0:a]atrim=start=0:end={seconds:.2f},asetpts=PTS-STARTPTS[al];"
                f"[0:v][0:a][vl][al]concat=n=2:v=1:a=1[v][a]"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(out),
        ],
        timeout=600,
        context="looping the ending",
    )


def _write_caption_files(
    out_dir: Path,
    beats: list[dict[str, Any]],
    settings: ClipSettings,
    *,
    language: str,
    duration: float,
    key_point: str,
    outro: str,
    credit: str,
    translated: dict[str, str] | None,
) -> dict[str, str]:
    files: dict[str, str] = {}
    ass_path = out_dir / "captions.ass"
    srt_path = out_dir / "captions.srt"
    ass_path.write_text(
        captions.make_ass(
            beats,
            settings,
            settings.width,
            settings.height,
            language=language,
            duration=duration,
            key_point=key_point,
            outro_text=outro,
            credit=credit,
            translated=translated,
        ),
        encoding="utf-8",
    )
    srt_path.write_text(captions.make_srt(beats), encoding="utf-8")
    files["ass"] = str(ass_path)
    files["srt"] = str(srt_path)

    if translated:
        tr_ass = out_dir / "captions_translated.ass"
        tr_srt = out_dir / "captions_translated.srt"
        tr_beats = [
            {**beat, "text": translated.get(beat["id"], beat.get("text", ""))}
            for beat in beats
        ]
        tr_ass.write_text(
            captions.make_ass(
                tr_beats,
                settings,
                settings.width,
                settings.height,
                language=settings.translate_to or language,
                duration=duration,
                key_point=key_point,
                outro_text=outro,
                credit=credit,
            ),
            encoding="utf-8",
        )
        tr_srt.write_text(captions.make_srt(tr_beats), encoding="utf-8")
        files["translated_ass"] = str(tr_ass)
        files["translated_srt"] = str(tr_srt)
    return files


def render_clip(
    source: Path,
    words: list[dict[str, Any]],
    clip: dict[str, Any],
    settings: ClipSettings,
    out_dir: Path,
    *,
    language: str = "",
    music: Path | None = None,
    progress: ProgressCb | None = None,
    suffix: str = "",
    edits: dict[str, str] | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = out_dir / "tmp"
    temp_dir.mkdir(exist_ok=True)
    start, end = float(clip["start"]), float(clip["end"])
    warnings: list[str] = []
    credits: list[dict[str, Any]] = []

    info = media.probe(source)
    if not info.get("has_video"):
        raise AppError("The downloaded file has no video stream.", code="no_video")
    if not info.get("has_audio"):
        raise AppError(
            "The downloaded file has no audio stream.",
            code="no_audio",
            hint="Transcription and captions need audio.",
        )

    ranges = plan_keep_ranges(words, start, end, settings)
    tight = temp_dir / "tight.mp4"
    duration = tighten(source, tight, ranges, settings, music, progress)
    if duration < 1.5:
        raise AppError("This clip is too short after trimming.", code="clip_too_short")

    remapped = remap_words(words, ranges)
    beats = captions.beats_from_words(
        remapped,
        0.0,
        duration,
        max_words=settings.caption_max_words,
        max_chars=30 if language.startswith("th") else 26,
    )
    if not beats:
        warnings.append("No caption words landed inside this clip.")
    elif edits:
        beats = captions.apply_edits(beats, edits)

    face = None
    if settings.reframe in {"auto", "face"}:
        face = detect_face(tight)
    if progress:
        progress(35, "Detecting the speaker...")

    assets: list[dict[str, Any]] = []
    if settings.broll and clip.get("broll_terms"):
        if progress:
            progress(40, "Finding B-roll...")
        assets, broll_warnings = broll.fetch_assets(
            [str(t) for t in clip["broll_terms"]], settings.aspect, settings.broll_max
        )
        warnings.extend(broll_warnings)
        credits = assets

    visual = out_dir / f"visual{suffix}.mp4"
    placements, visual_warnings = _visual_pass(
        tight, visual, info, face, settings, duration, assets, remapped, temp_dir, progress
    )
    warnings.extend(visual_warnings)
    if len(placements) < len(assets):
        assets = [asset for asset, _ in placements]
        credits = assets

    translated: dict[str, str] | None = None
    if settings.translate_to and beats:
        try:
            translated = captions.translate_beats(beats, settings.translate_to)
        except AppError as exc:
            warnings.append(f"Caption translation skipped: {exc.message}")

    key_point = clip.get("key_point", "") if settings.key_point else ""
    outro = settings.outro_text if settings.outro_enabled and duration > 5 else ""
    credit_text = broll.credits_text(credits)
    files = _write_caption_files(
        out_dir,
        beats,
        settings,
        language=language,
        duration=duration,
        key_point=key_point,
        outro=outro,
        credit=credit_text,
        translated=translated,
    )
    broll.write_credits(credits, out_dir / "credits.txt")
    files["credits"] = str(out_dir / "credits.txt")

    final = out_dir / f"final{suffix}.mp4"
    has_overlays = bool(settings.captions_enabled or key_point or outro or credit_text)
    if has_overlays:
        _burn(visual, final, Path(files["ass"]), bool(info.get("has_audio")), progress)
    else:
        final.write_bytes(visual.read_bytes())

    if settings.loop_ending and duration > 2.0:
        looped = out_dir / f"final_loop{suffix}.mp4"
        loop_ending(final, looped)
        looped.replace(final)
        duration += 1.2

    thumb = out_dir / f"thumb{suffix}.jpg"
    media.thumbnail(final, thumb, at=min(1.0, duration / 2))

    if progress:
        progress(100, "Clip ready")
    if tight.exists():
        tight.unlink(missing_ok=True)
    for leftover in temp_dir.glob("broll_*.mp4"):
        leftover.unlink(missing_ok=True)
    try:
        temp_dir.rmdir()
    except OSError:
        pass

    return {
        "beats": beats,
        "translated": translated or {},
        "duration": round(duration, 2),
        "face": face,
        "credits": credits,
        "warnings": warnings,
        "files": {
            "video": str(final),
            "visual": str(visual),
            "thumb": str(thumb),
            **files,
        },
    }


def burn_captions_only(
    visual: Path,
    beats: list[dict[str, Any]],
    settings: ClipSettings,
    out_dir: Path,
    *,
    language: str,
    duration: float,
    key_point: str,
    translated: dict[str, str] | None,
    credit: str,
    progress: ProgressCb | None = None,
) -> dict[str, Any]:
    info = media.probe(visual)
    outro = settings.outro_text if settings.outro_enabled and duration > 5 else ""
    files = _write_caption_files(
        out_dir,
        beats,
        settings,
        language=language,
        duration=duration,
        key_point=key_point if settings.key_point else "",
        outro=outro,
        credit=credit,
        translated=translated,
    )
    final = out_dir / "final.mp4"
    has_overlays = bool(settings.captions_enabled or (settings.key_point and key_point) or outro or credit)
    if has_overlays:
        _burn(visual, final, Path(files["ass"]), bool(info.get("has_audio")), progress)
    else:
        final.write_bytes(visual.read_bytes())
    if settings.loop_ending and duration > 2.0:
        looped = out_dir / "final_loop.mp4"
        loop_ending(final, looped)
        looped.replace(final)
    media.thumbnail(final, out_dir / "thumb.jpg", at=min(1.0, duration / 2))
    files["video"] = str(final)
    files["visual"] = str(visual)
    files["thumb"] = str(out_dir / "thumb.jpg")
    return files
