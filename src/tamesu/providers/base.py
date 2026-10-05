from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..models import ContentPart, ImageGenerationRequest, ImageGenerationResponse, ProviderResponse


class Provider(Protocol):
    """Identity and credentials only; capabilities are separate protocols below."""

    name: str

    def credential_error(self) -> str | None: ...


@runtime_checkable
class TextGenerationProvider(Provider, Protocol):
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


@runtime_checkable
class ImageGenerationProvider(Provider, Protocol):
    def validate_image_parameters(
        self, model: str, request: ImageGenerationRequest
    ) -> list[str]:
        """Provider-specific parameter errors, checked before credentials or any paid call."""
        ...

    def generate_image(
        self, *, model: str, request: ImageGenerationRequest, timeout_seconds: int
    ) -> ImageGenerationResponse: ...


@runtime_checkable
class StructuredMultimodalProvider(Provider, Protocol):
    """Structured output over mixed text and image input; the basis for vision judges."""

    def generate_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        parts: tuple[ContentPart, ...],
        output_schema: dict[str, Any],
        parameters: dict[str, Any],
        timeout_seconds: int,
    ) -> ProviderResponse: ...
