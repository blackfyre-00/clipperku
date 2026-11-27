"""Caption beats, ASS/SRT generation, presets, safe-area layout and translation."""
from __future__ import annotations

import json
import os
import re
from typing import Any

from app.errors import AppError
from app.schemas import ClipSettings
from services import llm

# ---------------------------------------------------------------- colors / time


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = (hex_color or "#FFFFFF").lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def ass_color(hex_color: str, alpha: int = 0) -> str:
    r, g, b = _rgb(hex_color)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def ass_inline(hex_color: str) -> str:
    r, g, b = _rgb(hex_color)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _has_thai(text: str) -> bool:
    return bool(re.search(r"[\u0E00-\u0E7F]", text or ""))


def default_font(language: str, text: str = "") -> str:
    thai = (language or "").lower().startswith("th") or _has_thai(text)
    if thai:
        return "Leelawadee UI" if os.name == "nt" else "Noto Sans Thai"
    return "Arial" if os.name == "nt" else "DejaVu Sans"


def _escape(text: str) -> str:
    text = (text or "").replace("\r", " ").replace("\n", " ")
    text = text.replace("{", "(").replace("}", ")")
    return re.sub(r"\s+", " ", text).strip()


def _wrap(text: str, max_chars: int) -> str:
    text = _escape(text)
    if len(text) <= max_chars:
        return text
    if " " in text:
        words = text.split(" ")
        lines: list[str] = []
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if len(candidate) > max_chars and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        return "\\N".join(lines[:3])
    return "\\N".join(text[i : i + max_chars] for i in range(0, min(len(text), max_chars * 3), max_chars))


# ---------------------------------------------------------------- beats


def beats_from_words(
    words: list[dict[str, Any]],
    start: float,
    end: float,
    *,
    max_words: int = 2,
    max_chars: int = 26,
) -> list[dict[str, Any]]:
    """Create 1-2 word beats with clip-relative timing."""
    selected: list[dict[str, Any]] = []
    for word in words:
        w_start = float(word.get("start", 0.0))
        w_end = float(word.get("end", w_start))
        if w_end <= start + 0.02 or w_start >= end - 0.02:
            continue
        text = _escape(str(word.get("word", "")))
        if not text:
            continue
        selected.append(
            {
                "text": text,
                "start": max(0.0, w_start - start),
                "end": max(0.05, min(end, w_end) - start),
            }
        )
    if not selected:
        return []

    beats: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []
    for word in selected:
        if group:
            gap = word["start"] - group[-1]["end"]
            chars = sum(len(w["text"]) for w in group) + len(group) + len(word["text"])
            punctuation = bool(re.search(r"[.!?…,;:。！？，]$", group[-1]["text"]))
            if len(group) >= max_words or gap > 0.55 or chars > max_chars or punctuation:
                beats.append(_make_beat(group, len(beats)))
                group = []
        group.append(word)
    if group:
        beats.append(_make_beat(group, len(beats)))

    # Clean monotonic timing.
    for i, beat in enumerate(beats):
        if i + 1 < len(beats):
            beat["end"] = max(beat["start"] + 0.08, min(beat["end"], beats[i + 1]["start"]))
        beat["end"] = max(beat["start"] + 0.08, beat["end"])
        for word in beat["words"]:
            word["start"] = max(beat["start"], word["start"])
            word["end"] = max(word["start"] + 0.05, min(word["end"], beat["end"]))
    return beats


def _make_beat(words: list[dict[str, Any]], index: int) -> dict[str, Any]:
    return {
        "id": f"b{index}",
        "start": round(words[0]["start"], 3),
        "end": round(words[-1]["end"], 3),
        "text": " ".join(w["text"] for w in words).strip(),
        "words": [dict(w) for w in words],
    }


def apply_edits(beats: list[dict[str, Any]], edits: dict[str, str]) -> list[dict[str, Any]]:
    """Replace beat text (names, homophones) and redistribute word timing."""
    out: list[dict[str, Any]] = []
    for beat in beats:
        text = edits.get(beat["id"])
        if text is None:
            out.append(beat)
            continue
        new_beat = dict(beat)
        clean = _escape(text)
        new_beat["text"] = clean
        tokens = clean.split(" ") if " " in clean else ([clean] if clean else [])
        span = max(0.1, beat["end"] - beat["start"])
        step = span / max(1, len(tokens))
        new_beat["words"] = [
            {
                "text": token,
                "start": round(beat["start"] + i * step, 3),
                "end": round(beat["start"] + (i + 1) * step, 3),
            }
            for i, token in enumerate(tokens)
        ]
        out.append(new_beat)
    return out


def beats_text(beats: list[dict[str, Any]]) -> str:
    return " ".join(b.get("text", "") for b in beats).strip()


