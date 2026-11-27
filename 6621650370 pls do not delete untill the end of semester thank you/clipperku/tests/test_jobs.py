from __future__ import annotations

import json
from types import SimpleNamespace

from app import jobs as jobs_mod


def test_restart_marks_processing_jobs_as_error(tmp_path, monkeypatch) -> None:
    payload = {
        "id": "job_stuck",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "processing",
        "stage": "Downloading 74%",
        "progress": 22.2,
        "settings": {},
        "sources": [{"id": "src_1", "url": "https://youtu.be/x", "status": "downloading", "stage": "Downloading 98%"}],
        "clips": [{"id": "clip_1", "status": "rendering", "stage": "Burning captions"}],
        "logs": [],
    }
    (tmp_path / "job_stuck.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(jobs_mod, "ensure_dirs", lambda: None)
    monkeypatch.setattr(jobs_mod, "get_settings", lambda: SimpleNamespace(jobs_dir=tmp_path))

    manager = jobs_mod.JobManager(workers=1)
    try:
        job = manager.get("job_stuck")
        assert job["status"] == "error"
        assert job["error"]["code"] == "server_restarted"
        assert job["sources"][0]["status"] == "error"
        assert job["sources"][0]["stage"] == "Failed"
        assert job["clips"][0]["status"] == "error"
    finally:
        manager.shutdown()


def test_finished_jobs_survive_restart(tmp_path, monkeypatch) -> None:
    payload = {
        "id": "job_done",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "done",
        "stage": "All clips ready",
        "sources": [{"id": "src_1", "status": "ready"}],
        "clips": [{"id": "clip_1", "status": "done", "files": {"video": "x.mp4"}}],
        "logs": [],
    }
    (tmp_path / "job_done.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(jobs_mod, "ensure_dirs", lambda: None)
    monkeypatch.setattr(jobs_mod, "get_settings", lambda: SimpleNamespace(jobs_dir=tmp_path))

    manager = jobs_mod.JobManager(workers=1)
    try:
        job = manager.get("job_done")
        assert job["status"] == "done"
        assert job["clips"][0]["status"] == "done"
    finally:
        manager.shutdown()
