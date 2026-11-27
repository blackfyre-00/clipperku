from __future__ import annotations

from services import metadata, youtube


CLIP = {
    "hook_text": "Why does nobody talk about this simple editing trick?",
    "hook_type": "question",
    "key_point": "Cut the pause before the first word",
    "reason": "Strong question hook with a clear payoff.",
}


def test_heuristic_metadata_is_click_ready() -> None:
    data = metadata.heuristic_metadata(CLIP, language="en")
    assert len(data["title"]) <= 100
    assert data["title"].startswith("Why does nobody")
    assert "#Shorts" in data["hashtags"]
    assert len(data["hashtags"]) <= 5
    assert "#shorts" in data["description"].lower()
    assert data["pinned_comment"]
    assert data["source"] == "heuristic"


def test_generate_metadata_without_llm_never_fails() -> None:
    data = metadata.generate_metadata(CLIP, language="en", beats=[{"text": "Nobody tells you this"}])
    assert data["title"]
    assert data["description"]


def test_schedule_status_is_private_with_publish_at() -> None:
    assert youtube._status("public")["privacyStatus"] == "public"
    scheduled = youtube._status("unlisted", "2026-12-01T18:00:00")
    assert scheduled["privacyStatus"] == "private"
    assert scheduled["publishAt"].endswith("Z")
