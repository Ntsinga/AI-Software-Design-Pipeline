"""MermaidRenderTool, with the MCP session boundary faked out -- no network."""

import pytest

pytest.importorskip("mcp")

from mcp import types

from design_pipeline.tools.mermaid import MermaidRenderTool


def _text_result(text: str, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=is_error)


def test_valid_diagram_returns_a_render_result(monkeypatch):
    tool = MermaidRenderTool()

    async def fake_call_tool(name, arguments, headers=None):
        assert name == "validate_and_render_mermaid_diagram"
        assert arguments["mermaidCode"] == "flowchart TD\n  A --> B"
        return _text_result("rendered ok")

    monkeypatch.setattr(tool, "_call_tool", fake_call_tool)
    result = tool.execute(name="Flow", diagram_type="flowchart", mermaid_code="flowchart TD\n  A --> B")
    assert result["valid"] is True
    assert result["mermaid_source"] == "flowchart TD\n  A --> B"
    assert "chart_url" not in result


def test_invalid_diagram_surfaces_the_error_without_raising(monkeypatch):
    tool = MermaidRenderTool()

    async def fake_call_tool(name, arguments, headers=None):
        return _text_result("syntax error: unexpected token", is_error=True)

    monkeypatch.setattr(tool, "_call_tool", fake_call_tool)
    result = tool.execute(name="Bad", diagram_type="flowchart", mermaid_code="not mermaid")
    assert result["valid"] is False
    assert "syntax error" in result["detail"]


def test_unreachable_server_raises_so_the_agent_loop_sees_a_clear_failure(monkeypatch):
    tool = MermaidRenderTool()

    async def fake_call_tool(name, arguments, headers=None):
        raise ConnectionError("boom")

    monkeypatch.setattr(tool, "_call_tool", fake_call_tool)
    with pytest.raises(RuntimeError, match="unreachable"):
        tool.execute(name="X", diagram_type="flowchart", mermaid_code="x")


def test_configured_api_key_persists_to_a_mermaid_chart_project(monkeypatch):
    tool = MermaidRenderTool(api_key="secret-token")
    calls: list[tuple] = []

    async def fake_call_tool(name, arguments, headers=None):
        calls.append((name, arguments, headers))
        if name == "validate_and_render_mermaid_diagram":
            return _text_result("rendered ok")
        if name == "list_mermaid_chart_projects":
            # Real shape: an object wrapping the array, not a bare list.
            return _text_result('{"success": true, "projects": [{"id": "project-1", "title": "Personal"}], "count": 1}')
        if name == "create_mermaid_chart_diagram":
            assert arguments["projectID"] == "project-1"
            assert headers == {"Authorization": "secret-token"}
            # Real shape: the URL is nested under diagram.editUrl.
            return _text_result('{"success": true, "diagram": {"documentID": "doc-1", "editUrl": "https://mermaidchart.com/d/abc"}}')
        raise AssertionError(f"unexpected tool call: {name}")

    monkeypatch.setattr(tool, "_call_tool", fake_call_tool)
    result = tool.execute(name="Flow", diagram_type="flowchart", mermaid_code="flowchart TD\n  A --> B")
    assert result["chart_url"] == "https://mermaidchart.com/d/abc"
    assert [call[0] for call in calls] == ["validate_and_render_mermaid_diagram", "list_mermaid_chart_projects", "create_mermaid_chart_diagram"]


def test_chart_storage_failure_does_not_break_the_core_render_result(monkeypatch):
    tool = MermaidRenderTool(api_key="secret-token")

    async def fake_call_tool(name, arguments, headers=None):
        if name == "validate_and_render_mermaid_diagram":
            return _text_result("rendered ok")
        raise ConnectionError("mermaid chart account service down")

    monkeypatch.setattr(tool, "_call_tool", fake_call_tool)
    result = tool.execute(name="Flow", diagram_type="flowchart", mermaid_code="flowchart TD\n  A --> B")
    assert result["valid"] is True
    assert "chart_url" not in result
    assert "chart_storage_error" in result
