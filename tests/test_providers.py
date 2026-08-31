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
