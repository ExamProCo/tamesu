from __future__ import annotations

from typing import Any, Protocol

from ..models import ProviderResponse


class Provider(Protocol):
    name: str

    def credential_error(self) -> str | None: ...

    def generate_text(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        output_schema: dict[str, Any],
        parameters: dict[str, Any],
        timeout_seconds: int,
    ) -> ProviderResponse: ...
