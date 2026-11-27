"""Shorts metadata that earns clicks: title, description, hashtags, pinned comment."""
from __future__ import annotations

import json
import re
from typing import Any

from services import llm

_HASHTAG = re.compile(r"^#[A-Za-z0-9_]{2,30}$")


def _clean(text: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def heuristic_metadata(clip: dict[str, Any], language: str = "") -> dict[str, Any]:
    """Deterministic, safe metadata used when no LLM is configured."""
    hook = _clean(clip.get("hook_text", ""), 140).strip("\"' ")
    key_point = _clean(clip.get("key_point", ""), 120)
    reason = _clean(clip.get("reason", ""), 160)
    title = hook or key_point or "This one detail changes everything"
    title = re.sub(r"[.!?]+$", "", title)
    if len(title) > 97:
        title = title[:94].rstrip() + "..."
    hashtags = ["#Shorts"]
    for tag in ("#viral", "#tips", "#editing", "#fyp"):
        if len(hashtags) < 4:
            hashtags.append(tag)
    language_note = f"Language: {language}. " if language else ""
    description = "\n".join(
        part
        for part in [
            title,
            key_point,
            reason or "Watch until the end for the full breakdown.",
            "",
            " ".join(hashtags),
        ]
        if part
    )
    return {
        "title": title[:100],
        "description": description[:4900],
        "hashtags": hashtags,
        "pinned_comment": "Which moment should I turn into a full video next?",
        "source": "heuristic",
        "language": language,
    }


def generate_metadata(
    clip: dict[str, Any],
    *,
    language: str = "",
    beats: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Prefer an LLM when configured; always return usable metadata."""
    fallback = heuristic_metadata(clip, language)
    if not llm.is_configured():
        return fallback

    lines = " ".join(b.get("text", "") for b in (beats or [])[:14])
    system = (
        "You write YouTube Shorts metadata that earns views and subscribers. "
        "Be specific, curiosity-driven and honest. Never clickbait beyond the content. "
        "Return ONLY JSON with keys: title, description, hashtags, pinned_comment. "
        "title <= 100 chars; description <= 800 chars including 3-5 hashtags; "
        "hashtags is a list of strings starting with #; pinned_comment <= 200 chars."
    )
    user = (
        f"LANGUAGE: {language or 'auto'}\n"
        f"HOOK: {clip.get('hook_text', '')}\n"
        f"HOOK TYPE: {clip.get('hook_type', '')}\n"
        f"KEY POINT: {clip.get('key_point', '')}\n"
        f"WHY SELECTED: {clip.get('reason', '')}\n"
        f"CAPTION LINES: {lines}"
    )
    try:
        response = llm.chat(system, user, json_mode=True, max_tokens=1200, temperature=0.6)
        data = llm.parse_json(response)
    except Exception:  # noqa: BLE001 - metadata is optional; never block a delivery
        return fallback
    if not isinstance(data, dict):
        return fallback

    title = _clean(data.get("title"), 100) or fallback["title"]
    tags = data.get("hashtags") or fallback["hashtags"]
    if isinstance(tags, str):
        tags = re.findall(r"#\w+", tags)
    tags = [tag if str(tag).startswith("#") else f"#{tag}" for tag in tags if str(tag).strip()]
    tags = [tag for tag in tags if _HASHTAG.match(tag)]
    tags = list(dict.fromkeys(["#Shorts", *tags]))[:5]
    description = _clean(data.get("description"), 4900) or fallback["description"]
    if "#shorts" not in description.lower():
        description = f"{description}\n\n{' '.join(tags)}".strip()
    pinned = _clean(data.get("pinned_comment"), 200) or fallback["pinned_comment"]
    return {
        "title": title,
        "description": description,
        "hashtags": tags,
        "pinned_comment": pinned,
        "source": "ai",
        "language": language,
    }
