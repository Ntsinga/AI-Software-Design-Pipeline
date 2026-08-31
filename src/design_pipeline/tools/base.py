"""Contract every real tool implementation satisfies."""

from __future__ import annotations

from typing import Any, Protocol

from ..providers.base import ToolSpec


class Tool(Protocol):
    """A tool an agent can call. `spec` is what the model sees; `execute`
    is what actually runs when the model calls it."""

    spec: ToolSpec

    def execute(self, **arguments: Any) -> dict[str, Any]:
        """Run the tool and return a JSON-serializable result.

        Should not raise for an ordinary failure the model can react to
        (e.g. invalid Mermaid syntax) -- return a result describing the
        failure instead, so the model sees it and can retry. Raising is
        reserved for the tool being unusable at all (e.g. the remote MCP
        server is unreachable).
        """
        ...
