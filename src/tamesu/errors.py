from typing import Any


class TamesuError(Exception):
    """Base class for user-facing Tamesu errors."""


class ConfigError(TamesuError):
    """A manifest or repository is invalid."""


class ExecutionError(TamesuError):
    """An evaluation cannot be executed safely."""


class ProviderError(TamesuError):
    """A provider request failed."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        request_id: str | None = None,
        usage: dict[str, Any] | None = None,
        cost_usd: float | None = None,
        response_metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.request_id = request_id
        self.usage = dict(usage or {})
        self.cost_usd = cost_usd
        self.response_metadata = dict(response_metadata or {})
