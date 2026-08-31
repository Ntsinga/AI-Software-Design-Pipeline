"""Deterministic Mermaid erDiagram generation from a `data-model` artifact.

The ERD is always derived from `DataModel`'s structured content in code --
never independently authored by an LLM. Same "trust code over LLM
restatement" pattern already used for the `diagrams` reconciliation in
`runtime.DesignRuntime._execute_agent` (which rebuilds `diagrams` from the
`mermaid.render` tool's own validated results rather than the model's
JSON restatement of them): here there's no LLM restatement step at all,
so the ERD can never drift out of sync with the data it represents.
"""

from __future__ import annotations

import re
from typing import Any

_CARDINALITY_SYMBOLS = {
    "one-to-one": "||--||",
    "one-to-many": "||--o{",
    "many-to-many": "}o--o{",
}


def _mermaid_safe_token(value: str, fallback: str) -> str:
    """Mermaid erDiagram attribute tokens (type, name) can't contain
    whitespace or most punctuation unquoted. Collapse anything unsafe to
    underscores rather than trying to quote individual tokens (erDiagram
    attribute lines don't support quoting the way relationship labels
    do)."""
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", (value or "").strip()).strip("_")
    return cleaned or fallback


def render_erd_mermaid(data_model: dict[str, Any]) -> str:
    """Build Mermaid erDiagram syntax from a `data-model` artifact's
    content. Returns a minimal valid `erDiagram` block even when the
    model is empty (no entities yet)."""
    lines = ["erDiagram"]
    for entity in data_model.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        name = _mermaid_safe_token(entity.get("name", ""), "ENTITY")
        fields = entity.get("fields") or []
        if fields:
            lines.append(f"    {name} {{")
            for field in fields:
                if not isinstance(field, dict):
                    continue
                ftype = _mermaid_safe_token(field.get("type", "string"), "string")
                fname = _mermaid_safe_token(field.get("name", "field"), "field")
                lines.append(f"        {ftype} {fname}")
            lines.append("    }")
        else:
            # An entity with no fields still needs to appear in the
            # diagram -- Mermaid accepts a bare entity name with no
            # attribute block.
            lines.append(f"    {name}")

    for relationship in data_model.get("relationships") or []:
        if not isinstance(relationship, dict):
            continue
        from_entity = _mermaid_safe_token(relationship.get("from_entity", ""), "")
        to_entity = _mermaid_safe_token(relationship.get("to_entity", ""), "")
        if not from_entity or not to_entity:
            continue
        symbol = _CARDINALITY_SYMBOLS.get(relationship.get("cardinality", "one-to-many"), _CARDINALITY_SYMBOLS["one-to-many"])
        label = (relationship.get("label") or "relates to").replace('"', "'")
        lines.append(f'    {from_entity} {symbol} {to_entity} : "{label}"')

    return "\n".join(lines)
