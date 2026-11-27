"""Clip selection: rubric prompt, robust normalization and a heuristic fallback."""
from __future__ import annotations

import math
import re
import secrets
from typing import Any

from app.config import get_settings
from app.errors import AppError
from app.schemas import ClipSettings
from services import llm

HOOK_TYPES = {"shock", "question", "promise", "story", "insight", "controversy", "other"}

GREETING = re.compile(
    r"^(?:(?:hey|hi|hello|yo|sup|okay|ok)[\s,!.]*)?"
    r"(?:(?:guys|everyone|everybody|folks|friends|y'all|all)[\s,!.]*)?"
    r"(?:(?:welcome back|welcome)[\s,!.]*)?"
    r"(?:(?:to (?:the|my) (?:channel|show|podcast|video))[\s,!.]*)?"
    r"$",
    re.IGNORECASE,
)
INTRO_PHRASES = re.compile(
    r"^(in (?:this|today'?s) (?:video|episode)|today (?:i|we)(?:'re| are)? going to|"
    r"before we (?:get started|begin)|let'?s (?:get|dive) (?:started|in)|"
    r"quick (?:announcement|word)|สวัสดี(?:ครับ|ค่ะ|คะ)|วันนี้(?:เราจะ|ผมจะ|ฉันจะ)|"
    r"大家好|今天(?:我们|我)要)",
    re.IGNORECASE,
)
FILLERS = {
    "um", "umm", "uh", "uhh", "er", "erm", "ah", "ahh", "hmm", "mm", "like",
    "so", "okay", "ok", "right", "well", "you know", "i mean", "basically", "actually",
    "เอ่อ", "เออ", "อ่า", "อืม", "อ่ะ", "นั่นแหละ", "คือว่า", "แบบว่า",
}
POWER_WORDS = {
    "secret", "mistake", "never", "nobody", "everyone", "why", "how", "truth", "wrong",
    "huge", "insane", "free", "money", "best", "worst", "stop", "warning", "shocking",
    "unbelievable", "problem", "fix", "nobody", "always", "proven", "simple", "danger",
    "จริงๆ", "ทำไม", "ผิด", "ลับ", "ที่สุด", "ไม่เคย", "ทุกคน", "หยุด", "อันตราย", "ฟรี",
}
EMOTION_WORDS = {
    "love", "hate", "fear", "amazing", "crazy", "angry", "sad", "excited", "proud",
    "struggle", "pain", "win", "fail", "hilarious", "terrifying", "beautiful", "regret",
    "รัก", "เกลียด", "กลัว", "สุดยอด", "บ้า", "โกรธ", "เศร้า", "ตื่นเต้น", "ภูมิใจ",
    "เจ็บ", "ชนะ", "แพ้", "ตลก", "เสียใจ",
}
PAYOFF_WORDS = {
    "result", "therefore", "here's", "that's why", "turns out", "in the end", "which means",
    "the point", "so that", "because", "finally", "lesson", "takeaway",
    "ดังนั้น", "ผลลัพธ์", "นี่คือ", "นั่นคือเหตุผล", "สรุป", "เพราะ", "บทเรียน",
}
STOPWORDS = {
    "the", "and", "that", "this", "with", "from", "have", "will", "just", "your", "you",
    "for", "are", "was", "were", "but", "not", "what", "when", "how", "why", "they",
    "them", "there", "here", "about", "into", "than", "then", "very", "really", "can",
    "could", "would", "should", "been", "being", "because", "which", "while", "also",
    "และ", "ที่", "การ", "ความ", "เป็น", "ได้", "จะ", "ให้", "กับ", "ของ", "ใน", "มี",
    "ไม่", "ว่า", "ก็", "เรา", "คุณ", "เขา", "มัน", "นี้", "นั้น",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _units(transcript: dict[str, Any], max_words: int = 34) -> list[dict[str, Any]]:
    words = transcript.get("words") or []
    units: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        current.append(word)
        text = _clean(word.get("word", ""))
        ends = bool(re.search(r"[.!?…。！？]$", text))
        if len(current) >= max_words:
            ends = True
        if ends:
            units.append(_unit(current))
            current = []
    if current:
        units.append(_unit(current))
    # split on long gaps (Thai has no punctuation)
    split: list[dict[str, Any]] = []
    for unit in units:
        ws = unit["words"]
        chunk: list[dict[str, Any]] = []
        for i, word in enumerate(ws):
            chunk.append(word)
            if i < len(ws) - 1 and (ws[i + 1]["start"] - word["end"]) >= 0.6:
                split.append(_unit(chunk))
                chunk = []
        if chunk:
            split.append(_unit(chunk))
    return [u for u in split if u["text"]]


def _unit(words: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "start": float(words[0]["start"]),
        "end": float(words[-1]["end"]),
        "text": _clean(" ".join(str(w.get("word", "")) for w in words)),
        "words": words,
    }


def _first_unit_index(units: list[dict[str, Any]], start: float) -> int:
    for i, unit in enumerate(units):
        if unit["start"] <= start <= unit["end"] + 0.25:
            return i
    return min(range(len(units)), key=lambda i: abs(units[i]["start"] - start))


def _strip_intro(units: list[dict[str, Any]], idx: int) -> int:
    """Advance past greetings / channel intros / filler-only openings."""
    for _ in range(4):
        if idx >= len(units):
            break
        text = units[idx]["text"]
        if GREETING.match(text) or INTRO_PHRASES.match(text):
            idx += 1
            continue
        words = units[idx]["words"]
        stripped = 0
        for word in words[:5]:
            token = _clean(word.get("word", "")).lower().strip(",.!?")
            if token in FILLERS or not token:
                stripped += 1
            else:
                break
        if stripped and stripped < len(words):
            trimmed = words[stripped:]
            units[idx] = _unit(trimmed)
        break
    return min(idx, len(units) - 1)


def _score_hook(text: str) -> float:
    lower = f" {text.lower()} "
    score = 3.0
    if "?" in text:
        score += 2.4
    if re.search(r"\d", text):
        score += 1.3
    score += min(4.0, sum(1.7 for w in POWER_WORDS if w in lower))
    if re.match(r"^\s*(why|how|what|stop|never|nobody|most people|here'?s|ทำไม|หยุด|ไม่มีใคร)", lower):
        score += 1.4
    if re.search(r"\b(you|your|you're)\b|คุณ", lower):
        score += 0.8
    if "!" in text:
        score += 0.5
    return round(max(0.0, min(10.0, score)), 1)


def _score_clarity(text: str) -> float:
    words = text.split()
    n = len(words)
    filler_hits = sum(1 for w in words if w.lower().strip(",.!?") in FILLERS)
    score = 7.2
    if n < 5:
        score -= 2.0
    if n > 30:
        score -= 2.2
    if n > 45:
        score -= 1.5
    score -= min(3.0, filler_hits * 0.9)
    if re.search(r"[.!?…]$", text.strip()):
        score += 0.8
    return round(max(0.0, min(10.0, score)), 1)


def _score_emotion(text: str) -> float:
    lower = f" {text.lower()} "
    hits = sum(1 for w in EMOTION_WORDS if w in lower)
    score = 3.0 + min(5.0, hits * 1.4)
    if "!" in text:
        score += 0.8
    if re.search(r"\b(i|we)\b", lower):
        score += 0.5
    return round(max(0.0, min(10.0, score)), 1)


def _score_payoff(text: str, end_text: str) -> float:
    lower = f" {text.lower()} "
    score = 4.0
    score += min(3.5, sum(1.4 for w in PAYOFF_WORDS if w in lower))
    if re.search(r"\d", text):
        score += 1.2
    if len(end_text.split()) >= 6:
        score += 0.8
    return round(max(0.0, min(10.0, score)), 1)


def _keywords(text: str, limit: int = 2) -> list[str]:
    tokens = re.findall(r"[A-Za-zก-๙][A-Za-zก-๙'’-]{3,}", text)
    seen: list[str] = []
    for token in tokens:
        low = token.lower()
        if low in STOPWORDS or low in seen:
            continue
        seen.append(low)
    return seen[:limit]


def heuristic_select(
    transcript: dict[str, Any],
    settings: ClipSettings,
    *,
    reason_prefix: str = "",
) -> list[dict[str, Any]]:
    units = _units(transcript)
    if not units:
        raise AppError("The transcript is empty.", code="empty_transcript")
    candidates: list[dict[str, Any]] = []
    max_candidates = 140
    step = 1 if len(units) <= 60 else 2
    for start_idx in range(0, len(units), step):
        if len(candidates) >= max_candidates:
            break
        start_idx = _strip_intro(units, start_idx)
        start = units[start_idx]["start"]
        best_end = None
        last_end = None
        for end_idx in range(start_idx, min(len(units), start_idx + 24)):
            end = units[end_idx]["end"]
            length = end - start
            if length < settings.min_length:
                continue
            if length > settings.max_length:
                break
            last_end = end
            if length >= settings.min_length + 4:
                best_end = end
                break
        end = best_end or last_end
        if end is None:
            continue
        length = end - start
        if not (settings.min_length - 0.5 <= length <= settings.max_length + 0.5):
            continue
        window = units[start_idx : start_idx + 24]
        text = _clean(" ".join(u["text"] for u in window if u["start"] < end))
        if len(text.split()) < 6:
            continue
        hook_text = units[start_idx]["text"][:180]
        hook = _score_hook(hook_text + " " + text[:120])
        clarity = _score_clarity(text[:500])
        emotion = _score_emotion(text[:600])
        payoff = _score_payoff(text[:600], units[min(len(units) - 1, start_idx + 2)]["text"])
        total = round(hook + clarity + emotion + payoff, 1)
        if hook < 3.8:
            continue
        candidates.append(
            {
                "start": round(start, 2),
                "end": round(end - 0.05, 2),
                "hook_text": hook_text,
                "hook_type": "question" if "?" in hook_text else "insight",
                "scores": {"hook": hook, "clarity": clarity, "emotion": emotion, "payoff": payoff},
                "score": total,
                "reason": (
                    f"{reason_prefix}Opens with “{hook_text[:90]}”. "
                    f"Hook {hook}/10, clarity {clarity}/10, emotion {emotion}/10, payoff {payoff}/10."
                ),
                "key_point": _clean(units[start_idx]["text"])[:110],
                "broll_terms": _keywords(text[:800]),
            }
        )

    # Greedy de-overlap, best total first.
    candidates.sort(key=lambda c: c["score"], reverse=True)
    picked: list[dict[str, Any]] = []
    for candidate in candidates:
        if len(picked) >= settings.clip_count:
            break
        if any(_overlap(candidate, other) > 0.35 for other in picked):
            continue
        picked.append(candidate)
    picked.sort(key=lambda c: c["score"], reverse=True)
    for index, candidate in enumerate(picked):
        candidate["id"] = f"clip_{secrets.token_urlsafe(6)}"
        candidate["index"] = index
        candidate["duration"] = round(candidate["end"] - candidate["start"], 2)
        candidate["warnings"] = _clip_warnings(candidate, settings)
    return picked


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    shorter = max(0.1, min(a["end"] - a["start"], b["end"] - b["start"]))
    return inter / shorter


def _clip_warnings(clip: dict[str, Any], settings: ClipSettings) -> list[str]:
    warnings: list[str] = []
    if clip.get("duration", 0) > 60:
        warnings.append("Longer than 60s — YouTube Shorts will reject it unless it is under the limit.")
    if clip.get("duration", 0) < settings.min_length - 1:
        warnings.append("Shorter than your minimum length because the source ran out of speech.")
    return warnings


def _prompt(settings: ClipSettings, meta: dict[str, Any], transcript_text: str, count: int) -> tuple[str, str]:
    system = (
        "You are a ruthless short-form video editor who has studied millions of high-performing clips. "
        "You pick moments that win the first 3 seconds and hold attention to the end.\n\n"
        "SCORING RUBRIC — score each clip 0-10 on all four, then total /40:\n"
        "1. hook — first 3 seconds: shock/surprise, direct question, or intriguing promise. "
        "No greetings, no 'hey guys', no channel intro, no slow build. Reward specificity and tension.\n"
        "2. standalone_clarity — makes sense with zero context from the rest of the video; "
        "complete thought, understandable pronouns, no dangling references.\n"
        "3. emotional_intensity — stakes, conflict, surprise, relatability, strong opinion. "
        "Calm information alone scores low.\n"
        "4. payoff — the clip delivers an answer, result, lesson, twist or concrete takeaway. "
        "Front-load the payoff if the best moment is later; do not save it for the end.\n\n"
        "HARD RULES:\n"
        "- Start and end on complete sentence boundaries from the transcript timestamps.\n"
        "- Never start on a greeting, filler, false start or mid-sentence.\n"
        "- Clip length must be between the given min and max seconds (default 20-60).\n"
        "- The opening line must match the first frame promise; prefer a line that works as a cold open.\n"
        "- Prefer clips that end slightly early on a strong beat rather than trailing off.\n"
        "- Do not invent timestamps. Only use the transcript.\n"
        "- Clips must not overlap each other.\n"
        "- Write hook_text exactly as spoken (mixed case, no added punctuation).\n"
        "- key_point: one short on-screen line capturing the clip's main idea (max 12 words).\n"
        "- broll_terms: 0-3 concrete visual nouns/search terms mentioned in the clip (English search terms).\n"
        "- reason: one or two sentences explaining the score, referencing the opening line.\n\n"
        "Return ONLY JSON, no markdown:\n"
        '{"clips":[{"start":12.4,"end":48.9,"hook_text":"...","hook_type":'
        '"shock|question|promise|story|insight|controversy|other",'
        '"scores":{"hook":9,"standalone_clarity":8,"emotional_intensity":7,"payoff":9},'
        '"reason":"...","key_point":"...","broll_terms":["..."]}]}'
    )
    user = (
        f"VIDEO: {meta.get('title', 'Unknown')}\n"
        f"DURATION: {meta.get('duration', 0):.1f}s\n"
        f"LANGUAGE: {meta.get('language', 'unknown')}\n"
        f"REQUESTED CLIPS: {count}\n"
        f"MIN/MAX LENGTH: {settings.min_length:.0f}-{settings.max_length:.0f}s\n"
        f"TARGET PLATFORM: vertical short-form (YouTube Shorts / TikTok / Reels)\n\n"
        "TRANSCRIPT (sentence-ish units with start-end seconds):\n"
        f"{transcript_text}\n\n"
        f"Return the {count} strongest non-overlapping clips, sorted by total score descending. "
        "If there are fewer strong moments, return fewer rather than padding with weak clips."
    )
    return system, user


def _transcript_text(transcript: dict[str, Any], limit_chars: int = 24000) -> tuple[str, list[list[dict[str, Any]]]]:
    units = _units(transcript)
    lines = [f"[{u['start']:.1f}-{u['end']:.1f}] {u['text']}" for u in units]
    total = sum(len(line) + 1 for line in lines)
    if total <= limit_chars:
        return "\n".join(lines), [units]
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for unit, line in zip(units, lines):
        if size + len(line) > limit_chars and current:
            chunks.append(current)
            current = []
            size = 0
        current.append(unit)
        size += len(line) + 1
    if current:
        chunks.append(current)
    rendered = []
    for chunk in chunks:
        rendered.append(
            "\n".join(f"[{u['start']:.1f}-{u['end']:.1f}] {u['text']}" for u in chunk)
        )
    return "\n\n---\n\n".join(rendered), chunks


def _normalize_clip(raw: dict[str, Any], transcript: dict[str, Any], settings: ClipSettings) -> dict[str, Any] | None:
    units = _units(transcript)
    if not units:
        return None
    try:
        start = float(raw.get("start"))
        end = float(raw.get("end"))
    except (TypeError, ValueError):
        return None
    duration = float(transcript.get("duration") or units[-1]["end"])
    if end <= start or start < 0 or start >= duration:
        return None
    start_idx = _first_unit_index(units, start)
    start_idx = _strip_intro(units, start_idx)
    start = units[start_idx]["start"]

    # End at a sentence boundary near the requested end.
    end_idx = min(range(len(units)), key=lambda i: abs(units[i]["end"] - end))
    if units[end_idx]["end"] - start > settings.max_length:
        while end_idx > start_idx and units[end_idx]["end"] - start > settings.max_length:
            end_idx -= 1
    end = units[end_idx]["end"]
    if end - start < settings.min_length:
        for i in range(end_idx + 1, len(units)):
            if units[i]["end"] - start <= settings.max_length:
                end_idx = i
                end = units[i]["end"]
            if end - start >= settings.min_length:
                break
    if end - start < max(5.0, settings.min_length - 2):
        return None
    if end - start > settings.max_length + 0.5:
        end = start + settings.max_length

    scores_raw = raw.get("scores") or {}
    def pick(*names: str, default: float = 6.0) -> float:
        for name in names:
            if name in scores_raw:
                try:
                    return max(0.0, min(10.0, float(scores_raw[name])))
                except (TypeError, ValueError):
                    continue
        return default

    hook = pick("hook", "hook_strength")
    clarity = pick("standalone_clarity", "clarity")
    emotion = pick("emotional_intensity", "emotion")
    payoff = pick("payoff")
    total = round(hook + clarity + emotion + payoff, 1)
    if total == 24.0 and "score" in raw:
        try:
            supplied = float(raw["score"])
            total = round(supplied if supplied > 10 else supplied * 4, 1)
        except (TypeError, ValueError):
            pass

    hook_text = _clean(str(raw.get("hook_text") or units[start_idx]["text"]))[:240]
    hook_type = str(raw.get("hook_type") or "other").lower().strip()
    if hook_type not in HOOK_TYPES:
        hook_type = "other"
    terms = raw.get("broll_terms") or []
    if not isinstance(terms, list):
        terms = []
    terms = [str(t)[:60] for t in terms if str(t).strip()][:3]

    clip = {
        "id": f"clip_{secrets.token_urlsafe(6)}",
        "start": round(start, 2),
        "end": round(end - 0.04, 2),
        "duration": round(end - start - 0.04, 2),
        "hook_text": hook_text,
        "hook_type": hook_type,
        "scores": {
            "hook": hook,
            "clarity": clarity,
            "emotion": emotion,
            "payoff": payoff,
        },
        "score": total,
        "reason": _clean(str(raw.get("reason") or ""))[:400] or "Selected by the scoring rubric.",
        "key_point": _clean(str(raw.get("key_point") or hook_text))[:140],
        "broll_terms": terms,
        "warnings": [],
    }
    clip["warnings"] = _clip_warnings(clip, settings)
    return clip


def select_clips(
    transcript: dict[str, Any],
    settings: ClipSettings,
    *,
    meta: dict[str, Any] | None = None,
    log: Any = None,
) -> dict[str, Any]:
    meta = meta or {}
    meta.setdefault("language", transcript.get("language", ""))
    meta.setdefault("duration", transcript.get("duration", 0))
    provider = llm.resolve_provider()
    requested = settings.clip_count
    raw_clips: list[dict[str, Any]] = []
    warning = ""

    if provider == "mock" or not llm.is_configured():
        if log:
            log("No LLM configured — using the built-in heuristic selector.")
        warning = "LLM not configured: clips were picked by the built-in heuristic scorer."
    else:
        transcript_text, chunks = _transcript_text(transcript)
        per_chunk = max(2, math.ceil((requested * 2) / max(1, len(chunks))))
        system, _ = _prompt(settings, meta, transcript_text, per_chunk)
        for chunk in chunks:
            chunk_text = "\n".join(f"[{u['start']:.1f}-{u['end']:.1f}] {u['text']}" for u in chunk)
            _, user = _prompt(settings, meta, chunk_text, per_chunk)
            try:
                response = llm.chat(system, user, json_mode=True, max_tokens=4000, temperature=0.35)
                data = llm.parse_json(response)
                if isinstance(data, dict):
                    data = data.get("clips") or data.get("results") or []
                if isinstance(data, list):
                    raw_clips.extend([c for c in data if isinstance(c, dict)])
            except (AppError, ValueError) as exc:
                if log:
                    log(f"LLM selection failed ({exc}). Falling back to heuristics for this chunk.")
                warning = "The LLM call failed, so the built-in heuristic selector was used."
                break

    normalized: list[dict[str, Any]] = []
    for raw in raw_clips:
        clip = _normalize_clip(raw, transcript, settings)
        if clip:
            normalized.append(clip)

    if not normalized:
        normalized = heuristic_select(transcript, settings)

    normalized.sort(key=lambda c: c["score"], reverse=True)
    deduped: list[dict[str, Any]] = []
    for clip in normalized:
        if any(_overlap(clip, other) > 0.45 for other in deduped):
            continue
        deduped.append(clip)
    deduped = deduped[:requested]
    deduped.sort(key=lambda c: c["score"], reverse=True)
    for index, clip in enumerate(deduped):
        clip["index"] = index

    if not deduped:
        raise AppError(
            "No usable clips were found.",
            code="no_clips",
            hint="Try a longer video with more continuous speech, or lower the minimum clip length.",
        )

    return {
        "clips": deduped,
        "provider": provider,
        "model": get_settings().llm_model or llm.DEFAULT_MODELS.get(provider, ""),
        "used_fallback": provider == "mock" or bool(warning),
        "warning": warning,
    }
