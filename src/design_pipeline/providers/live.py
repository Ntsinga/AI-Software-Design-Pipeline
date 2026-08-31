"""Small HTTP adapters for the OpenAI Responses and Anthropic Messages APIs.

Both support an optional multi-turn tool-calling loop (see `agents.
ProviderBackedAgent`), but their continuation mechanics differ, which is why
`ProviderRequest.history` is deliberately opaque:
  - OpenAI's Responses API is stateful server-side: `history` is the prior
    response's id, and a continuation turn only sends the new tool outputs.
  - Anthropic's Messages API is stateless: `history` is the accumulated
    `messages` list, and a continuation turn resends it in full with the
    tool results appended.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..provider_config import ProviderConfigurationError, ProviderSettings
from .base import ProviderRequest, ProviderResponse, ToolCall


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

    def _post_json(self, url: str, headers: dict[str, str], body: dict[str, Any], label: str) -> dict[str, Any]:
        """POST and parse JSON, turning *any* failure -- a non-2xx status,
        unparseable JSON, or a transport-level failure (timeout, connection
        error, etc.) from the request itself -- into one `LiveProviderError`.
        The request call is deliberately inside this try block: `httpx`'s
        request-level exceptions (`httpx.TimeoutException` and friends) are
        `httpx.HTTPError` subclasses too, so leaving `.post()` outside the
        guard -- as earlier versions of this method did -- lets a slow
        network crash the caller with a raw, unhandled exception instead of
        this clean, retryable error.
        """
        try:
            response = self._client.post(url, headers=headers, json=body)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LiveProviderError(f"{label} request failed: {exc}") from exc

    @staticmethod
    def _usage(payload: dict[str, Any]) -> dict[str, int]:
        # Gemini reports usage under `usageMetadata` with its own camelCase
        # field names instead of OpenAI/Anthropic's `usage`; falling back to
        # the payload itself (rather than requiring a `usage` key) lets
        # `GeminiProvider` pass `usageMetadata` straight through.
        usage = payload.get("usage") or payload.get("usageMetadata") or payload
        aliases = {
            "input_tokens": ("input_tokens", "prompt_tokens", "promptTokenCount"),
            "output_tokens": ("output_tokens", "completion_tokens", "candidatesTokenCount"),
            "total_tokens": ("total_tokens", "totalTokenCount"),
        }
        return {name: int(next((usage[key] for key in keys if isinstance(usage.get(key), int)), 0)) for name, keys in aliases.items()}


class OpenAIResponsesProvider(_HTTPProvider):
    name = "openai"
    endpoint = "https://api.openai.com/v1/responses"

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        body: dict[str, Any] = {
            "model": request.model or self.model,
            "temperature": request.temperature,
            "store": False,
        }
        if request.tools:
            body["tools"] = [{"type": "function", "name": tool.name, "description": tool.description, "parameters": tool.parameters} for tool in request.tools]
        if request.history:
            body["previous_response_id"] = request.history
            body["input"] = [{"type": "function_call_output", "call_id": call_id, "output": output} for call_id, output in request.tool_results.items()]
        else:
            body["instructions"] = request.system_prompt or None
            body["input"] = request.user_prompt
        payload = self._post_json(self.endpoint, {"Authorization": f"Bearer {self.settings.api_key}"}, body, "OpenAI")

        output = payload.get("output", [])
        tool_calls = self._pending_tool_calls(output)
        model_name = payload.get("model", request.model or self.model)
        if tool_calls:
            return ProviderResponse(provider=self.name, model=model_name, usage=self._usage(payload), tool_calls=tool_calls, history=payload.get("id"))
        text = payload.get("output_text") or self._output_text(output)
        if not text:
            raise LiveProviderError("OpenAI response did not contain text output")
        return ProviderResponse(text=text, provider=self.name, model=model_name, usage=self._usage(payload), history=payload.get("id"))

    @staticmethod
    def _pending_tool_calls(output: list[dict[str, Any]]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for item in output:
            if item.get("type") != "function_call":
                continue
            try:
                arguments = json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(ToolCall(id=item.get("call_id") or item.get("id", ""), name=item.get("name", ""), arguments=arguments))
        return calls

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
        messages: list[dict[str, Any]] = list(request.history) if request.history else []
        if not messages:
            messages = [{"role": "user", "content": request.user_prompt}]
        elif request.tool_results:
            messages = messages + [{
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": call_id, "content": output} for call_id, output in request.tool_results.items()],
            }]
        body: dict[str, Any] = {
            "model": request.model or self.model,
            "max_tokens": self.settings.max_output_tokens,
            "system": request.system_prompt or None,
            "temperature": request.temperature,
            "messages": messages,
        }
        if request.tools:
            body["tools"] = [{"name": tool.name, "description": tool.description, "input_schema": tool.parameters} for tool in request.tools]
        payload = self._post_json(self.endpoint, {"x-api-key": self.settings.api_key or "", "anthropic-version": "2023-06-01"}, body, "Anthropic")

        content = payload.get("content", [])
        tool_calls = [ToolCall(id=block["id"], name=block["name"], arguments=block.get("input") or {}) for block in content if block.get("type") == "tool_use"]
        model_name = payload.get("model", request.model or self.model)
        if tool_calls:
            history = messages + [{"role": "assistant", "content": content}]
            return ProviderResponse(provider=self.name, model=model_name, usage=self._usage(payload), tool_calls=tool_calls, history=history)
        text = "\n".join(block["text"] for block in content if block.get("type") == "text" and isinstance(block.get("text"), str))
        if not text:
            raise LiveProviderError("Anthropic response did not contain text output")
        return ProviderResponse(text=text, provider=self.name, model=model_name, usage=self._usage(payload))


class GeminiProvider(_HTTPProvider):
    """Google's Gemini `generateContent` API.

    Stateless like Anthropic's Messages API, so `history` here is the
    accumulated `contents` list -- but Gemini's `functionResponse` turns
    match by function *name*, not a call id (Gemini doesn't hand back an id
    for a function call at all), so `history` also carries a `pending_calls`
    map from the synthetic call ids this adapter assigns back to their names.
    """

    name = "gemini"
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models"

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        state: dict[str, Any] = request.history or {}
        contents: list[dict[str, Any]] = list(state.get("contents", []))
        if not contents:
            contents = [{"role": "user", "parts": [{"text": request.user_prompt}]}]
        elif request.tool_results:
            pending_calls: dict[str, str] = state.get("pending_calls", {})
            contents = contents + [{
                "role": "user",
                "parts": [{"functionResponse": {"name": pending_calls.get(call_id, call_id), "response": {"result": output}}} for call_id, output in request.tool_results.items()],
            }]
        body: dict[str, Any] = {"contents": contents, "generationConfig": {"temperature": request.temperature}}
        if request.system_prompt:
            body["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}
        if request.tools:
            body["tools"] = [{"functionDeclarations": [{"name": tool.name, "description": tool.description, "parameters": tool.parameters} for tool in request.tools]}]

        model_name = request.model or self.model
        payload = self._post_json(f"{self.endpoint}/{model_name}:generateContent", {"x-goog-api-key": self.settings.api_key or ""}, body, "Gemini")

        parts = (((payload.get("candidates") or [{}])[0]).get("content") or {}).get("parts") or []
        tool_calls: list[ToolCall] = []
        pending_calls = {}
        for part in parts:
            call = part.get("functionCall")
            if not call:
                continue
            call_id = f"call-{len(tool_calls)}"
            tool_calls.append(ToolCall(id=call_id, name=call.get("name", ""), arguments=call.get("args") or {}))
            pending_calls[call_id] = call.get("name", "")
        usage = self._usage(payload)
        if tool_calls:
            history = {"contents": contents + [{"role": "model", "parts": parts}], "pending_calls": pending_calls}
            return ProviderResponse(provider=self.name, model=model_name, usage=usage, tool_calls=tool_calls, history=history)
        text = "\n".join(part["text"] for part in parts if isinstance(part.get("text"), str))
        if not text:
            raise LiveProviderError("Gemini response did not contain text output")
        return ProviderResponse(text=text, provider=self.name, model=model_name, usage=usage)


def create_model_provider(settings: ProviderSettings):
    if settings.provider == "openai":
        return OpenAIResponsesProvider(settings)
    if settings.provider == "anthropic":
        return AnthropicMessagesProvider(settings)
    if settings.provider == "gemini":
        return GeminiProvider(settings)
    raise ProviderConfigurationError("A live provider must be selected before creating a model adapter")
