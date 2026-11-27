"""YouTube Data API upload with OAuth (optional feature)."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.errors import AppError

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
_pending_states: set[str] = set()


def _imports():
    try:
        from google.auth.transport.requests import Request  # noqa: F401
        from google.oauth2.credentials import Credentials  # noqa: F401
        from google_auth_oauthlib.flow import Flow  # noqa: F401
        from googleapiclient.discovery import build  # noqa: F401
        from googleapiclient.http import MediaFileUpload  # noqa: F401
    except ImportError as exc:
        raise AppError(
            "YouTube upload dependencies are not installed.",
            code="youtube_deps",
            hint="Run: pip install google-api-python-client google-auth-oauthlib",
        ) from exc


def _client_config() -> dict[str, Any]:
    s = get_settings()
    if s.yt_client_id and s.yt_client_secret:
        return {
            "installed": {
                "client_id": s.yt_client_id,
                "client_secret": s.yt_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [s.yt_redirect_uri],
            }
        }
    if s.yt_client_secrets and Path(s.yt_client_secrets).exists():
        return json.loads(Path(s.yt_client_secrets).read_text(encoding="utf-8"))
    raise AppError(
        "YouTube OAuth is not configured.",
        code="youtube_not_configured",
        hint="Download an OAuth client JSON from Google Cloud Console and set "
        "YT_CLIENT_SECRETS=/path/to/client_secret.json (or YT_CLIENT_ID / YT_CLIENT_SECRET).",
    )


def _flow():
    _imports()
    from google_auth_oauthlib.flow import Flow

    s = get_settings()
    return Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=s.yt_redirect_uri)


def is_authorized() -> bool:
    s = get_settings()
    path = Path(s.yt_token_file)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("refresh_token") or data.get("token"))


def status() -> dict[str, Any]:
    s = get_settings()
    return {
        "configured": s.yt_configured,
        "authorized": is_authorized(),
        "redirect_uri": s.yt_redirect_uri,
        "hint": (
            "Ready to upload."
            if s.yt_configured and is_authorized()
            else "Set YT_CLIENT_SECRETS, then connect your YouTube account."
        ),
    }


def auth_url() -> str:
    flow = _flow()
    state = secrets.token_urlsafe(16)
    _pending_states.add(state)
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return url


def exchange_code(code: str, state: str = "") -> None:
    if state and state not in _pending_states:
        raise AppError(
            "OAuth state did not match.",
            code="youtube_state",
            hint="Start the YouTube connection again.",
        )
    _pending_states.discard(state)
    flow = _flow()
    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            "YouTube authorization failed.",
            code="youtube_oauth",
            hint=str(exc),
        ) from exc
    credentials = flow.credentials
    token_path = Path(get_settings().yt_token_file)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")


def _credentials():
    _imports()
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = Path(get_settings().yt_token_file)
    if not path.exists():
        raise AppError(
            "YouTube is not connected.",
            code="youtube_not_authorized",
            hint="Click 'Connect YouTube' and approve access.",
        )
    credentials = Credentials.from_authorized_user_file(str(path), SCOPES)
    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            path.write_text(credentials.to_json(), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            raise AppError(
                "YouTube authorization expired.",
                code="youtube_refresh",
                hint=f"Reconnect your account. ({exc})",
            ) from exc
    return credentials


def _status(privacy: str, publish_at: str = "") -> dict[str, Any]:
    """Scheduled uploads must be private; YouTube flips them public at publishAt."""
    status: dict[str, Any] = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        when = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        status["privacyStatus"] = "private"
        status["publishAt"] = when.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return status


def upload(
    video_path: Path,
    title: str,
    description: str,
    privacy: str,
    publish_at: str = "",
) -> dict[str, Any]:
    _imports()
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    if not video_path.exists():
        raise AppError("The rendered clip file is missing.", code="file_missing")
    if "#shorts" not in description.lower():
        description = f"{description}\n\n#Shorts".strip()
    credentials = _credentials()
    try:
        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title[:100],
                    "description": description[:4900],
                    "tags": ["Shorts"],
                    "categoryId": "22",
                },
                "status": _status(privacy, publish_at),
            },
            media_body=MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024, resumable=True),
        )
        response = None
        while response is None:
            _, response = request.next_chunk()
        video_id = response.get("id", "")
        return {
            "video_id": video_id,
            "url": f"https://youtube.com/shorts/{video_id}" if video_id else "",
            "privacy": "scheduled" if publish_at else privacy,
            "scheduled_for": publish_at or "",
        }
    except HttpError as exc:
        detail = ""
        try:
            detail = json.loads(exc.content.decode("utf-8")).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            detail = str(exc)
        raise AppError(
            "YouTube rejected the upload.",
            code="youtube_http",
            hint=detail or str(exc),
        ) from exc