# ---------------------------------------------------------------- layout / styles


def safe_margin(settings: ClipSettings, height: int, outline: int = 4) -> int:
    """Never place caption text inside the bottom 15% covered by platform UI."""
    minimum = round(height * 0.16) + outline * 2
    requested = round(height * (settings.caption_position / 100.0))
    return max(minimum, requested)


PRESETS: dict[str, dict[str, Any]] = {
    "graphic": {
        "label": "Graphic Pill",
        "description": "Bold white uppercase words in a rounded dark bubble, like the speech-bubble overlay in the reference shorts.",
        "scale": 0.78,
        "bold": 1,
        "outline": 4,
        "shadow": 1,
        "border_style": 3,
        "animation": "fade",
        "box_alpha": 190,
        "active_bold": False,
        "force_uppercase": True,
        "highlight": False,
        "lower_left": False,
    },
    "cinema": {
        "label": "Cinema Label",
        "description": "Bold uppercase white label at lower-left, no box — the documentary / title-card look.",
        "scale": 0.86,
        "bold": 1,
        "outline": 6,
        "shadow": 1,
        "border_style": 1,
        "animation": "fade",
        "box_alpha": 0,
        "active_bold": False,
        "force_uppercase": True,
        "highlight": False,
        "lower_left": True,
    },
    "karaoke": {
        "label": "Bold Karaoke",
        "description": "Big bold words in a dark chip, bright word-by-word highlight with a punchy pop-in. Recommended default.",
        "scale": 1.05,
        "bold": 1,
        "outline": 10,
        "shadow": 2,
        "border_style": 3,
        "animation": "punch",
        "box_alpha": 140,
        "active_bold": True,
        "force_uppercase": False,
        "highlight": True,
        "lower_left": False,
    },
    "bold_pop": {
        "label": "Bold Pop",
        "description": "Chunky bold sans, white with a dark outline, yellow spoken word, quick pop-in.",
        "scale": 1.0,
        "bold": 1,
        "outline": 5,
        "shadow": 2,
        "border_style": 1,
        "animation": "pop",
        "box_alpha": 0,
        "active_bold": True,
        "force_uppercase": False,
        "highlight": True,
        "lower_left": False,
    },
    "minimal": {
        "label": "Minimal",
        "description": "Smaller, lighter captions with a soft outline. Calm and editorial.",
        "scale": 0.82,
        "bold": 0,
        "outline": 3,
        "shadow": 1,
        "border_style": 1,
        "animation": "fade",
        "box_alpha": 0,
        "active_bold": False,
        "force_uppercase": False,
        "highlight": True,
        "lower_left": False,
    },
}


def preset_payload() -> list[dict[str, Any]]:
    return [{"id": key, **value} for key, value in PRESETS.items()]


def _style_line(
    name: str,
    *,
    font: str,
    size: int,
    primary: str,
    outline_color: str,
    back_color: str,
    bold: int,
    border_style: int,
    outline: int,
    shadow: int,
    alignment: int,
    margin_v: int,
    margin_l: int = 0,
    margin_r: int = 0,
    box_alpha: int = 128,
) -> str:
    # ASS alpha: 0 = opaque, 255 = invisible. box_alpha is *opacity*.
    back_alpha = 255 - max(0, min(255, box_alpha)) if border_style == 3 else 0
    return (
        f"Style: {name},{font},{size},{ass_color(primary)},{ass_color(primary)},"
        f"{ass_color(outline_color)},{ass_color(back_color, back_alpha)},"
        f"{bold},0,0,0,100,100,0,0,{border_style},{outline},{shadow},{alignment},"
        f"{margin_l},{margin_r},{margin_v},1"
    )


def _dims(settings: ClipSettings, width: int, height: int) -> dict[str, Any]:
    preset = PRESETS.get(settings.caption_preset, PRESETS["bold_pop"])
    base = settings.caption_size * (height / 1920.0)
    size = max(24, round(base * preset["scale"]))
    outline = max(1, round(preset["outline"] * (height / 1920.0) * 1.9))
    margin = safe_margin(settings, height, outline)
    return {
        "preset": preset,
        "size": size,
        "outline": outline,
        "shadow": max(0, round(preset["shadow"] * (height / 1920.0) * 2)),
        "margin": margin,
        "bold": preset["bold"],
        "border_style": preset["border_style"],
        "box_alpha": preset["box_alpha"],
        "force_uppercase": bool(preset.get("force_uppercase", False)),
        "highlight": bool(preset.get("highlight", True)),
        "lower_left": bool(preset.get("lower_left", False)),
    }


# ---------------------------------------------------------------- ASS


