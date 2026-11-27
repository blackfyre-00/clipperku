"""Pexels B-roll search + download with source credits."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.errors import AppError

PEXELS_VIDEO = "https://api.pexels.com/videos/search"
PEXELS_PHOTO = "https://api.pexels.com/v1/search"


def _orientation(aspect: str) -> str:
    return {"9:16": "portrait", "16:9": "landscape", "1:1": "square"}.get(aspect, "portrait")


def _cache_key(term: str, aspect: str, url: str) -> str:
    raw = f"{term}|{aspect}|{url}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _download(url: str, out: Path) -> bool:
    try:
        with httpx.stream("GET", url, timeout=90, follow_redirects=True) as response:
            response.raise_for_status()
            with out.open("wb") as handle:
                for chunk in response.iter_bytes(65536):
                    handle.write(chunk)
        return out.exists() and out.stat().st_size > 2048
    except (httpx.HTTPError, OSError):
        out.unlink(missing_ok=True)
        return False


def _video_asset(term: str, aspect: str, cache: Path) -> dict[str, Any] | None:
    api_key = get_settings().pexels_api_key
    try:
        response = httpx.get(
            PEXELS_VIDEO,
            params={"query": term, "per_page": 6, "orientation": _orientation(aspect), "size": "medium"},
            headers={"Authorization": api_key},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None

    for video in data.get("videos", []):
        files = sorted(
            [f for f in video.get("video_files", []) if (f.get("width") or 0) >= 640],
            key=lambda f: abs((f.get("width") or 0) - 1280),
        )
        for file in files[:3]:
            link = file.get("link")
            if not link:
                continue
            key = _cache_key(term, aspect, link)
            out = cache / f"{key}.mp4"
            if not out.exists() and not _download(link, out):
                continue
            return {
                "kind": "video",
                "path": str(out),
                "term": term,
                "credit": f"B-roll: Pexels / {video.get('user', {}).get('name', 'Pexels')}",
                "source_url": video.get("url", "https://www.pexels.com/"),
                "author": video.get("user", {}).get("name", "Pexels"),
                "license": "Pexels License",
            }
    return None


def _photo_asset(term: str, aspect: str, cache: Path) -> dict[str, Any] | None:
    api_key = get_settings().pexels_api_key
    try:
        response = httpx.get(
            PEXELS_PHOTO,
            params={"query": term, "per_page": 5, "orientation": _orientation(aspect)},
            headers={"Authorization": api_key},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None

    for photo in data.get("photos", []):
        src = photo.get("src", {})
        link = src.get("large2x") or src.get("large") or src.get("original")
        if not link:
            continue
        key = _cache_key(term, aspect, link)
        out = cache / f"{key}.jpg"
        if not out.exists() and not _download(link, out):
            continue
        return {
            "kind": "photo",
            "path": str(out),
            "term": term,
            "credit": f"B-roll: Pexels / {photo.get('photographer', 'Pexels')}",
            "source_url": photo.get("url", "https://www.pexels.com/"),
            "author": photo.get("photographer", "Pexels"),
            "license": "Pexels License",
        }
    return None


def fetch_assets(terms: list[str], aspect: str, limit: int = 2) -> tuple[list[dict[str, Any]], list[str]]:
    """Return downloaded assets + non-fatal warnings. Never raises for API/network errors."""
    warnings: list[str] = []
    api_key = get_settings().pexels_api_key
    if not api_key:
        return [], ["B-roll is on but PEXELS_API_KEY is not set, so no B-roll was added."]
    cache = get_settings().broll_dir
    cache.mkdir(parents=True, exist_ok=True)
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for term in [t.strip() for t in terms if t.strip()][:6]:
        if len(assets) >= limit:
            break
        if term.lower() in seen:
            continue
        seen.add(term.lower())
        asset = _video_asset(term, aspect, cache) or _photo_asset(term, aspect, cache)
        if asset:
            assets.append(asset)
    if not assets:
        warnings.append("No usable Pexels clips were found for the mentioned topics.")
    return assets, warnings


def write_credits(assets: list[dict[str, Any]], out: Path) -> None:
    lines = ["B-roll credits", "=============", ""]
    if not assets:
        lines.append("No third-party B-roll used.")
    for asset in assets:
        lines.append(f"- {asset.get('term')}: {asset.get('credit')} ({asset.get('source_url')})")
        lines.append(f"  License: {asset.get('license')}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def credits_text(assets: list[dict[str, Any]]) -> str:
    if not assets:
        return ""
    authors = []
    for asset in assets:
        name = asset.get("author") or "Pexels"
        if name not in authors:
            authors.append(name)
    return "B-roll: Pexels / " + ", ".join(authors[:3])
