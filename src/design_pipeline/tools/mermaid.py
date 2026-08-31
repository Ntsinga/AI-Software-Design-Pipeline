"""Real Mermaid MCP tool: validates/renders diagrams, optionally persists
them to a Mermaid Chart account.

Two tiers, matching the Mermaid MCP server itself:
  - `validate_and_render_mermaid_diagram` needs no credentials at all. This
    is the core of the tool-calling loop -- an agent writes Mermaid syntax,
    calls this, and gets validated/rendered output (or an error to react
    to) back.
  - `create_mermaid_chart_diagram` (org-account storage) needs an API key.
    Purely additive: skipped when no key is configured, and never allowed
    to fail the core render result if it errors.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
from mcp import ClientSession, types
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from ..providers.base import ToolSpec

MERMAID_MCP_URL = "https://mcp.mermaid.ai/mcp"
CLIENT_NAME = "design-pipeline"


def _content_text(result: types.CallToolResult) -> str:
    return "\n".join(block.text for block in result.content if getattr(block, "type", None) == "text")


class MermaidRenderTool:
    spec = ToolSpec(
        name="mermaid.render",
        description=(
            "Validate and render a Mermaid diagram. Call this after writing Mermaid syntax and before "
            "finalizing your answer; if it reports invalid syntax, fix the diagram and call it again."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "A short human-readable name for this diagram."},
                "diagram_type": {"type": "string", "description": "e.g. flowchart, sequenceDiagram, classDiagram, gantt."},
                "mermaid_code": {"type": "string", "description": "The Mermaid diagram source to validate and render."},
            },
            "required": ["name", "diagram_type", "mermaid_code"],
        },
    )

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key
        self._project_id: str | None = None

    def execute(self, **arguments: Any) -> dict[str, Any]:
        return anyio.run(self._execute_async, arguments)

    async def _execute_async(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = arguments.get("name") or "diagram"
        diagram_type = arguments.get("diagram_type") or "flowchart"
        mermaid_code = arguments["mermaid_code"]
        try:
            render = await self._call_tool(
                "validate_and_render_mermaid_diagram",
                {"prompt": name, "mermaidCode": mermaid_code, "diagramType": diagram_type, "clientName": CLIENT_NAME},
            )
        except Exception as exc:
            raise RuntimeError(f"Mermaid MCP server unreachable: {exc}") from exc

        result: dict[str, Any] = {
            "name": name,
            "diagram_type": diagram_type,
            "mermaid_source": mermaid_code,
            "valid": not render.is_error,
            "detail": _content_text(render),
        }
        if render.is_error or not self._api_key:
            return result

        try:
            project_id = await self._resolve_project_id()
            stored = await self._call_tool(
                "create_mermaid_chart_diagram",
                {"projectID": project_id, "code": mermaid_code, "title": name, "clientName": CLIENT_NAME},
                headers={"Authorization": self._api_key},
            )
            result["chart_url"] = json.loads(_content_text(stored))["diagram"]["editUrl"]
        except Exception as exc:
            # Best-effort only: the diagram is already valid and is stored as
            # this artifact's own content regardless of this succeeding.
            result["chart_storage_error"] = str(exc)
        return result

    async def _resolve_project_id(self) -> str:
        if self._project_id is not None:
            return self._project_id
        listing = await self._call_tool("list_mermaid_chart_projects", {"clientName": CLIENT_NAME}, headers={"Authorization": self._api_key})
        payload = json.loads(_content_text(listing))
        projects = payload.get("projects", payload) if isinstance(payload, dict) else payload
        if not projects:
            raise RuntimeError("no Mermaid Chart projects found on this account")
        first = projects[0] if isinstance(projects, list) else projects
        self._project_id = first["id"]
        return self._project_id

    async def _call_tool(self, name: str, arguments: dict[str, Any], headers: dict[str, str] | None = None) -> types.CallToolResult:
        http_client = create_mcp_http_client(headers=headers) if headers else None
        async with streamable_http_client(MERMAID_MCP_URL, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, arguments)
