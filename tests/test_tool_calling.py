"""Multi-turn tool-calling loop in `ProviderBackedAgent`, exercised against
fake providers/tools -- no live model or MCP server involved."""

import json

import pytest

from design_pipeline.agents import ProviderBackedAgent
from design_pipeline.models import AgentDefinition
from design_pipeline.providers.base import ProviderRequest, ProviderResponse, ToolCall, ToolSpec
from design_pipeline.runtime import DesignRuntime


class _FakeTool:
    def __init__(self):
        self.calls: list[dict] = []
        self.spec = ToolSpec(name="fake.tool", description="A fake tool.", parameters={"type": "object", "properties": {}})

    def execute(self, **arguments):
        self.calls.append(arguments)
        return {"ok": True}


class _FinalProvider:
    name = "fake"

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(text='{"output": "value"}', provider=self.name, model="fake-model")


class _OneToolCallProvider:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(provider=self.name, model="fake-model", tool_calls=[ToolCall(id="call-1", name="fake.tool", arguments={"x": 1})], history="turn-1")
        return ProviderResponse(text='{"output": "value"}', provider=self.name, model="fake-model")


class _AlwaysToolCallProvider:
    name = "fake"

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(provider=self.name, model="fake-model", tool_calls=[ToolCall(id="call-x", name="fake.tool", arguments={})], history="turn")


def _definition() -> AgentDefinition:
    return AgentDefinition(id="test-agent", description="Test agent", inputs=[], outputs=["output"], tools=["fake.tool"])


def test_final_response_without_tools_is_unchanged():
    agent = ProviderBackedAgent(_definition(), _FinalProvider())
    assert agent.run(["output"], {}) == {"output": "value"}


def test_tool_call_executes_and_the_loop_continues_to_a_final_answer():
    tool = _FakeTool()
    provider = _OneToolCallProvider()
    agent = ProviderBackedAgent(_definition(), provider, tools=[tool])
    result = agent.run(["output"], {})
    assert result == {"output": "value"}
    assert tool.calls == [{"x": 1}]
    assert provider.calls == 2


def test_runaway_tool_calls_hit_the_iteration_cap_instead_of_looping_forever():
    tool = _FakeTool()
    agent = ProviderBackedAgent(_definition(), _AlwaysToolCallProvider(), tools=[tool], max_tool_iterations=3)
    with pytest.raises(ValueError, match="exceeded the tool-call limit"):
        agent.run(["output"], {})


def test_unknown_tool_name_is_reported_back_instead_of_crashing():
    provider = _OneToolCallProvider()
    agent = ProviderBackedAgent(_definition(), provider, tools=[])  # no tools registered
    result = agent.run(["output"], {})
    assert result == {"output": "value"}
    assert provider.calls == 2


def test_last_tool_calls_records_name_arguments_and_result():
    tool = _FakeTool()
    agent = ProviderBackedAgent(_definition(), _OneToolCallProvider(), tools=[tool])
    agent.run(["output"], {})
    assert agent.last_tool_calls == [{"tool": "fake.tool", "arguments": {"x": 1}, "result": {"ok": True}}]


def test_diagrams_output_is_reconciled_from_tool_results_not_model_transcription(tmp_path, monkeypatch):
    """A model that retypes a tool's result instead of reusing it verbatim
    (wrong field names, or even subtly different content) must not corrupt
    the stored `diagrams` output -- the tool's own validated result wins."""
    monkeypatch.setenv("DESIGN_PIPELINE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    instance = DesignRuntime(tmp_path)
    instance.initialize("test-project")

    class FakeMermaidTool:
        spec = ToolSpec(name="mermaid.render", description="d", parameters={"type": "object", "properties": {}})

        def execute(self, **kwargs):
            return {"name": kwargs.get("name"), "mermaid_source": "flowchart TD\n  A --> B", "valid": True, "detail": "ok"}

    monkeypatch.setattr("design_pipeline.runtime.resolve_tools", lambda tool_ids, mermaid_api_key=None: [FakeMermaidTool()] if "mermaid.render" in tool_ids else [])

    call_count = [0]

    class FakeProvider:
        name = "openai"
        model = "test-model"

        def generate(self, request):
            call_count[0] += 1
            if call_count[0] == 1:
                return ProviderResponse(provider=self.name, model=self.model, tool_calls=[ToolCall(id="c1", name="mermaid.render", arguments={"name": "Flow", "mermaid_code": "garbled"})], history="turn-1")
            # The model restates the diagram with a *different*, wrong shape/content than the tool actually returned.
            return ProviderResponse(text=json.dumps({"architecture-model": {}, "diagrams": [{"name": "Flow", "mermaid_code": "totally different and wrong"}]}), provider=self.name, model=self.model)

    monkeypatch.setattr("design_pipeline.runtime.create_model_provider", lambda settings: FakeProvider())

    values, _ = instance._execute_agent("architecture-agent", ["architecture-model", "diagrams"], {})
    assert values["diagrams"] == [{"name": "Flow", "mermaid_source": "flowchart TD\n  A --> B", "valid": True, "detail": "ok"}]
