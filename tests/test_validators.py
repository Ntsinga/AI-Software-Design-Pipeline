"""Output validation with corrective retry in `ProviderBackedAgent`, plus
the specific `workflow_id_coverage` validator."""

import json

import pytest

from design_pipeline.agents import ProviderBackedAgent
from design_pipeline.models import AgentDefinition
from design_pipeline.providers.base import ProviderRequest, ProviderResponse
from design_pipeline.validators import _hierarchy_chain_components, data_model_relationships_reference_known_entities, entity_crud_coverage, no_raw_ids_rendered_in_html, workflow_id_coverage


def _definition() -> AgentDefinition:
    return AgentDefinition(id="test-agent", description="Test", inputs=[], outputs=["mockup-spec"])


class _ScriptedProvider:
    """Returns a predetermined sequence of ProviderResponses across turns."""

    name = "fake"

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[ProviderRequest] = []

    def generate(self, request):
        self.requests.append(request)
        return self._responses.pop(0)


def test_workflow_id_coverage_accepts_verbatim_ids_and_full_coverage():
    values = {"mockup-spec": {"screens": [
        {"id": "s1", "workflow_id": "__landing__"},
        {"id": "s2", "workflow_id": "flow_a"},
        {"id": "s3", "workflow_id": "flow_b"},
    ]}}
    inputs = {"architecture-model": {"workflows": [{"id": "flow_a"}, {"id": "flow_b"}]}}
    assert workflow_id_coverage(values, inputs) == []


def test_workflow_id_coverage_rejects_invented_slugs():
    values = {"mockup-spec": {"screens": [{"id": "s1", "workflow_id": "wf_a"}]}}
    inputs = {"architecture-model": {"workflows": [{"id": "audit_universe"}]}}
    errors = workflow_id_coverage(values, inputs)
    assert any("wf_a" in e and "not one of" in e for e in errors)


def test_workflow_id_coverage_rejects_missing_coverage():
    values = {"mockup-spec": {"screens": [
        {"id": "s1", "workflow_id": "flow_a"},
        {"id": "s2", "workflow_id": "__landing__"},
    ]}}
    inputs = {"architecture-model": {"workflows": [{"id": "flow_a"}, {"id": "flow_b"}, {"id": "flow_c"}]}}
    errors = workflow_id_coverage(values, inputs)
    assert any("flow_b" in e and "flow_c" in e for e in errors)


def test_data_model_relationships_reference_known_entities_accepts_valid_refs():
    values = {"data-model": {
        "entities": [{"name": "audit_plan"}, {"name": "audit_unit"}],
        "relationships": [{"from_entity": "audit_plan", "to_entity": "audit_unit", "cardinality": "one-to-many"}],
    }}
    assert data_model_relationships_reference_known_entities(values, {}) == []


def test_data_model_relationships_reference_known_entities_rejects_dangling_ref():
    values = {"data-model": {
        "entities": [{"name": "audit_plan"}],
        "relationships": [{"from_entity": "audit_plan", "to_entity": "audit_unit", "cardinality": "one-to-many"}],
    }}
    errors = data_model_relationships_reference_known_entities(values, {})
    assert any("audit_unit" in e and "to_entity" in e for e in errors)


def test_data_model_relationships_validator_is_noop_when_data_model_absent():
    assert data_model_relationships_reference_known_entities({}, {}) == []
    assert data_model_relationships_reference_known_entities({"architecture-model": {}}, {}) == []


def test_entity_crud_coverage_prefers_data_model_over_system_model():
    """Once a project has a data-model, its entities are authoritative --
    a stale system-model.entities list must not override it."""
    values = {"mockup-spec": {"screens": [
        {"id": "s1", "entity_id": "audit_plan"}, {"id": "s2", "entity_id": "audit_plan"},
    ]}}
    inputs = {
        "data-model": {"entities": [{"name": "audit_plan"}]},
        "system-model": {"entities": ["ia_finding"]},  # stale; must be ignored when data-model is present
    }
    assert entity_crud_coverage(values, inputs) == []


def test_entity_crud_coverage_falls_back_to_system_model_when_no_data_model():
    values = {"mockup-spec": {"screens": [{"id": "s1", "entity_id": "ia_finding"}, {"id": "s2", "entity_id": "ia_finding"}]}}
    inputs = {"system-model": {"entities": ["ia_finding"]}}
    assert entity_crud_coverage(values, inputs) == []


