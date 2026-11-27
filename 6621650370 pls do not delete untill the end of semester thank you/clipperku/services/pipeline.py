"""Job orchestration: analyze (download/transcribe/select) and render."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.errors import AppError
from app.jobs import JobManager
from app.schemas import ClipSettings
from services import align, broll, captions, download, edit, media, select, transcribe


def _settings(job: dict[str, Any]) -> ClipSettings:
    return ClipSettings(**job["settings"])


def _source_dir(job_id: str, source_id: str) -> Path:
    return get_settings().work_dir / job_id / source_id


def _source_file(job_id: str, source_id: str) -> Path:
    folder = _source_dir(job_id, source_id)
    for name in ("source.mp4", "source.mkv", "source.webm", "source.mov", "source.m4v"):
        candidate = folder / name
        if candidate.exists():
            return candidate
    candidates = sorted(folder.glob("source.*"))
    if not candidates:
        raise AppError(
            "The source video is no longer on disk.",
            code="source_missing",
            hint="It may have been cleaned up. Start a new job.",
        )
    return candidates[0]


def _load_transcript(job_id: str, source_id: str) -> dict[str, Any]:
    path = _source_dir(job_id, source_id) / "transcript.json"
    if not path.exists():
        raise AppError(
            "The transcript is missing.",
            code="transcript_missing",
            hint="Start a new analysis for this video.",
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _music(settings: ClipSettings) -> Path | None:
    if not settings.music_file:
        return None
    path = get_settings().uploads_dir / Path(settings.music_file).name
    return path if path.exists() else None


def _progress_setter(
    manager: JobManager,
    job_id: str,
    source_id: str,
    index: int,
    total: int,
    base: float = 0.0,
    span: float = 100.0,
) -> Callable[[float, str], None]:
    """Map a stage-local 0-100 progress onto the job's overall percentage."""

    def setter(percent: float, message: str) -> None:
        pct = max(0.0, min(100.0, percent))
        manager.update_source(job_id, source_id, progress=round(pct, 1), stage=message)
        stage_fraction = (base + span * (pct / 100.0)) / 100.0
        overall = ((index + stage_fraction) / total) * 100.0
        manager.update(job_id, progress=round(overall, 1), stage=message)

    return setter


