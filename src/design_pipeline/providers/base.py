"""Provider-neutral model adapter interfaces."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    """A tool a model may call, described in the provider-neutral shape both
    OpenAI's `function` tools and Anthropic's `tools` can be built from."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolCall(BaseModel):
    """A pending tool call a model produced instead of a final answer."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ProviderRequest(BaseModel):
    system_prompt: str = ""
    user_prompt: str = Field(min_length=1)
    model: str | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    tools: list[ToolSpec] = Field(default_factory=list)
    # Opaque, provider-specific conversation state for continuing a
    # tool-calling turn (OpenAI: a previous_response_id string; Anthropic: an
    # accumulated messages list). Absent on the first turn of a run.
    history: Any = None
    # Tool results being fed back in on a continuation turn, keyed by the
    # ToolCall.id they answer.
    tool_results: dict[str, str] = Field(default_factory=dict)
    # Declared output name -> {"kind": "object"|"array", "schema": <nested
    # dict>|None}, when the caller wants the provider to *enforce* (not
    # just describe in the prompt) the final JSON answer's shape. Providers
    # that support native structured output (Gemini's responseSchema,
    # OpenAI's text.format json_schema) wire this in; providers without an
    # equivalent (Anthropic) ignore it and fall back to prompt guidance plus
    # ProviderBackedAgent's post-hoc recovery. `schema`, built by
    # `ProviderBackedAgent._field_schema`, recurses all the way down through
    # nested pydantic models/lists/Optionals with real required properties
    # at every level -- without that, a model can satisfy an unconstrained
    # `{"type": "object"}` slot (or a nested one, one level down) with `{}`
    # and technically not be wrong (confirmed live, twice: mockup-page-patch,
    # then mockup-screen-addition once its nested `screen`/`page` objects
    # were still unconstrained at depth 1). `schema: None` means the
    # output's type is too complex to safely translate at all (a real union
    # of several types, self-reference, ...) -- top-level key enforcement
    # only in that case.
    response_object_keys: dict[str, dict[str, Any]] | None = None


class ProviderResponse(BaseModel):
    text: str = ""
    provider: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    history: Any = None

    @property
    def is_final(self) -> bool:
        return not self.tool_calls


class ModelProvider(Protocol):
    """Capability boundary implemented by OpenAI, Anthropic, Gemini, etc."""

    name: str

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Generate a response for a structured provider request.

        May return a non-final response carrying `tool_calls` instead of
        `text`; the caller executes them and calls `generate` again with
        `history`/`tool_results` populated to continue the same turn.
        """
        ...
