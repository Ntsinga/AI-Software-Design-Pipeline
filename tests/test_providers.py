import json

import httpx
import pytest

from design_pipeline.agents import AgentLoader, ProviderBackedAgent
from design_pipeline.provider_config import ProviderConfigurationError, ProviderSettings, load_provider_settings
from design_pipeline.providers import AnthropicMessagesProvider, GeminiProvider, OpenAIResponsesProvider, ProviderRequest, ProviderResponse, ToolSpec


def test_project_env_selects_openai_without_exposing_key(tmp_path):
    (tmp_path / ".env").write_text("DESIGN_PIPELINE_PROVIDER=openai\nOPENAI_API_KEY=secret\nOPENAI_MODEL=test-model\n", encoding="utf-8")
    settings = load_provider_settings(tmp_path, environ={})
    assert settings.provider == "openai"
    assert settings.public_status() == {"provider": "openai", "model": "test-model", "mode": "live", "configured": True}
    assert "secret" not in str(settings.public_status())


def test_live_provider_requires_key_and_model():
    with pytest.raises(ProviderConfigurationError, match="API key"):
        OpenAIResponsesProvider(ProviderSettings(provider="openai", model="test-model"))
    with pytest.raises(ProviderConfigurationError, match="model"):
        AnthropicMessagesProvider(ProviderSettings(provider="anthropic", model="", api_key="secret"))


def test_openai_responses_adapter_uses_server_side_bearer_key():
    def responder(request):
        assert request.url == httpx.URL("https://api.openai.com/v1/responses")
        assert request.headers["authorization"] == "Bearer secret"
        assert json.loads(request.content)["model"] == "test-model"
        return httpx.Response(200, json={"model": "test-model", "output": [{"content": [{"type": "output_text", "text": "{\"result\": true}"}]}], "usage": {"input_tokens": 3, "output_tokens": 2}})

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = OpenAIResponsesProvider(ProviderSettings(provider="openai", model="test-model", api_key="secret"), client)
    result = provider.generate(ProviderRequest(user_prompt="hello"))
    assert result.text == '{"result": true}'
    assert result.usage["input_tokens"] == 3


def test_anthropic_messages_adapter_uses_messages_api_headers():
    def responder(request):
        assert request.url == httpx.URL("https://api.anthropic.com/v1/messages")
        assert request.headers["x-api-key"] == "secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        return httpx.Response(200, json={"model": "test-model", "content": [{"type": "text", "text": "{\"result\": true}"}], "usage": {"input_tokens": 3, "output_tokens": 2}})

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = AnthropicMessagesProvider(ProviderSettings(provider="anthropic", model="test-model", api_key="secret"), client)
    result = provider.generate(ProviderRequest(user_prompt="hello"))
    assert result.text == '{"result": true}'
    assert result.usage["output_tokens"] == 2


def test_project_env_selects_gemini_without_exposing_key(tmp_path):
    (tmp_path / ".env").write_text("DESIGN_PIPELINE_PROVIDER=gemini\nGEMINI_API_KEY=secret\nGEMINI_MODEL=test-model\n", encoding="utf-8")
    settings = load_provider_settings(tmp_path, environ={})
    assert settings.provider == "gemini"
    assert settings.public_status() == {"provider": "gemini", "model": "test-model", "mode": "live", "configured": True}
    assert "secret" not in str(settings.public_status())