def analyze_job(manager: JobManager, job_id: str) -> None:
    job = manager.get(job_id)
    settings = _settings(job)
    media.ffmpeg_path()
    media.ffprobe_path()
    manager.update(job_id, status="processing", stage="Preparing…", progress=0.5, error=None)
    sources = list(job["sources"])
    total = max(1, len(sources))
    any_clips = False
    errors: list[dict[str, str]] = []
    first_selection: dict[str, Any] | None = None

    for index, source in enumerate(sources):
        source_id = source["id"]
        manager.update_source(
            job_id,
            source_id,
            status="downloading",
            stage="Starting download…",
            progress=0,
            error=None,
        )
        try:
            work_dir = _source_dir(job_id, source_id)
            meta = download.download(
                source["url"],
                work_dir,
                progress=_progress_setter(manager, job_id, source_id, index, total, base=0, span=30),
            )
            manager.update_source(
                job_id,
                source_id,
                title=meta.get("title", ""),
                duration=round(float(meta.get("duration") or 0), 2),
                status="transcribing",
                stage="Transcribing…",
                progress=0,
            )
            transcript = transcribe.transcribe(
                Path(meta["path"]),
                work_dir,
                progress=_progress_setter(manager, job_id, source_id, index, total, base=30, span=35),
            )
            manager.update_source(
                job_id,
                source_id,
                language=transcript.get("language", ""),
                word_count=len(transcript.get("words") or []),
                status="selecting",
                stage="Scoring clips…",
                progress=0,
            )
            manager.log(job_id, f"Transcribed {meta.get('title') or source['url']} ({transcript.get('language')})")

            selection = select.select_clips(
                transcript,
                settings,
                meta={
                    "title": meta.get("title", ""),
                    "duration": meta.get("duration", 0),
                    "language": transcript.get("language", ""),
                },
                log=lambda message: manager.log(job_id, message),
            )
            first_selection = first_selection or selection
            if selection.get("warning"):
                manager.warn(job_id, selection["warning"])
            for candidate in selection["clips"]:
                candidate.update(
                    {
                        "source_id": source_id,
                        "source_title": meta.get("title", ""),
                        "source_url": source["url"],
                        "language": transcript.get("language", ""),
                        "status": "candidate",
                        "progress": 0,
                        "error": None,
                        "files": {},
                        "beats": [],
                        "translated": {},
                        "caption_edits": {},
                        "credits": [],
                        "credits_text": "",
                        "variants": [],
                    }
                )
                manager.add_clip(job_id, candidate)
                any_clips = True
            manager.update_source(
                job_id,
                source_id,
                status="ready",
                stage="Ready",
                progress=100,
            )
            manager.log(job_id, f"Selected {len(selection['clips'])} clip(s) from {meta.get('title') or source['url']}")
        except AppError as exc:
            errors.append(exc.to_dict())
            manager.update_source(
                job_id,
                source_id,
                status="error",
                stage="Failed",
                error=exc.to_dict(),
            )
            manager.warn(job_id, f"{source.get('title') or source['url']}: {exc.message}")
            manager.log(job_id, f"Source failed: {exc.message}")
        except Exception as exc:  # noqa: BLE001
            payload = {
                "code": "source_failed",
                "message": "This video could not be processed.",
                "hint": f"{type(exc).__name__}: {exc}",
            }
            errors.append(payload)
            manager.update_source(job_id, source_id, status="error", stage="Failed", error=payload)
            manager.warn(job_id, f"{source.get('title') or source['url']}: {payload['message']}")

    if not any_clips:
        manager.set_error(
            job_id,
            errors[0]
            if errors
            else {
                "code": "no_clips",
                "message": "No clips were produced.",
                "hint": "Try a different video or adjust the clip length settings.",
            },
        )
        return

    selection_info = {
        "provider": (first_selection or {}).get("provider", ""),
        "model": (first_selection or {}).get("model", ""),
        "used_fallback": (first_selection or {}).get("used_fallback", False),
    }
    manager.update(
        job_id,
        status="ready",
        stage="Ready to review",
        progress=100,
        selection=selection_info,
    )
    manager.log(job_id, "Analysis complete — review the scored clips and choose what to render.")


def _next_word_start(words: list[dict[str, Any]], target: float, limit: float = 2.0) -> float | None:
    for word in words:
        start = float(word.get("start", 0.0))
        if target <= start <= target + limit:
            return start
    return None


