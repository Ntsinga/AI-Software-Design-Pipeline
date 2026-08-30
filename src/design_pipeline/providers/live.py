"""Small HTTP adapters for the OpenAI Responses and Anthropic Messages APIs."""

from __future__ import annotations

from typing import Any

import httpx

from ..provider_config import ProviderConfigurationError, ProviderSettings
from .base import ProviderRequest, ProviderResponse


class LiveProviderError(RuntimeError):
    """A provider request failed or did not return usable text."""


class _HTTPProvider:
    name: str

    def __init__(self, settings: ProviderSettings, client: httpx.Client | None = None):
        if not settings.api_key:
            raise ProviderConfigurationError(f"{settings.provider} is selected but its API key is missing")
        if not settings.model:
            raise ProviderConfigurationError(f"{settings.provider} is selected but its model is missing")
        self.settings = settings
        self.model = settings.model
        self._client = client or httpx.Client(timeout=settings.timeout_seconds)

    @staticmethod
    def _usage(payload: dict[str, Any]) -> dict[str, int]:
        usage = payload.get("usage") or {}
        aliases = {
            "input_tokens": ("input_tokens", "prompt_tokens"),
            "output_tokens": ("output_tokens", "completion_tokens"),
            "total_tokens": ("total_tokens",),
        }
        return {name: int(next((usage[key] for key in keys if isinstance(usage.get(key), int)), 0)) for name, keys in aliases.items()}


class OpenAIResponsesProvider(_HTTPProvider):
    name = "openai"
    endpoint = "https://api.openai.com/v1/responses"

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        response = self._client.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
            json={
                "model": request.model or self.model,
                "instructions": request.system_prompt or None,
                "input": request.user_prompt,
                "temperature": request.temperature,
                "store": False,
            },
        )
        try:
            response.raise_for_status()
            payload = response.json()
            text = payload.get("output_text") or self._output_text(payload.get("output", []))
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise LiveProviderError(f"OpenAI request failed: {exc}") from exc
        if not text:
            raise LiveProviderError("OpenAI response did not contain text output")
        return ProviderResponse(text=text, provider=self.name, model=payload.get("model", request.model or self.model), usage=self._usage(payload))

    @staticmethod
    def _output_text(outputs: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for output in outputs:
            for content in output.get("content", []) or []:
                if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                    parts.append(content["text"])
        return "\n".join(parts)


class AnthropicMessagesProvider(_HTTPProvider):
    name = "anthropic"
    endpoint = "https://api.anthropic.com/v1/messages"

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        response = self._client.post(
            self.endpoint,
            headers={
                "x-api-key": self.settings.api_key or "",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": request.model or self.model,
                "max_tokens": self.settings.max_output_tokens,
                "system": request.system_prompt or None,
                "temperature": request.temperature,
                "messages": [{"role": "user", "content": request.user_prompt}],
            },
        )
        try:
            response.raise_for_status()
            payload = response.json()
            text = "\n".join(block["text"] for block in payload.get("content", []) if block.get("type") == "text" and isinstance(block.get("text"), str))
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise LiveProviderError(f"Anthropic request failed: {exc}") from exc
        if not text:
            raise LiveProviderError("Anthropic response did not contain text output")
        return ProviderResponse(text=text, provider=self.name, model=payload.get("model", request.model or self.model), usage=self._usage(payload))


def create_model_provider(settings: ProviderSettings):
    if settings.provider == "openai":
        return OpenAIResponsesProvider(settings)
    if settings.provider == "anthropic":
        return AnthropicMessagesProvider(settings)
    raise ProviderConfigurationError("A live provider must be selected before creating a model adapter")