def test_gemini_adapter_uses_header_api_key_and_url_shape():
    def responder(request):
        assert request.url == httpx.URL("https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent")
        assert request.headers["x-goog-api-key"] == "secret"
        body = json.loads(request.content)
        assert body["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
        return httpx.Response(200, json={
            "modelVersion": "test-model",
            "candidates": [{"content": {"role": "model", "parts": [{"text": "{\"result\": true}"}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 5},
        })

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = GeminiProvider(ProviderSettings(provider="gemini", model="test-model", api_key="secret"), client)
    result = provider.generate(ProviderRequest(user_prompt="hello"))
    assert result.text == '{"result": true}'
    assert result.usage == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}


def test_gemini_truncated_response_raises_a_clear_error_not_a_json_parse_error():
    """Without checking finishReason, a truncated response surfaces as
    "invalid JSON: unterminated string" -- a cryptic parse error nowhere
    near the actual cause. Confirmed live: mockup-screen-addition asking
    for up to two full HTML pages in one answer hit exactly this."""
    candidate = {
        "finishReason": "MAX_TOKENS",
        "content": {"parts": [{"text": '{"mockup-page-patch": {"html": "<html><body>tru'}]},
    }

    def responder(request):
        return httpx.Response(200, json={"candidates": [candidate]})

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = GeminiProvider(ProviderSettings(provider="gemini", model="test-model", api_key="secret", max_output_tokens=16000), client)
    from design_pipeline.providers import LiveProviderError
    with pytest.raises(LiveProviderError, match="truncated"):
        provider.generate(ProviderRequest(user_prompt="hello"))


def test_gemini_other_non_stop_finish_reason_also_raises_clearly():
    def responder(request):
        return httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]})

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = GeminiProvider(ProviderSettings(provider="gemini", model="test-model", api_key="secret"), client)
    from design_pipeline.providers import LiveProviderError
    with pytest.raises(LiveProviderError, match="SAFETY"):
        provider.generate(ProviderRequest(user_prompt="hello"))


def test_gemini_tool_call_round_trip_matches_by_name_not_id():
    """Gemini hands back no id for a function call -- only a name -- so the
    adapter's own synthetic call ids must still route the tool result back
    to the right `functionResponse.name` on the next turn."""
    calls = []

    def responder(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            assert body["tools"][0]["functionDeclarations"][0]["name"] == "fake.tool"
            return httpx.Response(200, json={
                "candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "fake.tool", "args": {"x": 1}}}]}}],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
            })
        # Second turn: the function response must carry the original function name.
        function_response_turn = body["contents"][-1]
        assert function_response_turn["role"] == "user"
        assert function_response_turn["parts"][0]["functionResponse"]["name"] == "fake.tool"
        return httpx.Response(200, json={
            "candidates": [{"content": {"role": "model", "parts": [{"text": "done"}]}}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "totalTokenCount": 3},
        })

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = GeminiProvider(ProviderSettings(provider="gemini", model="test-model", api_key="secret"), client)
    tool_spec = ToolSpec(name="fake.tool", description="A fake tool.", parameters={"type": "object", "properties": {}})

    first = provider.generate(ProviderRequest(user_prompt="hello", tools=[tool_spec]))
    assert first.tool_calls[0].name == "fake.tool"
    second = provider.generate(ProviderRequest(user_prompt="hello", tools=[tool_spec], history=first.history, tool_results={first.tool_calls[0].id: '{"ok": true}'}))
    assert second.text == "done"
    assert len(calls) == 2


def test_output_shape_recurses_through_nested_models_lists_and_optionals():
    """mockup-page-patch is flat (screen_id/html, both plain strings) --
    its fields are inlined directly. mockup-screen-addition nests two more
    models (screen, page) and an Optional third (updated_source_page) --
    all three must be inlined too, not just the top level, or a model can
    satisfy the unconstrained inner slots with `{}` the same way it did
    for mockup-page-patch before this went one level deep (confirmed live,
    twice). business-model has one `dict[str, list[str]]` field
    (responsibilities) that isn't translatable at all -- correctly forfeits
    the *whole* object to an unconstrained fallback rather than sending a
    schema that silently drops that one field. architecture-model has no
    entry in OUTPUT_SCHEMAS at all -- same fallback, simpler reason."""
    assert ProviderBackedAgent._output_shape("mockup-page-patch") == {
        "kind": "object",
        "schema": {"type": "object", "properties": {"screen_id": {"type": "string"}, "html": {"type": "string"}}, "required": ["screen_id", "html"]},
    }
    assert ProviderBackedAgent._output_shape("mockup-pages")["schema"] == ProviderBackedAgent._output_shape("mockup-page-patch")["schema"]
    assert ProviderBackedAgent._output_shape("mockup-pages")["kind"] == "array"

    addition_shape = ProviderBackedAgent._output_shape("mockup-screen-addition")
    assert addition_shape["kind"] == "object"
    properties = addition_shape["schema"]["properties"]
    assert set(properties) == {"screen", "page", "updated_source_page"}
    assert set(addition_shape["schema"]["required"]) == {"screen", "page"}  # updated_source_page is Optional
    assert properties["page"] == {"type": "object", "properties": {"screen_id": {"type": "string"}, "html": {"type": "string"}}, "required": ["screen_id", "html"]}
    assert properties["updated_source_page"] == properties["page"]  # Optional[MockupPage] unwraps to MockupPage's own shape
    assert set(properties["screen"]["properties"]) == {"id", "name", "purpose", "key_elements", "workflow_link", "workflow_id", "entity_id"}
    assert properties["screen"]["required"] == ["id", "name"]  # the rest have defaults
    assert properties["screen"]["properties"]["key_elements"] == {"type": "array", "items": {"type": "string"}}

    assert ProviderBackedAgent._output_shape("business-model") == {"kind": "object", "schema": None}
    assert ProviderBackedAgent._output_shape("architecture-model") == {"kind": "object", "schema": None}


def test_gemini_enforces_declared_keys_and_flat_fields_via_native_response_schema():
    """The fix for the two live bugs in sequence: (1) don't just describe
    the required top-level keys in the prompt -- have Gemini's API itself
    reject any other shape via responseSchema/responseMimeType; (2) for a
    flat, all-primitive output (mockup-page-patch's screen_id/html), inline
    its fields as *required* properties too -- an unconstrained
    `{"type": "OBJECT"}` can be satisfied by `{}`, which is exactly what
    Gemini did once (1) alone was fixed."""
    def responder(request):
        body = json.loads(request.content)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["responseSchema"] == {
            "type": "OBJECT",
            "properties": {"mockup-page-patch": {
                "type": "OBJECT",
                "properties": {"screen_id": {"type": "STRING"}, "html": {"type": "STRING"}},
                "required": ["screen_id", "html"],
            }},
            "required": ["mockup-page-patch"],
        }
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{\"mockup-page-patch\": {\"screen_id\": \"s1\", \"html\": \"<p>x</p>\"}}"}]}}]})

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = GeminiProvider(ProviderSettings(provider="gemini", model="test-model", api_key="secret"), client)
    shape = {"kind": "object", "schema": {"type": "object", "properties": {"screen_id": {"type": "string"}, "html": {"type": "string"}}, "required": ["screen_id", "html"]}}
    provider.generate(ProviderRequest(user_prompt="hello", response_object_keys={"mockup-page-patch": shape}))


