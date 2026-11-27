from __future__ import annotations

import os

import pytest

from app.errors import AppError
from services import media, transcribe


def test_mock_backend_works_on_python_314(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TRANSCRIBE_BACKEND", "mock")
    monkeypatch.setattr(media, "probe", lambda path: {"duration": 20.0})
    data = transcribe.transcribe(tmp_path / "source.mp4", tmp_path)
    assert data["backend"] == "mock"
    assert len(data["words"]) >= 3
    assert (tmp_path / "transcript.json").exists()


def test_real_backend_fails_fast_on_python_314(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TRANSCRIBE_BACKEND", raising=False)
    monkeypatch.setattr(transcribe.sys, "version_info", (3, 14, 0))
    with pytest.raises(AppError) as exc:
        transcribe.transcribe(tmp_path / "source.mp4", tmp_path, progress=lambda pct, msg: None)
    assert "Python 3.14" in exc.value.message
    assert "TRANSCRIBE_BACKEND=mock" in exc.value.message
