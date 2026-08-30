"""Provider-neutral model adapter interfaces."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class ProviderRequest(BaseModel):
    system_prompt: str = ""
    user_prompt: str = Field(min_length=1)
    model: str | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class ProviderResponse(BaseModel):
    text: str
    provider: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)


class ModelProvider(Protocol):
    """Capability boundary implemented by OpenAI, Anthropic, Gemini, etc."""

    name: str

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Generate a response for a structured provider request."""
        ...