def _five_level_data_model():
    """MajorProcess -> SubProcess -> RiskStatement -> TestObjective ->
    Procedure, a real chain reproduced from the live failure this fixes."""
    return {
        "entities": [{"name": n} for n in ["MajorProcess", "SubProcess", "RiskStatement", "TestObjective", "Procedure"]],
        "relationships": [
            {"from_entity": "MajorProcess", "to_entity": "SubProcess", "cardinality": "one-to-many"},
            {"from_entity": "SubProcess", "to_entity": "RiskStatement", "cardinality": "one-to-many"},
            {"from_entity": "RiskStatement", "to_entity": "TestObjective", "cardinality": "one-to-many"},
            {"from_entity": "TestObjective", "to_entity": "Procedure", "cardinality": "one-to-many"},
        ],
    }


def test_entity_crud_coverage_exempts_deep_hierarchy_chain_from_dual_screen_rule():
    """Reproduces the live failure: forcing 2 screens per level of a
    5-level fieldwork register produced an unmanageable pile the model
    couldn't satisfy. One shared screen for the whole chain must pass."""
    values = {"mockup-spec": {"screens": [{"id": "fieldwork_register_editor", "entity_id": "MajorProcess"}]}}
    inputs = {"data-model": _five_level_data_model()}
    assert entity_crud_coverage(values, inputs) == []


def test_entity_crud_coverage_still_requires_some_coverage_for_chain_entities():
    """A chain still needs at least ONE screen representing it -- it just
    doesn't need two per level."""
    values = {"mockup-spec": {"screens": [{"id": "s", "entity_id": "unrelated_entity"}]}}
    inputs = {"data-model": {**_five_level_data_model(), "entities": _five_level_data_model()["entities"] + [{"name": "unrelated_entity"}]}}
    errors = entity_crud_coverage(values, inputs)
    assert any("MajorProcess" in e or "SubProcess" in e for e in errors)


def test_entity_crud_coverage_ordinary_parent_child_pair_still_needs_two_screens():
    """A single parent-child relationship (component size 2) does NOT
    qualify as a deep hierarchy chain -- normal dual-screen CRUD still
    applies, unaffected by the chain exemption."""
    values = {"mockup-spec": {"screens": [{"id": "s", "entity_id": "audit_plan"}]}}
    inputs = {"data-model": {
        "entities": [{"name": "audit_plan"}, {"name": "audit_unit"}],
        "relationships": [{"from_entity": "audit_plan", "to_entity": "audit_unit", "cardinality": "one-to-many"}],
    }}
    errors = entity_crud_coverage(values, inputs)
    assert any("only ONE screen" in e and "audit_plan" in e for e in errors)


def _real_audit_data_model():
    """The exact live data model that exposed the connected-components
    bug: AuditPlan/AuditUnit/AuditProject are upstream of the fieldwork
    chain but must NOT be swept into its exemption; Procedure is the
    chain's legitimate 5th member even though it also branches into
    Workpaper and AuditIssue afterward."""
    names = ["AuditPlan", "AuditUnit", "AuditProject", "PlanningDocument", "MajorProcess", "SubProcess",
             "RiskStatement", "TestObjective", "Procedure", "Workpaper", "AuditIssue", "ReviewNote", "WrapUpDocument"]
    return {
        "entities": [{"name": n} for n in names],
        "relationships": [
            {"from_entity": "AuditPlan", "to_entity": "AuditProject", "cardinality": "one-to-many"},
            {"from_entity": "AuditUnit", "to_entity": "AuditProject", "cardinality": "one-to-many"},
            {"from_entity": "AuditProject", "to_entity": "PlanningDocument", "cardinality": "one-to-many"},
            {"from_entity": "AuditProject", "to_entity": "MajorProcess", "cardinality": "one-to-many"},
            {"from_entity": "AuditProject", "to_entity": "WrapUpDocument", "cardinality": "one-to-many"},
            {"from_entity": "MajorProcess", "to_entity": "SubProcess", "cardinality": "one-to-many"},
            {"from_entity": "SubProcess", "to_entity": "RiskStatement", "cardinality": "one-to-many"},
            {"from_entity": "RiskStatement", "to_entity": "TestObjective", "cardinality": "one-to-many"},
            {"from_entity": "TestObjective", "to_entity": "Procedure", "cardinality": "one-to-many"},
            {"from_entity": "Procedure", "to_entity": "Workpaper", "cardinality": "one-to-many"},
            {"from_entity": "Procedure", "to_entity": "AuditIssue", "cardinality": "one-to-many"},
            {"from_entity": "Workpaper", "to_entity": "ReviewNote", "cardinality": "one-to-many"},
        ],
    }


