"""Provider-agnostic LLM client (OpenAI-compatible, Anthropic, Gemini)."""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings
from app.errors import AppError

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "openrouter": "openai/gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
    "gemini": "gemini-2.0-flash",
    "ollama": "llama3.1",
}


def resolve_provider() -> str:
    s = get_settings()
    provider = (s.llm_provider or "auto").lower()
    if provider == "auto":
        return "openai" if s.llm_api_key else "mock"
    return provider


def is_configured() -> bool:
    provider = resolve_provider()
    if provider == "mock":
        return False
    if provider in {"ollama"}:
        return True
    return bool(get_settings().llm_api_key)


def _model(provider: str) -> str:
    return get_settings().llm_model or DEFAULT_MODELS.get(provider, "gpt-4o-mini")


def _post(url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise AppError(
            "Could not reach the LLM provider.",
            code="llm_unreachable",
            hint=str(exc),
        ) from exc
    if response.status_code >= 400:
        detail = response.text[:600]
        raise AppError(
            f"The LLM provider returned HTTP {response.status_code}.",
            code="llm_http_error",
            hint=detail,
        )
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise AppError("The LLM provider returned a non-JSON response.", code="llm_bad_response") from exc


def chat(
    system: str,
    user: str,
    *,
    json_mode: bool = False,
    max_tokens: int = 4096,
    temperature: float = 0.4,
) -> str:
    settings = get_settings()
    provider = resolve_provider()
    model = _model(provider)

    if provider == "mock":
        raise AppError("No LLM is configured.", code="llm_not_configured")

    if provider in {"openai", "openrouter", "ollama", "custom"}:
        base = settings.llm_base_url
        if not base:
            base = {
                "openai": "https://api.openai.com/v1",
                "openrouter": "https://openrouter.ai/api/v1",
                "ollama": "http://localhost:11434/v1",
            }.get(provider, "")
        if not base:
            raise AppError("LLM_BASE_URL is required for a custom provider.", code="llm_config")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"
        data = _post(f"{base.rstrip('/')}/chat/completions", headers=headers, payload=payload)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise AppError("Unexpected LLM response shape.", code="llm_bad_response", hint=str(data)[:500]) from exc

    if provider == "anthropic":
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        data = _post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.llm_api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload=payload,
        )
        try:
            return "".join(block.get("text", "") for block in data["content"] if block.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise AppError("Unexpected Anthropic response.", code="llm_bad_response") from exc

    if provider == "gemini":
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                **({"responseMimeType": "application/json"} if json_mode else {}),
            },
        }
        data = _post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            f"?key={settings.llm_api_key}",
            headers={"Content-Type": "application/json"},
            payload=payload,
        )
        try:
            return "".join(
                part.get("text", "")
                for part in data["candidates"][0]["content"]["parts"]
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise AppError("Unexpected Gemini response.", code="llm_bad_response") from exc

    raise AppError(
        f"Unknown LLM provider '{provider}'.",
        code="llm_config",
        hint="Set LLM_PROVIDER to auto, openai, anthropic, gemini, openrouter, ollama or custom.",
    )


def parse_json(text: str) -> Any:
    """Parse JSON from a model response, tolerating code fences and prose."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0]
    if not starts:
        raise ValueError("No JSON found in model output")
    start = min(starts)
    opener = cleaned[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : index + 1])
    raise ValueError("Unbalanced JSON in model output")
