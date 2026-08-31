"""`erd.render_erd_mermaid` -- deterministic ERD generation from a
data-model artifact's structured content."""

from design_pipeline.erd import render_erd_mermaid


def test_render_erd_mermaid_basic_entity_and_relationship():
    data_model = {
        "entities": [
            {"name": "audit_plan", "fields": [{"name": "year", "type": "integer"}, {"name": "status", "type": "string"}]},
            {"name": "audit_unit", "fields": [{"name": "name", "type": "string"}]},
        ],
        "relationships": [
            {"from_entity": "audit_plan", "to_entity": "audit_unit", "cardinality": "one-to-many", "label": "contains"},
        ],
    }
    mermaid = render_erd_mermaid(data_model)
    assert mermaid.startswith("erDiagram")
    assert "audit_plan {" in mermaid
    assert "integer year" in mermaid
    assert "string status" in mermaid
    assert "audit_unit {" in mermaid
    assert 'audit_plan ||--o{ audit_unit : "contains"' in mermaid


def test_render_erd_mermaid_cardinality_symbols():
    for cardinality, symbol in [("one-to-one", "||--||"), ("one-to-many", "||--o{"), ("many-to-many", "}o--o{")]:
        data_model = {"entities": [{"name": "a"}, {"name": "b"}], "relationships": [{"from_entity": "a", "to_entity": "b", "cardinality": cardinality}]}
        mermaid = render_erd_mermaid(data_model)
        assert symbol in mermaid


def test_render_erd_mermaid_entity_with_no_fields_is_still_included():
    mermaid = render_erd_mermaid({"entities": [{"name": "bare_entity", "fields": []}], "relationships": []})
    assert "bare_entity" in mermaid
    assert "{" not in mermaid  # no attribute block for a fieldless entity


def test_render_erd_mermaid_sanitizes_unsafe_tokens():
    data_model = {"entities": [{"name": "Audit Plan / Unit", "fields": [{"name": "field with spaces", "type": "enum(a, b)"}]}], "relationships": []}
    mermaid = render_erd_mermaid(data_model)
    # No raw spaces or parens leak into unquoted erDiagram attribute tokens.
    for line in mermaid.splitlines():
        if line.strip().startswith("erDiagram") or line.strip() in ("}",) or line.strip().endswith("{"):
            continue
        assert "(" not in line and ")" not in line


def test_render_erd_mermaid_quotes_relationship_labels():
    data_model = {"entities": [{"name": "a"}, {"name": "b"}], "relationships": [{"from_entity": "a", "to_entity": "b", "label": "has many"}]}
    mermaid = render_erd_mermaid(data_model)
    assert '"has many"' in mermaid


def test_render_erd_mermaid_skips_relationships_with_missing_endpoints():
    data_model = {"entities": [{"name": "a"}], "relationships": [{"from_entity": "", "to_entity": "a"}]}
    mermaid = render_erd_mermaid(data_model)
    assert "-->" not in mermaid and "--" not in mermaid.replace("erDiagram", "")


def test_render_erd_mermaid_empty_model():
    assert render_erd_mermaid({}) == "erDiagram"
    assert render_erd_mermaid({"entities": [], "relationships": []}) == "erDiagram"