def test_hierarchy_chain_detection_matches_the_real_five_level_register_exactly():
    components = _hierarchy_chain_components(_real_audit_data_model())
    assert len(components) == 1
    assert components[0] == {"MajorProcess", "SubProcess", "RiskStatement", "TestObjective", "Procedure"}


def test_entity_crud_coverage_does_not_over_exempt_upstream_or_branching_entities():
    """Reproduces the live bug exactly: a loose connected-components check
    swept AuditPlan/AuditUnit/AuditProject (merely upstream of the chain)
    and Workpaper/AuditIssue/ReviewNote (past the branch point) into the
    exemption. All of these must still require their own 2 screens; only
    the true 5-level chain is exempt."""
    screens = [
        {"id": "fieldwork_editor", "entity_id": "MajorProcess"},  # the one shared chain screen -- satisfies all 5 levels
        {"id": "plan_list", "entity_id": "AuditPlan"},  # only 1 screen each -- should still be flagged
        {"id": "unit_list", "entity_id": "AuditUnit"},
        {"id": "project_list", "entity_id": "AuditProject"},
        {"id": "workpaper_list", "entity_id": "Workpaper"},
        {"id": "issue_list", "entity_id": "AuditIssue"},
        {"id": "review_note_list", "entity_id": "ReviewNote"},
        {"id": "planning_doc_list", "entity_id": "PlanningDocument"},
        {"id": "wrapup_list", "entity_id": "WrapUpDocument"},
    ]
    values = {"mockup-spec": {"screens": screens}}
    inputs = {"data-model": _real_audit_data_model()}
    errors = entity_crud_coverage(values, inputs)
    single_screen_error = next((e for e in errors if "only ONE screen" in e), None)
    assert single_screen_error is not None
    for name in ["AuditPlan", "AuditUnit", "AuditProject", "Workpaper", "AuditIssue", "ReviewNote", "PlanningDocument", "WrapUpDocument"]:
        assert name in single_screen_error
    # The exempted chain members must NOT appear in the single-screen error.
    for name in ["MajorProcess", "SubProcess", "RiskStatement", "TestObjective", "Procedure"]:
        assert name not in single_screen_error


def test_entity_crud_coverage_accepts_list_plus_form_pairs():
    values = {"mockup-spec": {"screens": [
        {"id": "landing", "workflow_id": "__landing__"},                  # no entity_id, fine
        {"id": "e1-list", "entity_id": "ia_finding"},
        {"id": "e1-form", "entity_id": "ia_finding"},
        {"id": "e2-list", "entity_id": "ia_action_plan"},
        {"id": "e2-form", "entity_id": "ia_action_plan"},
    ]}}
    inputs = {"system-model": {"entities": ["ia_finding", "ia_action_plan"]}}
    assert entity_crud_coverage(values, inputs) == []


def test_entity_crud_coverage_rejects_a_single_screen_per_entity():
    """One combined screen isn't CRUD coverage -- the constraint promises
    a list/register screen AND a separate detail/edit/form screen. This
    was the actual live failure mode: the model produced exactly one
    screen per entity and the validator used to let that through."""
    values = {"mockup-spec": {"screens": [{"id": "combined", "entity_id": "ia_finding"}]}}
    inputs = {"system-model": {"entities": ["ia_finding"]}}
    errors = entity_crud_coverage(values, inputs)
    assert any("ia_finding" in e and "only ONE screen" in e for e in errors)


def test_entity_crud_coverage_rejects_missing_entities():
    values = {"mockup-spec": {"screens": [{"id": "s1", "entity_id": "ia_finding"}, {"id": "s2", "entity_id": "ia_finding"}]}}
    inputs = {"system-model": {"entities": ["ia_finding", "ia_workpaper", "ia_action_plan"]}}
    errors = entity_crud_coverage(values, inputs)
    assert any("ia_workpaper" in e and "ia_action_plan" in e for e in errors)


def test_entity_crud_coverage_rejects_invented_entity_ids():
    values = {"mockup-spec": {"screens": [{"id": "s", "entity_id": "findings"}]}}  # invented; real name is ia_finding
    inputs = {"system-model": {"entities": ["ia_finding"]}}
    errors = entity_crud_coverage(values, inputs)
    assert any("findings" in e and "not one of" in e for e in errors)


def test_entity_crud_coverage_is_noop_when_system_model_absent():
    assert entity_crud_coverage({"mockup-spec": {"screens": []}}, {}) == []
    assert entity_crud_coverage({"mockup-spec": {"screens": []}}, {"system-model": {}}) == []


