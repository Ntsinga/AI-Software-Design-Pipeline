import json

import httpx
import pytest

from design_pipeline.agents import AgentLoader, ProviderBackedAgent
from design_pipeline.provider_config import ProviderConfigurationError, ProviderSettings, load_provider_settings, update_model, update_provider
from design_pipeline.providers import AnthropicMessagesProvider, GeminiProvider, OpenAIResponsesProvider, ProviderRequest, ProviderResponse, ToolSpec


def test_project_env_selects_openai_without_exposing_key(tmp_path):
    (tmp_path / ".env").write_text("DESIGN_PIPELINE_PROVIDER=openai\nOPENAI_API_KEY=secret\nOPENAI_MODEL=test-model\n", encoding="utf-8")
    settings = load_provider_settings(tmp_path, environ={})
    assert settings.provider == "openai"
    assert settings.public_status() == {"provider": "openai", "model": "test-model", "mode": "live", "configured": True}
    assert "secret" not in str(settings.public_status())


def test_update_provider_and_model_write_settings_yaml_not_env(tmp_path):
    """The live provider/model toggle is app-managed state, not a secret --
    it belongs in .design/settings.yaml, never rewritten into .env."""
    (tmp_path / ".env").write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
    update_provider(tmp_path, "openai")
    update_model(tmp_path, "openai", "gpt-5.4-nano")
    settings = load_provider_settings(tmp_path, environ={})
    assert settings.provider == "openai"
    assert settings.model == "gpt-5.4-nano"
    # .env is untouched -- only the API key line remains.
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "OPENAI_API_KEY=secret\n"
    settings_yaml = (tmp_path / ".design" / "settings.yaml").read_text(encoding="utf-8")
    assert "provider: openai" in settings_yaml
    assert "gpt-5.4-nano" in settings_yaml


def test_each_provider_remembers_its_own_model_independently(tmp_path):
    update_provider(tmp_path, "openai")
    update_model(tmp_path, "openai", "gpt-5.4-nano")
    update_model(tmp_path, "anthropic", "claude-sonnet-5")
    update_model(tmp_path, "gemini", "gemini-3.5-flash-lite")

    assert load_provider_settings(tmp_path, environ={}).model == "gpt-5.4-nano"
    update_provider(tmp_path, "anthropic")
    assert load_provider_settings(tmp_path, environ={}).model == "claude-sonnet-5"
    update_provider(tmp_path, "gemini")
    assert load_provider_settings(tmp_path, environ={}).model == "gemini-3.5-flash-lite"
    # Switching back to openai still has its own model, unaffected by the
    # other two switches in between.
    update_provider(tmp_path, "openai")
    assert load_provider_settings(tmp_path, environ={}).model == "gpt-5.4-nano"


def test_legacy_env_provider_and_model_migrate_once_into_settings_yaml(tmp_path):
    """Anyone upgrading from before .design/settings.yaml existed shouldn't
    have their already-configured provider/model silently reset to stub."""
    (tmp_path / ".env").write_text(
        "DESIGN_PIPELINE_PROVIDER=gemini\nGEMINI_API_KEY=secret\nGEMINI_MODEL=gemini-3.5-flash-lite\nOPENAI_MODEL=gpt-5.4-nano\n",
        encoding="utf-8",
    )
    settings = load_provider_settings(tmp_path, environ={})
    assert settings.provider == "gemini"
    assert settings.model == "gemini-3.5-flash-lite"
    settings_yaml_path = tmp_path / ".design" / "settings.yaml"
    assert settings_yaml_path.exists()
    assert "gpt-5.4-nano" in settings_yaml_path.read_text(encoding="utf-8")  # openai's model also carried over

    # The migration only ever runs once: editing .env afterward has no
    # effect -- .design/settings.yaml is now the sole source of truth.
    (tmp_path / ".env").write_text("DESIGN_PIPELINE_PROVIDER=openai\nOPENAI_API_KEY=secret\n", encoding="utf-8")
    assert load_provider_settings(tmp_path, environ={}).provider == "gemini"