def render_job(
    manager: JobManager,
    job_id: str,
    clip_ids: list[str],
    settings_override: ClipSettings | None,
) -> None:
    job = manager.get(job_id)
    settings = settings_override or _settings(job)
    if settings_override:
        manager.update(job_id, settings=settings.model_dump())
    manager.update(job_id, status="rendering", stage="Rendering clips…", progress=0, error=None)
    clips = [clip for clip in job["clips"] if clip["id"] in set(clip_ids)]
    if not clips:
        raise AppError("None of the selected clips exist.", code="no_clips")
    total = len(clips)
    music = _music(settings)
    if settings.music_file and not music:
        manager.warn(job_id, "The selected music file was not found; rendering without music.")
    warnings_seen: set[str] = set()
    done_count = 0

    for index, clip in enumerate(clips):
        clip_id = clip["id"]
        manager.update_clip(
            job_id,
            clip_id,
            status="rendering",
            progress=0,
            error=None,
        )

        def progress(percent: float, message: str, _clip_id: str = clip_id, _index: int = index) -> None:
            manager.update_clip(job_id, _clip_id, progress=round(percent, 1), stage=message)
            overall = ((_index + max(0.0, min(100.0, percent)) / 100.0) / total) * 100.0
            manager.update(
                job_id,
                progress=round(overall, 1),
                stage=f"Clip {_index + 1}/{total}: {message}",
            )

        try:
            source_path = _source_file(job_id, clip["source_id"])
            transcript = _load_transcript(job_id, clip["source_id"])
            words = transcript.get("words") or []
            out_dir = get_settings().outputs_dir / job_id / clip_id
            result = edit.render_clip(
                source_path,
                words,
                clip,
                settings,
                out_dir,
                language=clip.get("language", transcript.get("language", "")),
                music=music,
                progress=progress,
                edits=clip.get("caption_edits") or {},
            )
            variants: list[dict[str, Any]] = []
            if settings.ab_variants:
                for variant_index, offset in enumerate((0.9, 1.8), start=1):
                    if variant_index > 2:
                        break
                    new_start = _next_word_start(words, float(clip["start"]) + offset)
                    if new_start is None:
                        continue
                    source_duration = float(transcript.get("duration") or 0)
                    new_end = min(source_duration, new_start + (float(clip["end"]) - float(clip["start"])))
                    if new_end - new_start < max(8.0, settings.min_length - 4):
                        continue
                    variant_clip = {**clip, "start": new_start, "end": new_end}
                    try:
                        variant_result = edit.render_clip(
                            source_path,
                            words,
                            variant_clip,
                            settings,
                            out_dir / f"ab{variant_index}",
                            language=clip.get("language", ""),
                            music=music,
                            suffix=f"_ab{variant_index}",
                            edits=clip.get("caption_edits") or {},
                        )
                        variants.append(
                            {
                                "label": f"Hook {variant_index + 1}",
                                "start": round(new_start, 2),
                                "duration": variant_result["duration"],
                                "files": variant_result["files"],
                            }
                        )
                    except AppError as exc:
                        manager.warn(job_id, f"A/B variant skipped: {exc.message}")
            for message in result.get("warnings") or []:
                if message not in warnings_seen:
                    warnings_seen.add(message)
                    manager.warn(job_id, message)
            manager.update_clip(
                job_id,
                clip_id,
                status="done",
                progress=100,
                stage="Ready",
                files=result["files"],
                beats=result["beats"],
                translated=result["translated"],
                duration=result["duration"],
                rendered_duration=result["duration"],
                credits=result["credits"],
                credits_text=broll.credits_text(result["credits"]),
                face=result.get("face"),
                variants=variants,
                warnings=result.get("warnings") or [],
                error=None,
            )
            done_count += 1
            manager.log(job_id, f"Rendered clip {index + 1}/{total}")
        except AppError as exc:
            manager.update_clip(
                job_id,
                clip_id,
                status="error",
                error=exc.to_dict(),
                stage="Failed",
                progress=0,
            )
            manager.warn(job_id, f"Clip failed: {exc.message}")
        except Exception as exc:  # noqa: BLE001
            manager.update_clip(
                job_id,
                clip_id,
                status="error",
                error={
                    "code": "render_failed",
                    "message": "This clip could not be rendered.",
                    "hint": f"{type(exc).__name__}: {exc}",
                },
                stage="Failed",
            )

    if done_count == total:
        status, stage = "done", "All clips ready"
    elif done_count:
        status, stage = "partial", "Some clips failed"
    else:
        status, stage = "error", "Rendering failed"
    manager.update(job_id, status=status, stage=stage, progress=100)


def _settle_after_clip(manager: JobManager, job_id: str, stage: str) -> None:
    """Recompute job status after one clip is re-rendered in place.

    Candidate clips that were never rendered are ignored so a job that was
    already finished does not regress to "partial" just because the user only
    exported some of the scored moments.
    """
    job = manager.get(job_id)
    rendered = [clip for clip in job.get("clips") or [] if clip.get("status") in {"done", "error"}]
    done = sum(1 for clip in rendered if clip.get("status") == "done")
    status = "done" if rendered and done == len(rendered) else "partial"
    manager.update(job_id, status=status, stage=stage, progress=100)