def test_gemini_response_schema_falls_back_to_unconstrained_object_for_deep_outputs():
    """An output with no flat pydantic schema on file (architecture-model
    isn't in OUTPUT_SCHEMAS at all) still gets its top-level key enforced,
    just without inlined fields -- Gemini's schema subset doesn't reliably
    support the $defs/$ref a real nested schema would need."""
    def responder(request):
        body = json.loads(request.content)
        assert body["generationConfig"]["responseSchema"]["properties"]["architecture-model"] == {"type": "OBJECT"}
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{\"architecture-model\": {}}"}]}}]})

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = GeminiProvider(ProviderSettings(provider="gemini", model="test-model", api_key="secret"), client)
    shape = {"kind": "object", "schema": None}
    provider.generate(ProviderRequest(user_prompt="hello", response_object_keys={"architecture-model": shape}))


def test_gemini_response_schema_omitted_when_tools_are_present():
    """Gemini rejects responseSchema/responseMimeType together with
    function-calling tools in the same request -- tools must win."""
    def responder(request):
        body = json.loads(request.content)
        assert "responseSchema" not in body["generationConfig"]
        assert "responseMimeType" not in body["generationConfig"]
        assert "tools" in body
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = GeminiProvider(ProviderSettings(provider="gemini", model="test-model", api_key="secret"), client)
    tool_spec = ToolSpec(name="fake.tool", description="A fake tool.", parameters={"type": "object", "properties": {}})
    shape = {"kind": "object", "schema": None}
    provider.generate(ProviderRequest(user_prompt="hello", tools=[tool_spec], response_object_keys={"architecture-model": shape}))


