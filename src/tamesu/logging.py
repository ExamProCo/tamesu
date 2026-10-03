from __future__ import annotations

import json
import os
import re
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Iterable, Mapping


LOG_SCHEMA_VERSION = 1
REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "bearer_token",
    "client_secret",
    "cookie",
    "credential",
    "credentials",
    "password",
    "proxy_authorization",
    "refresh_token",
    "secret",
    "set_cookie",
    "signature",
    "token",
    "x_amz_credential",
    "x_amz_signature",
    "x_api_key",
    "x_goog_api_key",
}
_QUERY_SECRET_KEYS = {
    "api_key",
    "key",
    "access_token",
    "authorization",
    "credential",
    "password",
    "signature",
    "token",
    "x_amz_credential",
    "x_amz_signature",
    "x_goog_signature",
}
_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
_INLINE_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|authorization|password|client[_-]?secret)"
    r"([\"']?\s*[:=]\s*[\"']?)([^,\s\"'&}]+)"
)


class CallLogWriter:
    """Append versioned, redacted lifecycle events to one run's JSONL call log."""

    def __init__(self, path: Path, *, secret_values: Iterable[str] = ()) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._secret_values = tuple(
            sorted(
                {
                    *(_environment_secrets()),
                    *(value for value in secret_values if isinstance(value, str) and value),
                },
                key=len,
                reverse=True,
            )
        )

    def append(self, event: str, **fields: Any) -> None:
        if not event:
            raise ValueError("A call-log event name is required")
        if "at" not in fields or "run_id" not in fields:
            raise ValueError("Call-log events require at and run_id fields")
        if "schema_version" in fields or "event" in fields:
            raise ValueError("schema_version and event are managed by CallLogWriter")
        document = redact_value(
            {"schema_version": LOG_SCHEMA_VERSION, "event": event, **fields},
            secret_values=self._secret_values,
        )
        line = json.dumps(
            document,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def redact(self, value: Any) -> Any:
        return redact_value(value, secret_values=self._secret_values)

    def safe_text(self, value: object) -> str:
        return str(self.redact(str(value)))


def redact_value(value: Any, *, secret_values: Iterable[str] = ()) -> Any:
    secrets = tuple(
        sorted(
            {secret for secret in secret_values if isinstance(secret, str) and secret},
            key=len,
            reverse=True,
        )
    )
    return _redact(value, secrets)


def _redact(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            redacted[key] = REDACTED if _sensitive_key(key) else _redact(item, secrets)
        return redacted
    if isinstance(value, (list, tuple, set)):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        return _redact_text(value, secrets)
    return value


def _redact_text(value: str, secrets: tuple[str, ...]) -> str:
    redacted = value
    for secret in secrets:
        redacted = redacted.replace(secret, REDACTED)
    redacted = _INLINE_SECRET_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", redacted
    )
    return _URL_PATTERN.sub(lambda match: _redact_url(match.group(0)), redacted)


def _redact_url(value: str) -> str:
    trailing = ""
    while value and value[-1] in ".,);]":
        trailing = value[-1] + trailing
        value = value[:-1]
    try:
        parsed = urllib.parse.urlsplit(value)
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        return value + trailing
    if not pairs:
        return value + trailing
    query = urllib.parse.urlencode(
        [
            (key, REDACTED if _normalize_key(key) in _QUERY_SECRET_KEYS else item)
            for key, item in pairs
        ]
    )
    return urllib.parse.urlunsplit(parsed._replace(query=query)) + trailing


def _environment_secrets() -> set[str]:
    return {
        value
        for name, value in os.environ.items()
        if value and _sensitive_key(name) and len(value) >= 4
    }


def _sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in _SENSITIVE_KEYS:
        return True
    parts = set(normalized.split("_"))
    return bool(parts & {"authorization", "credential", "password", "secret"}) or (
        "token" in parts and "tokens" not in parts
    ) or normalized.endswith("_api_key")


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
