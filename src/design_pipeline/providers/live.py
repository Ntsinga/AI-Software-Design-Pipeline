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


def _object_schema_for(shape: dict[str, Any], *, upper: bool) -> dict[str, Any]:
    """Translate one `ProviderRequest.response_object_keys` shape entry
    into a JSON Schema (OpenAI, `upper=False`) or Gemini's OpenAPI-subset
    schema (`upper=True`, whose type names are uppercase: OBJECT, STRING,
    ...). `shape["schema"]`, built by `ProviderBackedAgent._field_schema`,
    is already the full nested structure (real required properties at
    every level, not just the top) -- this just relabels each `type` value
    for the target provider; `None` anywhere in it becomes an unconstrained
    object, which is otherwise satisfiable by `{}` (confirmed live, twice)."""
    def type_name(name: str) -> str:
        return name.upper() if upper else name

    def translate(node: dict[str, Any] | None) -> dict[str, Any]:
        if node is None:
            return {"type": type_name("object")}
        if node["type"] == "object":
            return {
                "type": type_name("object"),
                "properties": {name: translate(sub) for name, sub in node["properties"].items()},
                "required": node["required"],
            }
        if node["type"] == "array":
            return {"type": type_name("array"), "items": translate(node["items"])}
        return {"type": type_name(node["type"])}

    inner = translate(shape["schema"])
    if shape["kind"] == "array":
        return {"type": type_name("array"), "items": inner}
    return inner


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
        except httpx.HTTPStatusError as exc:
            # A non-2xx's default str() only restates the status code and
            # URL -- identical whether the response really came from the
            # provider's own edge or from some intermediary along the way
            # (a corporate proxy, a CDN, a misconfigured gateway). That
            # ambiguity is otherwise unanswerable after the fact, once the
            # actual response is gone and only this message remains in the
            # artifact history. `Server`/`Via` are the headers most likely
            # to reveal an intermediary (Google's real edge reports
            # `Server: Google Frontend`, distinct from any proxy's own
            # software); the body snippet distinguishes the provider's own
            # JSON error shape from an intermediary's own error page.
            r = exc.response
            server = r.headers.get("server") or "n/a"
            via = r.headers.get("via") or "n/a"
            snippet = (r.text or "")[:200].replace("\n", " ") or "n/a"
            raise LiveProviderError(f"{label} request failed: {exc} [server={server}; via={via}; body={snippet}]") from exc
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
            "max_output_tokens": self.settings.max_output_tokens,
        }
        if request.tools:
            body["tools"] = [{"type": "function", "name": tool.name, "description": tool.description, "parameters": tool.parameters} for tool in request.tools]
        elif request.response_object_keys:
            # Same rationale as GeminiProvider's responseSchema branch --
            # enforce the declared shape natively rather than by prompt text
            # alone. `strict: False`: outputs whose `fields` are unknown
            # (deep/nested pydantic schemas) stay unconstrained objects with
            # no `properties`, and OpenAI's strict mode requires every
            # nested object to fully enumerate its properties with
            # `additionalProperties: false`.
            body["text"] = {"format": {
                "type": "json_schema",
                "name": "pipeline_output",
                "strict": False,
                "schema": {
                    "type": "object",
                    "properties": {name: _object_schema_for(shape, upper=False) for name, shape in request.response_object_keys.items()},
                    "required": list(request.response_object_keys),
                },
            }}
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
        forcing_structured_output = False
        if request.tools:
            body["tools"] = [{"name": tool.name, "description": tool.description, "input_schema": tool.parameters} for tool in request.tools]
        elif request.response_object_keys:
            # Anthropic has no OpenAI-`text.format`/Gemini-`responseSchema`
            # equivalent -- previously this branch did not exist at all, so
            # Claude got zero native enforcement, only prompt guidance plus
            # ProviderBackedAgent's post-hoc recovery (weaker than Gemini
            # and OpenAI already had). The standard workaround: define one
            # tool whose input_schema IS the desired output shape, then
            # force the model to call it via tool_choice -- Claude then has
            # to produce arguments matching the schema to call the tool at
            # all, the same real enforcement the other two providers get.
            forcing_structured_output = True
            body["tools"] = [{
                "name": "emit_output",
                "description": "Call this with your final answer, matching the given schema exactly.",
                "input_schema": {
                    "type": "object",
                    "properties": {name: _object_schema_for(shape, upper=False) for name, shape in request.response_object_keys.items()},
                    "required": list(request.response_object_keys),
                },
            }]
            body["tool_choice"] = {"type": "tool", "name": "emit_output"}
        payload = self._post_json(self.endpoint, {"x-api-key": self.settings.api_key or "", "anthropic-version": "2023-06-01"}, body, "Anthropic")

        content = payload.get("content", [])
        tool_calls = [ToolCall(id=block["id"], name=block["name"], arguments=block.get("input") or {}) for block in content if block.get("type") == "tool_use"]
        model_name = payload.get("model", request.model or self.model)
        if forcing_structured_output:
            # Not a real pending tool call the caller needs to execute and
            # continue -- unwrap the forced call's arguments straight into
            # `.text` as a JSON string, so this looks exactly like every
            # other provider's final, non-tool-calls structured-output
            # response to ProviderBackedAgent (which only ever expects a
            # JSON string in `.text`, never response_object_keys-shaped
            # tool_calls).
            if not tool_calls:
                raise LiveProviderError("Anthropic did not call the forced emit_output tool")
            return ProviderResponse(text=json.dumps(tool_calls[0].arguments), provider=self.name, model=model_name, usage=self._usage(payload))
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
        body: dict[str, Any] = {"contents": contents, "generationConfig": {"temperature": request.temperature, "maxOutputTokens": self.settings.max_output_tokens}}
        if request.system_prompt:
            body["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}
        if request.tools:
            body["tools"] = [{"functionDeclarations": [{"name": tool.name, "description": tool.description, "parameters": tool.parameters} for tool in request.tools]}]
        elif request.response_object_keys:
            # Enforce the declared top-level keys via Gemini's native
            # structured output instead of prompt text alone -- prompt-only
            # guidance is what let flash-lite rename/wrap/flatten a
            # hyphenated key like `mockup-page-patch` live. Deliberately
            # shallow (no nested field schemas): Gemini's schema subset
            # doesn't reliably support the $defs/$ref our full pydantic
            # schemas use, and only the top-level shape needs enforcing --
            # inner field correctness stays on prompt guidance. Mutually
            # exclusive with `tools` in Gemini's API, hence `elif`.
            body["generationConfig"]["responseMimeType"] = "application/json"
            body["generationConfig"]["responseSchema"] = {
                "type": "OBJECT",
                "properties": {name: _object_schema_for(shape, upper=True) for name, shape in request.response_object_keys.items()},
                "required": list(request.response_object_keys),
            }

        model_name = request.model or self.model
        payload = self._post_json(f"{self.endpoint}/{model_name}:generateContent", {"x-goog-api-key": self.settings.api_key or ""}, body, "Gemini")

        candidate = (payload.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        finish_reason = candidate.get("finishReason")
        if finish_reason == "MAX_TOKENS":
            # Otherwise this surfaces as "invalid JSON: unterminated
            # string" -- a cryptic parse error miles away from the actual
            # cause (the response got cut off mid-string before it could
            # finish). Confirmed live: mockup-screen-addition can ask for
            # up to two full HTML pages (page + updated_source_page) in one
            # answer, needing more headroom than a single-page patch does.
            raise LiveProviderError(
                f"Gemini's response was truncated at the {self.settings.max_output_tokens}-token output limit before "
                "it could finish -- raise DESIGN_PIPELINE_MAX_OUTPUT_TOKENS (or ask for something smaller, e.g. "
                "fewer/simpler screens at once) and try again."
            )
        if finish_reason and finish_reason not in ("STOP", "TOOL_CALLS"):
            raise LiveProviderError(f"Gemini stopped without a usable answer (finishReason: {finish_reason})")
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
