from __future__ import annotations

import json
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

from ..errors import ProviderError


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout_seconds: int,
    provider_name: str,
) -> tuple[dict[str, Any], Message]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "tamesu/0.1.0", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
            response_headers = response.headers
    except urllib.error.HTTPError as exc:
        request_id = _request_id(exc.headers)
        body = exc.read().decode("utf-8", errors="replace")
        message = _safe_error_message(body, exc.reason)
        retryable = exc.code in {408, 409, 424, 429} or 500 <= exc.code <= 599
        raise ProviderError(
            f"{provider_name} API error {exc.code}: {message}",
            retryable=retryable,
            status_code=exc.code,
            request_id=request_id,
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProviderError(f"{provider_name} request failed: {exc}", retryable=True) from exc

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"{provider_name} returned invalid JSON", retryable=True) from exc
    if not isinstance(document, dict):
        raise ProviderError(f"{provider_name} returned an unexpected response", retryable=True)
    return document, response_headers


def _request_id(headers: Message | None) -> str | None:
    if not headers:
        return None
    for name in ("x-request-id", "request-id", "x-amzn-requestid"):
        value = headers.get(name)
        if value:
            return value
    return None


def _safe_error_message(body: str, fallback: object) -> str:
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        return str(fallback)
    if isinstance(document, dict):
        error = document.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(document.get("message"), str):
            return document["message"]
    return str(fallback)