def test_real_env_var_still_overrides_settings_yaml(tmp_path):
    """A true process environment variable (e.g. set by the hosting platform)
    still wins over the toggled selection -- only the .env FILE stopped
    participating, not a real env var."""
    update_provider(tmp_path, "openai")
    update_model(tmp_path, "openai", "gpt-5.4-nano")
    settings = load_provider_settings(tmp_path, environ={"DESIGN_PIPELINE_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "secret", "ANTHROPIC_MODEL": "claude-opus-5"})
    assert settings.provider == "anthropic"
    assert settings.model == "claude-opus-5"


def test_update_model_rejects_stub_and_empty_model(tmp_path):
    with pytest.raises(ProviderConfigurationError):
        update_model(tmp_path, "stub", "whatever")
    with pytest.raises(ProviderConfigurationError):
        update_model(tmp_path, "openai", "   ")


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


def test_anthropic_enforces_declared_shape_via_a_forced_tool_call():
    """Anthropic has no responseSchema/text.format equivalent -- previously
    this meant response_object_keys was silently ignored for Claude
    entirely, weaker enforcement than Gemini and OpenAI already had. The
    fix: define one tool whose input_schema is the declared shape and force
    it via tool_choice; the forced call's arguments come back as `.text`
    (a JSON string), not as a real pending tool_calls the caller has to
    execute and continue -- from ProviderBackedAgent's side this must look
    exactly like Gemini/OpenAI's own final, non-tool-calls response."""
    def responder(request):
        body = json.loads(request.content)
        assert body["tools"] == [{
            "name": "emit_output",
            "description": "Call this with your final answer, matching the given schema exactly.",
            "input_schema": {
                "type": "object",
                "properties": {"mockup-page-patch": {
                    "type": "object",
                    "properties": {"screen_id": {"type": "string"}, "html": {"type": "string"}},
                    "required": ["screen_id", "html"],
                }},
                "required": ["mockup-page-patch"],
            },
        }]
        assert body["tool_choice"] == {"type": "tool", "name": "emit_output"}
        return httpx.Response(200, json={
            "model": "test-model",
            "content": [{"type": "tool_use", "id": "call1", "name": "emit_output", "input": {"mockup-page-patch": {"screen_id": "s1", "html": "<p>x</p>"}}}],
            "usage": {"input_tokens": 3, "output_tokens": 2},
        })

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = AnthropicMessagesProvider(ProviderSettings(provider="anthropic", model="test-model", api_key="secret"), client)
    shape = {"kind": "object", "schema": {"type": "object", "properties": {"screen_id": {"type": "string"}, "html": {"type": "string"}}, "required": ["screen_id", "html"]}}
    result = provider.generate(ProviderRequest(user_prompt="hello", response_object_keys={"mockup-page-patch": shape}))
    assert result.is_final  # not exposed as tool_calls -- agents.py must not try to "execute" our own synthetic tool
    assert json.loads(result.text) == {"mockup-page-patch": {"screen_id": "s1", "html": "<p>x</p>"}}


def test_anthropic_structured_output_omitted_when_tools_are_present():
    """Same mutual-exclusivity rule as Gemini/OpenAI: a real tool-calling
    turn (e.g. architecture-agent's mermaid.render) must win over forcing
    the synthetic emit_output tool -- agents.py never sets both at once,
    but the provider shouldn't silently do the wrong thing if it did."""
    def responder(request):
        body = json.loads(request.content)
        assert [tool["name"] for tool in body["tools"]] == ["real.tool"]
        assert "tool_choice" not in body
        return httpx.Response(200, json={"model": "test-model", "content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 1, "output_tokens": 1}})

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = AnthropicMessagesProvider(ProviderSettings(provider="anthropic", model="test-model", api_key="secret"), client)
    tool_spec = ToolSpec(name="real.tool", description="d", parameters={"type": "object", "properties": {}})
    shape = {"kind": "object", "schema": None}
    provider.generate(ProviderRequest(user_prompt="hello", tools=[tool_spec], response_object_keys={"business-model": shape}))


def test_anthropic_raises_a_clear_error_if_the_forced_tool_is_not_called():
    def responder(request):
        return httpx.Response(200, json={"model": "test-model", "content": [{"type": "text", "text": "I'd rather not"}], "usage": {"input_tokens": 1, "output_tokens": 1}})

    client = httpx.Client(transport=httpx.MockTransport(responder))
    provider = AnthropicMessagesProvider(ProviderSettings(provider="anthropic", model="test-model", api_key="secret"), client)
    from design_pipeline.providers import LiveProviderError
    shape = {"kind": "object", "schema": None}
    with pytest.raises(LiveProviderError, match="emit_output"):
        provider.generate(ProviderRequest(user_prompt="hello", response_object_keys={"business-model": shape}))


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
    (responsibilities) that isn't translatable at all (no provider's
    structured-output dialect safely describes "arbitrary string keys" --
    OpenAI's strict mode rejects additionalProperties with a real schema
    outright). That field alone now falls back to an unconstrained object;
    every OTHER field still gets its real schema, and -- being
    business-model's own top-level fields -- all of them are required
    regardless of Python's own field.is_required(). This is the actual fix
    for a real production incident: the OLD "one untranslatable field
    forfeits the whole object" behavior discarded every field's
    constraint, and with nothing left in the schema at all, a live model
    kept returning a bare `{}` for business-model/solution-model/
    system-model, "generated" with no error. architecture-model has no
    entry in OUTPUT_SCHEMAS at all -- forfeits for the simpler reason that
    there's no schema to translate in the first place."""
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
    # workflow_id/entity_id are forced into `required` even though neither
    # has a Python-side is_required() (both default to "") and this object
    # isn't top_level: every screen needs at least one of them populated
    # per workflow_id_coverage/entity_crud_coverage in validators.py, and a
    # live model (Gemini, on a large real project) was confirmed leaving
    # both blank on obviously-CRUD screens when the schema didn't flag them
    # as expected at all. purpose/key_elements/workflow_link stay genuinely
    # optional -- only these two get the special-cased treatment.
    assert set(properties["screen"]["required"]) == {"id", "name", "workflow_id", "entity_id"}
    assert properties["screen"]["properties"]["key_elements"] == {"type": "array", "items": {"type": "string"}}

    business_shape = ProviderBackedAgent._output_shape("business-model")
    assert business_shape["kind"] == "object"
    business_properties = business_shape["schema"]["properties"]
    assert set(business_properties) == {
        "actors", "stakeholders", "capabilities", "goals", "processes", "rules",
        "outcomes", "responsibilities", "external_organizations", "events",
    }
    # Every field except the untranslatable dict one gets a real, typed
    # schema (not the permissive object fallback) --
    assert business_properties["actors"] == {"type": "array", "items": {"type": "string"}}
    assert business_properties["responsibilities"] == {"type": "object", "properties": {}, "required": []}
    # -- and ALL of them, including responsibilities, are required: this is
    # what actually stops an empty `{}` from validating. Before this fix,
    # `required` was `[]` for every field here (all nine had Python-side
    # defaults) even on the rare occasion the whole object wasn't `None`.
    assert set(business_shape["schema"]["required"]) == set(business_properties)

    assert ProviderBackedAgent._output_shape("architecture-model") == {"kind": "object", "schema": None}


def test_brd_output_shape_is_a_plain_string_not_an_unconstrained_object():
    """brd has no OUTPUT_SCHEMAS entry (it's markdown prose -- see
    DeterministicAgent._requirements, which assigns it a raw string), but
    _output_shape's schema-less fallback defaults to "unconstrained
    object" -- telling a live model, via its own native structured-output
    enforcement, that brd's value MUST be a JSON object was a real,
    confirmed-live bug: asked for prose but constrained to an object type,
    models kept resolving the conflict as an empty `{}`."""
    assert ProviderBackedAgent._output_shape("brd") == {"kind": "object", "schema": {"type": "string"}}


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