def make_ass(
    beats: list[dict[str, Any]],
    settings: ClipSettings,
    width: int,
    height: int,
    *,
    language: str = "",
    duration: float = 0.0,
    key_point: str = "",
    outro_text: str = "",
    credit: str = "",
    translated: dict[str, str] | None = None,
) -> str:
    dims = _dims(settings, width, height)
    sample_text = " ".join(b.get("text", "") for b in beats[:12]) + " " + (translated and " ".join(translated.values()) or "")
    font = settings.caption_font.strip() or default_font(language, sample_text)
    primary = settings.caption_color
    highlight = settings.highlight_color
    outline_color = "#000000"
    back_color = "#000000"

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        _style_line(
            "Caption",
            font=font,
            size=dims["size"],
            primary=primary,
            outline_color=outline_color,
            back_color=back_color,
            bold=dims["bold"],
            border_style=dims["border_style"],
            outline=dims["outline"],
            shadow=dims["shadow"],
            alignment=1 if dims["lower_left"] else 2,
            margin_v=max(round(height * 0.17), dims["margin"]) if dims["lower_left"] else dims["margin"],
            margin_l=round(width * 0.08) if dims["lower_left"] else 0,
            box_alpha=dims["box_alpha"],
        ),
        _style_line(
            "CaptionAlt",
            font=font,
            size=max(20, round(dims["size"] * 0.78)),
            primary="#E8E8E8",
            outline_color=outline_color,
            back_color=back_color,
            bold=dims["bold"],
            border_style=dims["border_style"],
            outline=max(1, dims["outline"] - 2),
            shadow=dims["shadow"],
            alignment=2,
            margin_v=max(round(height * 0.17), dims["margin"] - round(dims["size"] * 0.95)),
            box_alpha=dims["box_alpha"],
        ),
        _style_line(
            "KeyPoint",
            font=font,
            size=max(24, round(dims["size"] * 0.58)),
            primary=highlight if dims["preset"]["animation"] == "punch" else "#FFFFFF",
            outline_color="#000000",
            back_color="#000000",
            bold=1,
            border_style=3,
            outline=max(6, round(height * 0.012)),
            shadow=0,
            alignment=1 if dims["lower_left"] else 8,
            margin_v=round(height * (0.16 if dims["lower_left"] else 0.10)),
            margin_l=round(width * 0.08),
            margin_r=round(width * 0.08),
            box_alpha=120,
        ),
        _style_line(
            "Outro",
            font=font,
            size=max(28, round(dims["size"] * 0.92)),
            primary="#FFFFFF",
            outline_color="#000000",
            back_color="#000000",
            bold=1,
            border_style=3,
            outline=max(8, round(height * 0.014)),
            shadow=0,
            alignment=5,
            margin_v=0,
        ),
        _style_line(
            "Credit",
            font=font,
            size=max(18, round(dims["size"] * 0.32)),
            primary="#FFFFFF",
            outline_color="#000000",
            back_color="#000000",
            bold=0,
            border_style=1,
            outline=max(1, dims["outline"] - 2),
            shadow=1,
            alignment=7,
            margin_v=round(height * 0.035),
            margin_l=round(width * 0.04),
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    if key_point and duration > 0:
        end = min(duration, 5.2)
        point_anim = "{\\fad(150,240)\\t(0,220,\\fscx106\\fscy106)}" if dims["preset"]["animation"] == "punch" else "{\\fad(180,260)}"
        point_text = _wrap(key_point, 30)
        if dims["force_uppercase"]:
            point_text = point_text.upper()
        lines.append(
            f"Dialogue: 2,{ass_time(0)},{ass_time(end)},KeyPoint,,0,0,0,,"
            f"{point_anim}{point_text}"
        )

    translated = translated or {}
    upper = settings.uppercase or dims["force_uppercase"]
    beat_anim = "{\\fad(60,60)}"
    if dims["preset"]["animation"] == "pop":
        beat_anim = "{\\fscx82\\fscy82\\t(0,70,\\fscx105\\fscy105)\\t(70,140,\\fscx100\\fscy100)}"
    elif dims["preset"]["animation"] == "punch":
        beat_anim = (
            "{\\fscx86\\fscy86\\fad(45,45)"
            "\\t(0,90,\\fscx108\\fscy108)\\t(90,180,\\fscx100\\fscy100)}"
        )

    for beat in beats:
        alt = translated.get(beat["id"], "")
        alt_line = _wrap(alt, 30) if alt else ""

        if not dims["highlight"]:
            # Speech-bubble / label look: one steady line per beat, no karaoke.
            text = _wrap(beat.get("text", ""), 30)
            if upper:
                text = text.upper()
            primary_line = text
            if alt_line:
                primary_line = (
                    f"{primary_line}\\N{{\\rCaptionAlt}}{alt_line}"
                    if settings.bilingual
                    else f"{{\\rCaptionAlt}}{alt_line}"
                )
            lines.append(
                f"Dialogue: 1,{ass_time(beat['start'])},{ass_time(beat['end'])},Caption,,0,0,0,,"
                f"{{\\fad(90,110)}}{primary_line}"
            )
            continue

        words = beat.get("words") or [{"text": beat.get("text", ""), "start": beat["start"], "end": beat["end"]}]
        for i, word in enumerate(words):
            start = float(word.get("start", beat["start"]))
            end = float(words[i + 1]["start"]) if i + 1 < len(words) else float(beat["end"])
            end = max(start + 0.06, min(end, float(beat["end"])))
            rendered = []
            for j, token in enumerate(words):
                text = token.get("text", "")
                if upper:
                    text = text.upper()
                if j == i:
                    rendered.append(
                        f"{{\\c{ass_inline(highlight)}\\b1\\fscx112\\fscy112}}{text}"
                        f"{{\\c{ass_inline(primary)}\\b{dims['bold']}\\fscx100\\fscy100}}"
                    )
                else:
                    rendered.append(text)
            primary_line = " ".join(rendered)
            if alt_line:
                primary_line = (
                    f"{primary_line}\\N{{\\rCaptionAlt}}{alt_line}"
                    if settings.bilingual
                    else f"{{\\rCaptionAlt}}{alt_line}"
                )
            lines.append(
                f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{beat_anim}{primary_line}"
            )

    if outro_text and duration > 0:
        start = max(0.0, duration - 2.7)
        lines.append(
            f"Dialogue: 3,{ass_time(start)},{ass_time(duration)},Outro,,0,0,0,,"
            f"{{\\pos({width // 2},{round(height * 0.46)})\\fad(160,160)}}{_wrap(outro_text, 26)}"
        )

    if credit and duration > 0:
        start = max(0.0, duration - 2.4)
        lines.append(
            f"Dialogue: 4,{ass_time(start)},{ass_time(duration)},Credit,,0,0,0,,"
            f"{{\\fad(150,150)}}{_wrap(credit, 46)}"
        )

    return "\n".join(lines) + "\n"


def make_srt(beats: list[dict[str, Any]], *, max_words: int = 7, max_chars: int = 42) -> str:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for beat in beats:
        if not current:
            current = [beat]
            continue
        words = sum(len(b.get("text", "").split()) for b in current) + len(beat.get("text", "").split())
        chars = sum(len(b.get("text", "")) for b in current) + len(beat.get("text", ""))
        gap = beat["start"] - current[-1]["end"]
        if words > max_words or chars > max_chars or gap > 0.8:
            groups.append(current)
            current = [beat]
        else:
            current.append(beat)
    if current:
        groups.append(current)

    out: list[str] = []
    for i, group in enumerate(groups, start=1):
        text = " ".join(b.get("text", "") for b in group).strip()
        out.append(f"{i}\n{srt_time(group[0]['start'])} --> {srt_time(group[-1]['end'])}\n{text}\n")
    return "\n".join(out)


# ---------------------------------------------------------------- translation


def translate_beats(beats: list[dict[str, Any]], language: str) -> dict[str, str]:
    if not beats:
        return {}
    if llm.resolve_provider() == "mock" or not llm.is_configured():
        raise AppError(
            "Caption translation needs an LLM provider.",
            code="translation_unavailable",
            hint="Set LLM_PROVIDER and LLM_API_KEY in .env.",
        )
    payload = [{"id": b["id"], "text": b.get("text", "")} for b in beats]
    system = (
        "You translate short-form video captions. Translate each caption line naturally and concisely "
        "into the requested language. Keep names, numbers and slang faithful. Do not add commentary. "
        "Return ONLY JSON: {\"lines\":[{\"id\":\"b0\",\"text\":\"...\"}]} with the same ids and count."
    )
    user = (
        f"TARGET LANGUAGE: {language}\n"
        f"CAPTION LINES:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        response = llm.chat(system, user, json_mode=True, max_tokens=4000, temperature=0.2)
        data = llm.parse_json(response)
        rows = data.get("lines", []) if isinstance(data, dict) else data
        result: dict[str, str] = {}
        for row in rows:
            if isinstance(row, dict) and row.get("id") in {b["id"] for b in beats}:
                result[str(row["id"])] = _escape(str(row.get("text", "")))
        if not result:
            raise ValueError("empty translation")
        return result
    except (AppError, ValueError, KeyError, TypeError) as exc:
        raise AppError(
            "Caption translation failed.",
            code="translation_failed",
            hint=str(exc),
        ) from exc