def test_openai_enforces_declared_keys_and_flat_fields_via_native_json_schema():
    def responder(request):
        body = json.loads(request.content)
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is False
        assert body["text"]["format"]["schema"]["required"] == ["mockup-pages"]
        assert body["text"]["format"]["schema"]["properties"]["mockup-pages"] == {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"screen_id": {"type": "string"}, "html": {"type": "string"}},
                "required": ["screen_id", "html"],
            },
        }
        return httpx.Response(200, json={"model": "test-model", "output_text": "{\"mockup-pages\": []}"})

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = OpenAIResponsesProvider(ProviderSettings(provider="openai", model="test-model", api_key="secret"), client)
    shape = {"kind": "array", "schema": {"type": "object", "properties": {"screen_id": {"type": "string"}, "html": {"type": "string"}}, "required": ["screen_id", "html"]}}
    provider.generate(ProviderRequest(user_prompt="hello", response_object_keys={"mockup-pages": shape}))


def test_transport_level_failure_becomes_a_clean_live_provider_error():
    """A timeout/connection error from the request itself -- not just a
    non-2xx response -- must also become `LiveProviderError`, not an
    unhandled `httpx` exception. `.post()` used to sit outside the guard."""
    from design_pipeline.providers import LiveProviderError

    def responder(request):
        raise httpx.WriteTimeout("write timed out", request=request)

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = OpenAIResponsesProvider(ProviderSettings(provider="openai", model="test-model", api_key="secret"), client)
    with pytest.raises(LiveProviderError, match="OpenAI request failed"):
        provider.generate(ProviderRequest(user_prompt="hello"))


def test_provider_backed_agent_sends_the_exact_schema_for_known_outputs(runtime):
    """Without this, a live model has no way to know what shape e.g.
    'system-model' needs and reliably invents its own -- observed live:
    a model returned {technology_stack, database_schema_design, ...}
    instead of anything matching SystemModel's actual fields."""
    definition = AgentLoader(runtime.store.paths.agents).load("requirements-agent")
    captured = {}

    class CapturingProvider:
        name = "fake"

        def generate(self, request):
            captured["payload"] = json.loads(request.user_prompt)
            return ProviderResponse(text=json.dumps({"business-model": {"actors": []}, "solution-model": {"capabilities": []}, "system-model": {"requirements": []}}), provider="fake", model="fake-model")

    ProviderBackedAgent(definition, CapturingProvider()).run(["business-model", "solution-model", "system-model"], {"brd": "text"})
    schemas = captured["payload"]["output_schemas"]
    assert set(schemas) == {"business-model", "solution-model", "system-model"}
    assert set(schemas["system-model"]["properties"]) == {"requirements", "business_capabilities", "business_workflows", "system_capabilities", "entities", "services", "screens", "integrations", "permissions", "traceability"}


def test_schema_hint_covers_list_shaped_outputs_too(runtime):
    """mockup-pages is a list of objects, not a single object -- the schema
    hint must handle that the same way as the single-object outputs."""
    definition = AgentLoader(runtime.store.paths.agents).load("ux-agent")
    captured = {}

    class CapturingProvider:
        name = "fake"

        def generate(self, request):
            captured["payload"] = json.loads(request.user_prompt)
            return ProviderResponse(text=json.dumps({"mockup-pages": [{"screen_id": "a", "html": "<p>x</p>"}]}), provider="fake", model="fake-model")

    ProviderBackedAgent(definition, CapturingProvider()).run(["mockup-pages"], {})
    schema = captured["payload"]["output_schemas"]["mockup-pages"]
    assert schema["type"] == "array"
    item_schema = schema["$defs"][schema["items"]["$ref"].rsplit("/", 1)[-1]]
    assert set(item_schema["properties"]) == {"screen_id", "html"}


def test_provider_backed_agent_requires_declared_json_outputs(runtime):
    definition = AgentLoader(runtime.store.paths.agents).load("architecture-agent")

    class FakeProvider:
        name = "fake"

        def generate(self, request):
            return ProviderResponse(text='```json\n{"architecture-model": {"style": "layered"}}\n```', provider="fake", model="fake-model")

    values = ProviderBackedAgent(definition, FakeProvider()).run(["architecture-model"], {"system-model": {}})
    assert values["architecture-model"]["style"] == "layered"