def test_no_raw_ids_rendered_rejects_visible_entity_id_in_heading():
    """Reproduces the live failure: the model wrote the entity_id literally
    into an <h1>, e.g. 'Annual Plans Register (ia_annual_plan)'."""
    values = {
        "mockup-spec": {"screens": [{"id": "s1", "entity_id": "ia_annual_plan"}]},
        "mockup-pages": [{"screen_id": "s1", "html": "<h1>Annual Plans Register (ia_annual_plan)</h1>"}],
    }
    errors = no_raw_ids_rendered_in_html(values, {})
    assert any("ia_annual_plan" in e and "s1" in e for e in errors)


def test_no_raw_ids_rendered_accepts_natural_titles():
    values = {
        "mockup-spec": {"screens": [{"id": "s1", "entity_id": "ia_annual_plan"}]},
        "mockup-pages": [{"screen_id": "s1", "html": "<h1>Annual Plan Register</h1>"}],
    }
    assert no_raw_ids_rendered_in_html(values, {}) == []


def test_no_raw_ids_rendered_checks_single_screen_patch_against_upstream_spec():
    """Single-screen retry shape: mockup-spec comes from inputs (already
    generated), only mockup-page-patch is a fresh output."""
    values = {"mockup-page-patch": {"screen_id": "s1", "html": "<h1>Register (ia_annual_plan)</h1>"}}
    inputs = {"mockup-spec": {"screens": [{"id": "s1", "entity_id": "ia_annual_plan"}]}}
    errors = no_raw_ids_rendered_in_html(values, inputs)
    assert any("ia_annual_plan" in e for e in errors)


def test_no_raw_ids_rendered_is_noop_without_a_spec():
    assert no_raw_ids_rendered_in_html({"mockup-pages": [{"screen_id": "s1", "html": "<h1>x</h1>"}]}, {}) == []


def test_workflow_id_coverage_is_noop_when_architecture_absent():
    """Older projects predate the workflow-enumeration step; validator must
    not block them just because architecture-model.workflows is empty."""
    assert workflow_id_coverage({"mockup-spec": {"screens": []}}, {}) == []
    assert workflow_id_coverage({"mockup-spec": {"screens": []}}, {"architecture-model": {}}) == []


def test_validation_retry_feeds_errors_back_and_succeeds():
    provider = _ScriptedProvider([
        ProviderResponse(text=json.dumps({"mockup-spec": {"screens": [{"id": "x", "workflow_id": "bogus"}]}}), provider="fake", model="m"),
        ProviderResponse(text=json.dumps({"mockup-spec": {"screens": [{"id": "x", "workflow_id": "real_workflow"}]}}), provider="fake", model="m"),
    ])
    inputs = {"architecture-model": {"workflows": [{"id": "real_workflow"}]}}
    agent = ProviderBackedAgent(_definition(), provider, output_validators=[workflow_id_coverage])
    result = agent.run(["mockup-spec"], inputs)
    assert result["mockup-spec"]["screens"][0]["workflow_id"] == "real_workflow"
    # Second request must have carried the specific validation error back to the model.
    second_request_body = json.loads(provider.requests[1].user_prompt)
    assert "bogus" in second_request_body["revision_instruction"]


def test_workflow_and_crud_screens_pass_together():
    values = {"mockup-spec": {"screens": [
        {"id": "landing", "workflow_id": "__landing__"},
        {"id": "s_planning", "workflow_id": "planning_flow"},
        {"id": "e_finding_list", "entity_id": "ia_finding", "workflow_id": ""},
        {"id": "e_finding_form", "entity_id": "ia_finding", "workflow_id": ""},
        {"id": "e_action_list", "entity_id": "ia_action_plan", "workflow_id": ""},
        {"id": "e_action_form", "entity_id": "ia_action_plan", "workflow_id": ""},
    ]}}
    inputs = {
        "architecture-model": {"workflows": [{"id": "planning_flow"}]},
        "system-model": {"entities": ["ia_finding", "ia_action_plan"]},
    }
    assert workflow_id_coverage(values, inputs) == []
    assert entity_crud_coverage(values, inputs) == []


def test_validation_retry_gives_up_after_the_cap_with_a_clear_error():
    always_bad = ProviderResponse(text=json.dumps({"mockup-spec": {"screens": [{"id": "x", "workflow_id": "bogus"}]}}), provider="fake", model="m")
    provider = _ScriptedProvider([always_bad, always_bad, always_bad])
    inputs = {"architecture-model": {"workflows": [{"id": "real_workflow"}]}}
    agent = ProviderBackedAgent(_definition(), provider, output_validators=[workflow_id_coverage], max_validation_retries=2)
    with pytest.raises(ValueError, match="failed validation after 3 attempt"):
        agent.run(["mockup-spec"], inputs)

