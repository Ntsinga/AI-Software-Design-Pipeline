"""Maps `AgentDefinition.tools` id strings (declared in agent YAML) to real
implementations. Only `mermaid.render` is implemented -- `artifact.read` /
`artifact.write`, also declared in `architecture.yaml`, stay inert, same as
before this module existed. Unknown ids are silently skipped rather than
erroring, so an agent YAML can list aspirational tools without breaking.
"""

from __future__ import annotations

from .base import Tool


def resolve_tools(tool_ids: list[str], *, mermaid_api_key: str | None = None) -> list[Tool]:
    resolved: list[Tool] = []
    for tool_id in tool_ids:
        if tool_id == "mermaid.render":
            from .mermaid import MermaidRenderTool

            resolved.append(MermaidRenderTool(api_key=mermaid_api_key))
    return resolved