class _StaticTextProvider:
    """Minimal provider whose response body is fixed at construction, for
    exercising ProviderBackedAgent's key-recovery on a single declared
    output (retry_screen's mockup-page-patch is its only real caller)."""

    name = "fake"

    def __init__(self, text):
        self.text = text

    def generate(self, request):
        return ProviderResponse(text=self.text, provider="fake", model="fake-model")


def test_recovers_a_hyphenated_output_key_the_model_renamed(runtime):
    """Hyphens aren't valid identifiers in most languages, so a model
    sometimes silently normalizes `mockup-page-patch` to snake_case or
    camelCase while otherwise answering correctly."""
    definition = AgentLoader(runtime.store.paths.agents).load("ux-agent")
    provider = _StaticTextProvider(json.dumps({"mockup_page_patch": {"screen_id": "s1", "html": "<p>x</p>"}}))
    values = ProviderBackedAgent(definition, provider).run(["mockup-page-patch"], {})
    assert values == {"mockup-page-patch": {"screen_id": "s1", "html": "<p>x</p>"}}


def test_recovers_output_wrapped_under_an_unrelated_single_key(runtime):
    """A model may wrap its one answer under some other name entirely
    (e.g. "patch", "result") instead of the declared output name."""
    definition = AgentLoader(runtime.store.paths.agents).load("ux-agent")
    provider = _StaticTextProvider(json.dumps({"patch": {"screen_id": "s1", "html": "<p>x</p>"}}))
    values = ProviderBackedAgent(definition, provider).run(["mockup-page-patch"], {})
    assert values == {"mockup-page-patch": {"screen_id": "s1", "html": "<p>x</p>"}}


def test_recovers_output_nested_under_an_explanatory_wrapper(runtime):
    """A chattier model may nest the real answer under a conversational
    wrapper key instead of naming the declared output at the top level."""
    definition = AgentLoader(runtime.store.paths.agents).load("ux-agent")
    provider = _StaticTextProvider(json.dumps({"response": {"mockup-page-patch": {"screen_id": "s1", "html": "<p>x</p>"}}}))
    values = ProviderBackedAgent(definition, provider).run(["mockup-page-patch"], {})
    assert values == {"mockup-page-patch": {"screen_id": "s1", "html": "<p>x</p>"}}


def test_recovers_output_flattened_at_the_top_level_with_no_wrapper(runtime):
    """Confirmed live against Gemini flash-lite: for a single-output
    request, the model sometimes skips the wrapper key entirely and
    returns the declared output's own schema fields flattened at the top
    level -- {"screen_id": ..., "html": ...} instead of
    {"mockup-page-patch": {"screen_id": ..., "html": ...}}."""
    definition = AgentLoader(runtime.store.paths.agents).load("ux-agent")
    provider = _StaticTextProvider(json.dumps({"screen_id": "s1", "html": "<p>x</p>"}))
    values = ProviderBackedAgent(definition, provider).run(["mockup-page-patch"], {})
    assert values == {"mockup-page-patch": {"screen_id": "s1", "html": "<p>x</p>"}}


def test_missing_output_error_includes_the_raw_response_for_diagnosis(runtime):
    """Without this, a "did not produce declared output(s)" error is a dead
    end -- there's no way to tell what the model actually returned without
    reproducing the call. Confirmed the hard way debugging this live.

    Uses two declared outputs (like the real architecture step, which
    co-generates architecture-model + diagrams) so neither of them can be
    single-output-recovered -- this exercises a genuine, unrecoverable
    mismatch, not one of the near-miss shapes the recovery above handles."""
    definition = AgentLoader(runtime.store.paths.agents).load("architecture-agent")
    provider = _StaticTextProvider(json.dumps({"unrelated-key": {"foo": "bar"}}))
    with pytest.raises(ValueError, match=r"unrelated-key.*foo.*bar") as excinfo:
        ProviderBackedAgent(definition, provider).run(["architecture-model", "diagrams"], {})
    assert "did not produce declared output(s): architecture-model, diagrams" in str(excinfo.value)
