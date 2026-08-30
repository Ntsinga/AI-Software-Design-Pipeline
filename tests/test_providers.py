import json

import httpx
import pytest

from design_pipeline.agents import AgentLoader, ProviderBackedAgent
from design_pipeline.provider_config import ProviderConfigurationError, ProviderSettings, load_provider_settings
from design_pipeline.providers import AnthropicMessagesProvider, OpenAIResponsesProvider, ProviderRequest, ProviderResponse


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


def test_provider_backed_agent_requires_declared_json_outputs(runtime):
    definition = AgentLoader(runtime.store.paths.agents).load("architecture-agent")

    class FakeProvider:
        name = "fake"

        def generate(self, request):
            return ProviderResponse(text='```json\n{"architecture-model": {"style": "layered"}}\n```', provider="fake", model="fake-model")

    values = ProviderBackedAgent(definition, FakeProvider()).run(["architecture-model"], {"system-model": {}})
    assert values["architecture-model"]["style"] == "layered"
