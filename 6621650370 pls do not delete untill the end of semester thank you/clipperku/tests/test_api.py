from __future__ import annotations

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app


def test_health_and_presets() -> None:
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        payload = health.json()
        assert payload["ok"] is True
        assert "llm" in payload and "youtube" in payload
        assert payload["transcribe_backend"]

        presets = client.get("/api/presets")
        assert presets.status_code == 200
        data = presets.json()
        assert {item["id"] for item in data["captions"]} == {"graphic", "cinema", "karaoke", "bold_pop", "minimal"}
        assert len(data["aspects"]) == 3
        assert "permission" in data["disclaimer"].lower()


def test_analyze_rejects_bad_urls() -> None:
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"urls": ["https://example.com/video"]})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation"


def test_analyze_rejects_bad_settings() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/analyze",
            json={
                "urls": ["https://youtu.be/abc123"],
                "settings": {"min_length": 50, "max_length": 40},
            },
        )
        assert response.status_code == 422


def test_unknown_job_returns_clean_error() -> None:
    with TestClient(app) as client:
        response = client.get("/api/jobs/job_missing")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "not_found"


def test_metadata_endpoint(monkeypatch) -> None:
    clip = {
        "hook_text": "Why does nobody talk about this?",
        "key_point": "Hook first",
        "reason": "Clear question hook.",
        "language": "en",
        "beats": [],
    }
    monkeypatch.setattr(main_module, "_clip_for", lambda *args: clip)
    monkeypatch.setattr(main_module.MANAGER, "update_clip", lambda *args, **kwargs: None)
    with TestClient(app) as client:
        response = client.post("/api/jobs/job_1/clips/clip_1/metadata", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["title"]
        assert "#Shorts" in data["hashtags"]


def test_index_is_served() -> None:
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "ShortsClipper" in response.text