def update_captions(
    manager: JobManager,
    job_id: str,
    clip_id: str,
    lines: dict[str, str],
    settings_override: ClipSettings | None,
) -> None:
    job = manager.get(job_id)
    clip = manager.find_clip(job_id, clip_id)
    beats = clip.get("beats") or []
    if not beats:
        raise AppError(
            "This clip has no captions yet.",
            code="no_captions",
            hint="Render the clip first, then edit its captions.",
        )
    settings = settings_override or _settings(job)
    if settings_override:
        manager.update(job_id, settings=settings.model_dump())
    visual = Path((clip.get("files") or {}).get("visual", ""))
    if not visual.exists():
        raise AppError(
            "The caption source video is missing.",
            code="visual_missing",
            hint="Re-render this clip.",
        )
    edits = dict(clip.get("caption_edits") or {})
    edits.update(lines)
    edited = captions.apply_edits(beats, edits)
    # Edited words are redistributed evenly inside their beat; snap them back
    # onto real speech activity so the new text still lands on the audio.
    try:
        edited, notes = align.align_beats_to_audio(visual, edited)
        for note in notes:
            manager.warn(job_id, note)
    except Exception:  # noqa: BLE001 - alignment is an enhancement, not a gate
        pass
    out_dir = get_settings().outputs_dir / job_id / clip_id
    manager.update(job_id, status="rendering", stage="Re-burning captions...")
    files = edit.burn_captions_only(
        visual,
        edited,
        settings,
        out_dir,
        language=clip.get("language", ""),
        duration=float(clip.get("rendered_duration") or clip.get("duration") or 0),
        key_point=clip.get("key_point", ""),
        translated=clip.get("translated") or None,
        credit=clip.get("credits_text", ""),
    )
    manager.update_clip(
        job_id,
        clip_id,
        beats=edited,
        caption_edits=edits,
        files={**(clip.get("files") or {}), **files},
        settings=settings.model_dump(),
        status="done",
        stage="Ready",
        progress=100,
    )
    _settle_after_clip(manager, job_id, "Captions updated")
    manager.log(job_id, "Captions updated and re-rendered.")


def translate_clip(
    manager: JobManager,
    job_id: str,
    clip_id: str,
    language: str,
    settings_override: ClipSettings | None,
) -> None:
    job = manager.get(job_id)
    clip = manager.find_clip(job_id, clip_id)
    beats = clip.get("beats") or []
    if not beats:
        raise AppError("Render the clip before translating captions.", code="no_captions")
    settings = settings_override or _settings(job)
    settings = settings.model_copy(update={"translate_to": language})
    manager.update(job_id, settings=settings.model_dump())
    translated = captions.translate_beats(beats, language)
    visual = Path((clip.get("files") or {}).get("visual", ""))
    if not visual.exists():
        raise AppError("The caption source video is missing. Re-render the clip.", code="visual_missing")
    out_dir = get_settings().outputs_dir / job_id / clip_id
    manager.update(job_id, status="rendering", stage=f"Translating to {language}...")
    files = edit.burn_captions_only(
        visual,
        beats,
        settings,
        out_dir,
        language=clip.get("language", ""),
        duration=float(clip.get("rendered_duration") or clip.get("duration") or 0),
        key_point=clip.get("key_point", ""),
        translated=translated,
        credit=clip.get("credits_text", ""),
    )
    manager.update_clip(
        job_id,
        clip_id,
        translated=translated,
        files={**(clip.get("files") or {}), **files},
        status="done",
        stage="Ready",
        progress=100,
    )
    _settle_after_clip(manager, job_id, f"Captions translated to {language}")
    manager.log(job_id, f"Captions translated to {language}.")


def resync_captions(
    manager: JobManager,
    job_id: str,
    clip_id: str,
    settings_override: ClipSettings | None,
) -> None:
    """Re-time a clip's captions to the speech detected in its own audio."""
    job = manager.get(job_id)
    clip = manager.find_clip(job_id, clip_id)
    beats = clip.get("beats") or []
    if not beats:
        raise AppError("Render the clip before syncing captions.", code="no_captions")
    visual = Path((clip.get("files") or {}).get("visual", ""))
    if not visual.exists():
        raise AppError("The caption source video is missing. Re-render the clip.", code="visual_missing")
    settings = settings_override or _settings(job)
    manager.update(job_id, status="rendering", stage="Syncing captions to audio...")
    aligned, notes = align.align_beats_to_audio(visual, beats)
    for note in notes:
        manager.warn(job_id, note)
    out_dir = get_settings().outputs_dir / job_id / clip_id
    files = edit.burn_captions_only(
        visual,
        aligned,
        settings,
        out_dir,
        language=clip.get("language", ""),
        duration=float(clip.get("rendered_duration") or clip.get("duration") or 0),
        key_point=clip.get("key_point", ""),
        translated=clip.get("translated") or None,
        credit=clip.get("credits_text", ""),
    )
    manager.update_clip(
        job_id,
        clip_id,
        beats=aligned,
        files={**(clip.get("files") or {}), **files},
        status="done",
        stage="Ready",
        progress=100,
    )
    _settle_after_clip(manager, job_id, "Captions synced to audio")
    manager.log(job_id, "Captions re-timed to the audio.")
