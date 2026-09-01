import pytest

from design_pipeline.models import ArtifactStatus, StepStatus, WorkflowStatus


def test_workflow_pauses_and_resumes_at_approval_gates(runtime):
    first = runtime.run()
    assert first.status == WorkflowStatus.PAUSED
    assert first.pending_approvals == ["system-model"]
    assert runtime.store.artifacts.get("system-model").metadata.status == ArtifactStatus.AWAITING_REVIEW
    assert runtime.store.list_tasks()
    assert runtime.store.list_tasks()[0].handoff.target_agent == "requirements-agent"

    runtime.approve("system-model")
    second = runtime.run()
    assert second.status == WorkflowStatus.PAUSED
    assert second.pending_approvals == ["data-model"]

    runtime.approve("data-model")
    third = runtime.run()
    assert third.status == WorkflowStatus.PAUSED
    assert third.pending_approvals == ["architecture-model"]

    runtime.approve("architecture-model")
    fourth = runtime.run()
    assert fourth.status == WorkflowStatus.COMPLETED
    assert runtime.store.artifacts.get("mockup-spec").metadata.status == ArtifactStatus.GENERATED
    assert runtime.state().step_states["mockups"] == StepStatus.COMPLETED
    pages = runtime.store.artifacts.get("mockup-pages").content
    assert pages and all("html" in page and "screen_id" in page for page in pages)


def test_requirements_step_receives_the_uploaded_document_text(runtime):
    """Reproduces a real production incident: a live provider has no
    filesystem or tool access (the agent YAML's declared `project.read`
    tool was never actually implemented), so unless the extracted BRD text
    is embedded directly into project-inspection's content, the model
    receives nothing but bare file paths and returns empty output for
    brd/business-model/solution-model/system-model. This asserts the
    "requirements" step's own `inputs` -- exactly what a live provider's
    prompt is built from -- actually carries the uploaded text."""
    runtime.ingest_brd_text("# BR-500\nAuditors must approve every finding before closure.", "AuditModule.md")

    seen_inputs = {}

    def spy(agent_id, outputs, inputs, comments=None, instruction=None):
        seen_inputs.update(inputs)
        return ({o: {} for o in outputs}, {"agent": agent_id, "provider": "stub", "model": "spy"})

    runtime._execute_agent = spy  # type: ignore[assignment]
    runtime.run()

    # read_brd() always reports the fixed on-disk filename ("BRD.md") it
    # re-reads from, not the original upload's name -- that's pre-existing,
    # unrelated behavior; only the text is under test here.
    staged = seen_inputs["project-inspection"]["staged_document"]
    assert staged["filename"] == "BRD.md"
    assert "Auditors must approve every finding before closure" in staged["text"]


def test_uploaded_brd_lands_under_this_projects_own_input_directory(runtime):
    """A prior bug wrote every project's upload to the same shared,
    non-project-scoped path -- the file this test checks for is exactly
    where DocumentReader/DesignRuntime must agree to read it back from."""
    runtime.ingest_brd_text("# BR-1\nContent.", "BRD.md")
    assert (runtime.store.paths.input / "BRD.md").read_text(encoding="utf-8") == "# BR-1\nContent."


def test_design_reference_is_an_optional_mockups_input(runtime):
    """Absent by default -- the mockups step must not require one."""
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    completed = runtime.run()
    assert completed.status == WorkflowStatus.COMPLETED

    runtime.set_design_reference({"colors": {"brand": "#714B67"}, "navigation": "app launcher"})
    stored = runtime.store.artifacts.get("design-reference")
    assert stored.content["colors"]["brand"] == "#714B67"
    # A second capture replaces it as a new version, like any other artifact.
    runtime.set_design_reference({"colors": {"brand": "#000000"}})
    assert runtime.store.artifacts.get("design-reference").metadata.version == 2


def test_design_reference_can_be_ingested_from_a_document(runtime):
    stored = runtime.ingest_design_reference_text("# Brand notes\nPrimary color is teal.", "brand.md")
    assert "teal" in stored.content["notes"]
    assert stored.content["source"] == "file:brand.md"


def test_researching_a_design_reference_requires_a_live_provider(runtime):
    with pytest.raises(ValueError, match="live provider"):
        runtime.generate_design_reference("WhatsApp")


def test_researching_a_named_design_reference(tmp_path, monkeypatch):
    import json

    from design_pipeline.providers.base import ProviderResponse
    from design_pipeline.runtime import DesignRuntime

    monkeypatch.setenv("DESIGN_PIPELINE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    class FakeProvider:
        name = "openai"
        model = "test-model"

        def generate(self, request):
            payload = json.loads(request.user_prompt)
            assert payload["name"] == "WhatsApp"
            return ProviderResponse(text=json.dumps({"colors": {"brand": "#25D366"}, "navigation": "bottom tab bar"}), provider=self.name, model=self.model)

    monkeypatch.setattr("design_pipeline.runtime.create_model_provider", lambda settings: FakeProvider())

    instance = DesignRuntime(tmp_path)
    instance.initialize("test-project")
    stored = instance.generate_design_reference("WhatsApp")
    assert stored.content["colors"]["brand"] == "#25D366"
    assert stored.content["name"] == "WhatsApp"
    assert stored.metadata.generated_by.provider == "openai"


def test_retry_versions_only_one_artifact(runtime):
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    original = runtime.store.artifacts.get("architecture-model")
    runtime.add_comment("architecture-model", "Add a boundary note")
    revised = runtime.retry("architecture-model", "Address the review note")
    assert revised.metadata.version == original.metadata.version + 1
    assert revised.metadata.parent_version == original.metadata.version
    assert runtime.store.artifacts.get("brd").metadata.version == 1
    assert runtime.store.artifacts.get("architecture-model").metadata.status == ArtifactStatus.GENERATED
    assert runtime.store.artifacts.get("architecture-model", original.metadata.version).metadata.status == ArtifactStatus.SUPERSEDED
    assert revised.content["feedback_applied"] == ["Add a boundary note"]


def test_retry_regenerates_all_co_generated_outputs(runtime):
    """`mockup-spec` and `mockup-pages` come from one workflow step -- they
    must retry together so they stay consistent. Before this fix, retrying
    one silently left the other stale, and observed live as mockup-pages
    with 10 screens vs mockup-spec still at 5, breaking half the nav."""
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    runtime.run()  # produces mockup-spec + mockup-pages v1

    spec_before = runtime.store.artifacts.get("mockup-spec").metadata.version
    pages_before = runtime.store.artifacts.get("mockup-pages").metadata.version

    runtime.retry("mockup-pages")

    assert runtime.store.artifacts.get("mockup-spec").metadata.version == spec_before + 1
    assert runtime.store.artifacts.get("mockup-pages").metadata.version == pages_before + 1
    assert runtime.store.artifacts.get("mockup-spec", spec_before).metadata.status == ArtifactStatus.SUPERSEDED

    # And it works symmetrically -- retrying mockup-spec also bumps mockup-pages.
    spec_v2 = runtime.store.artifacts.get("mockup-spec").metadata.version
    pages_v2 = runtime.store.artifacts.get("mockup-pages").metadata.version
    runtime.retry("mockup-spec")
    assert runtime.store.artifacts.get("mockup-spec").metadata.version == spec_v2 + 1
    assert runtime.store.artifacts.get("mockup-pages").metadata.version == pages_v2 + 1


def test_retry_resolves_comments_so_they_are_not_resent_next_time(runtime):
    """A comment used by a successful retry must not keep getting resent
    on every future retry -- it should be marked resolved and excluded
    from the next call's `comments` argument."""
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    runtime.run()

    comment = runtime.add_comment("architecture-model", "Add a boundary note")
    assert comment.status == "open"

    seen_comments_first: list[str] = []
    seen_comments_second: list[str] = []

    def spy(agent_id, outputs, inputs, comments=None, instruction=None):
        target = seen_comments_first if not seen_comments_first else seen_comments_second
        target.extend(c.text for c in comments or [])
        return ({o: {} for o in outputs}, {"agent": agent_id, "provider": "stub", "model": "spy"})

    runtime._execute_agent = spy  # type: ignore[assignment]
    runtime.retry("architecture-model")
    assert seen_comments_first == ["Add a boundary note"]
    resolved = next(c for c in runtime.store.list_comments("architecture-model") if c.id == comment.id)
    assert resolved.status == "resolved"
    assert resolved.resolved_at is not None

    runtime.retry("architecture-model")  # second retry, no new comments added
    assert seen_comments_second == []  # the resolved comment must not be resent


def test_retry_screen_resolves_its_own_comments(runtime):
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    runtime.run()
    target_id = runtime.store.artifacts.get("mockup-pages").content[0]["screen_id"]

    comment = runtime.add_comment("mockup-pages", "Remove the stat cards", location={"kind": "screen", "screen_id": target_id})

    def spy(agent_id, outputs, inputs, comments=None, instruction=None):
        assert any(c.text == "Remove the stat cards" for c in comments or [])
        return ({"mockup-page-patch": {"screen_id": target_id, "html": "<html>ok</html>"}}, {"agent": agent_id, "provider": "stub", "model": "spy"})

    runtime._execute_agent = spy  # type: ignore[assignment]
    runtime.retry_screen(target_id)
    resolved = next(c for c in runtime.store.list_comments("mockup-pages") if c.id == comment.id)
    assert resolved.status == "resolved"

    # A second retry_screen call must not see the now-resolved comment.
    def spy_second(agent_id, outputs, inputs, comments=None, instruction=None):
        assert comments == []
        return ({"mockup-page-patch": {"screen_id": target_id, "html": "<html>ok2</html>"}}, {"agent": agent_id, "provider": "stub", "model": "spy"})

    runtime._execute_agent = spy_second  # type: ignore[assignment]
    runtime.retry_screen(target_id)


def test_retry_screen_recovers_a_patch_nested_under_an_unrelated_key(runtime):
    """Confirmed live: even with the top-level `mockup-page-patch` key
    enforced (via Gemini's responseSchema), a model can still nest the
    real {screen_id, html} object one level deeper inside it, e.g.
    {"mockup-page-patch": {"result": {"screen_id": ..., "html": ...}}} --
    the envelope schema deliberately doesn't constrain what's *inside*
    that key. retry_screen must recover it instead of rejecting the whole
    regeneration."""
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    runtime.run()
    target_id = runtime.store.artifacts.get("mockup-pages").content[0]["screen_id"]

    def spy(agent_id, outputs, inputs, comments=None, instruction=None):
        return ({"mockup-page-patch": {"result": {"screen_id": target_id, "html": "<html>nested</html>"}}}, {"agent": agent_id, "provider": "stub", "model": "spy"})

    runtime._execute_agent = spy  # type: ignore[assignment]
    saved = runtime.retry_screen(target_id)
    patched = next(page for page in saved.content if page["screen_id"] == target_id)
    assert patched["html"] == "<html>nested</html>"


def test_retry_screen_raises_with_the_raw_response_when_unrecoverable(runtime):
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    runtime.run()
    target_id = runtime.store.artifacts.get("mockup-pages").content[0]["screen_id"]

    def spy(agent_id, outputs, inputs, comments=None, instruction=None):
        return ({"mockup-page-patch": {"note": "nothing useful here"}}, {"agent": agent_id, "provider": "stub", "model": "spy"})

    runtime._execute_agent = spy  # type: ignore[assignment]
    with pytest.raises(ValueError, match="nothing useful here"):
        runtime.retry_screen(target_id)


def test_add_mockup_screen_appends_to_both_spec_and_pages_untouched_otherwise(runtime):
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    runtime.run()
    source_id = runtime.store.artifacts.get("mockup-pages").content[0]["screen_id"]
    pages_before = runtime.store.artifacts.get("mockup-pages").content
    screens_before = runtime.store.artifacts.get("mockup-spec").content["screens"]

    def spy(agent_id, outputs, inputs, comments=None, instruction=None):
        assert outputs == ["mockup-screen-addition"]
        assert inputs["link_from_screen_id"] == source_id
        assert inputs["new_screen_requirement"] == "Create Audit Plan form"
        return ({"mockup-screen-addition": {
            "screen": {"id": "audit_plan_create", "name": "Create Audit Plan"},
            "page": {"screen_id": "audit_plan_create", "html": "<html>new screen</html>"},
            "updated_source_page": {"screen_id": source_id, "html": "<html>source now links out</html>"},
        }}, {"agent": agent_id, "provider": "stub", "model": "spy"})

    runtime._execute_agent = spy  # type: ignore[assignment]
    saved = runtime.add_mockup_screen("Create Audit Plan form", link_from_screen_id=source_id)

    # The new screen landed in both mockup-pages and mockup-spec.
    added_page = next(p for p in saved.content if p["screen_id"] == "audit_plan_create")
    assert added_page["html"] == "<html>new screen</html>"
    spec_screens = runtime.store.artifacts.get("mockup-spec").content["screens"]
    assert any(s["id"] == "audit_plan_create" for s in spec_screens)

    # The one linking screen was updated...
    source_page = next(p for p in saved.content if p["screen_id"] == source_id)
    assert source_page["html"] == "<html>source now links out</html>"

    # ...but every other existing screen and spec entry is byte-for-byte untouched.
    untouched_ids = {p["screen_id"] for p in pages_before} - {source_id}
    for page_id in untouched_ids:
        before = next(p for p in pages_before if p["screen_id"] == page_id)
        after = next(p for p in saved.content if p["screen_id"] == page_id)
        assert after == before
    assert len(spec_screens) == len(screens_before) + 1
    for screen in screens_before:
        assert screen in spec_screens


def test_add_mockup_screen_rejects_a_colliding_screen_id(runtime):
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    runtime.run()
    existing_id = runtime.store.artifacts.get("mockup-pages").content[0]["screen_id"]

    def spy(agent_id, outputs, inputs, comments=None, instruction=None):
        return ({"mockup-screen-addition": {
            "screen": {"id": existing_id, "name": "Duplicate"},
            "page": {"screen_id": existing_id, "html": "<html>dup</html>"},
        }}, {"agent": agent_id, "provider": "stub", "model": "spy"})

    runtime._execute_agent = spy  # type: ignore[assignment]
    with pytest.raises(ValueError, match="already exists"):
        runtime.add_mockup_screen("Some new screen")


def test_add_mockup_screen_requires_a_description(runtime):
    runtime.run()
    with pytest.raises(ValueError, match="description"):
        runtime.add_mockup_screen("   ")


def test_split_mockup_screen_rewrites_source_and_adds_the_extracted_screen(runtime):
    """The mirror image of add_mockup_screen: a screen carrying two things
    (e.g. a plan-year list conflated with one year's projects table) gets
    split into a genuine list + a new detail screen, with every other
    screen untouched -- same guarantee as retry_screen/add_mockup_screen."""
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    runtime.run()
    source_id = runtime.store.artifacts.get("mockup-pages").content[0]["screen_id"]
    pages_before = runtime.store.artifacts.get("mockup-pages").content
    screens_before = runtime.store.artifacts.get("mockup-spec").content["screens"]

    def spy(agent_id, outputs, inputs, comments=None, instruction=None):
        assert outputs == ["mockup-screen-addition"]
        assert inputs["link_from_screen_id"] == source_id
        assert "the projects table" in inputs["new_screen_requirement"]
        return ({"mockup-screen-addition": {
            "screen": {"id": "plan_detail", "name": "Plan Detail"},
            "page": {"screen_id": "plan_detail", "html": "<html>extracted projects table</html>"},
            "updated_source_page": {"screen_id": source_id, "html": "<html>now a genuine list</html>"},
        }}, {"agent": agent_id, "provider": "stub", "model": "spy"})

    runtime._execute_agent = spy  # type: ignore[assignment]
    saved = runtime.split_mockup_screen(source_id, "the projects table")

    rewritten_source = next(p for p in saved.content if p["screen_id"] == source_id)
    assert rewritten_source["html"] == "<html>now a genuine list</html>"
    new_page = next(p for p in saved.content if p["screen_id"] == "plan_detail")
    assert new_page["html"] == "<html>extracted projects table</html>"

    spec_screens = runtime.store.artifacts.get("mockup-spec").content["screens"]
    assert any(s["id"] == "plan_detail" for s in spec_screens)
    assert len(spec_screens) == len(screens_before) + 1

    untouched_ids = {p["screen_id"] for p in pages_before} - {source_id}
    for page_id in untouched_ids:
        before = next(p for p in pages_before if p["screen_id"] == page_id)
        after = next(p for p in saved.content if p["screen_id"] == page_id)
        assert after == before


def test_split_mockup_screen_requires_updated_source_page(runtime):
    """A split without a rewritten source screen isn't a split -- it's
    add_mockup_screen with extra steps, and would leave the original
    screen carrying both things it was supposed to be split out of."""
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    runtime.run()
    source_id = runtime.store.artifacts.get("mockup-pages").content[0]["screen_id"]

    def spy(agent_id, outputs, inputs, comments=None, instruction=None):
        return ({"mockup-screen-addition": {
            "screen": {"id": "plan_detail", "name": "Plan Detail"},
            "page": {"screen_id": "plan_detail", "html": "<html>extracted</html>"},
        }}, {"agent": agent_id, "provider": "stub", "model": "spy"})

    runtime._execute_agent = spy  # type: ignore[assignment]
    with pytest.raises(ValueError, match="updated_source_page"):
        runtime.split_mockup_screen(source_id, "the projects table")


def test_split_mockup_screen_requires_screen_id_and_description(runtime):
    runtime.run()
    with pytest.raises(ValueError, match="screen_id"):
        runtime.split_mockup_screen("   ", "the table")
    with pytest.raises(ValueError, match="description"):
        runtime.split_mockup_screen("some_screen", "   ")


def test_retry_loads_comments_from_all_co_generated_siblings(runtime):
    """A comment on mockup-pages must still reach the agent when the user
    retries mockup-spec (they regenerate together, so both sets of
    feedback are relevant)."""
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    runtime.run()

    runtime.add_comment("mockup-pages", "This screen's action button label is wrong")
    runtime.add_comment("mockup-spec", "Spec-level flow should include an escalation step")

    seen_comments: list[str] = []

    def spy(agent_id, outputs, inputs, comments=None, instruction=None):
        seen_comments.extend(c.text for c in comments or [])
        return ({o: {"screens": [], "primary_flow": []} if o == "mockup-spec" else [] for o in outputs}, {"agent": agent_id, "provider": "stub", "model": "spy"})

    runtime._execute_agent = spy  # type: ignore[assignment]
    runtime.retry("mockup-spec")
    assert any("action button label" in text for text in seen_comments)  # comment attached to sibling
    assert any("escalation step" in text for text in seen_comments)     # comment attached to the target


def test_references_lifecycle(runtime):
    """Adding a reference creates the artifact; adding another appends;
    same filename replaces in place; removing drops the entry."""
    runtime.add_reference_text("architecture", "First doc: use dark theme.", "brand-notes.md")
    assert len(runtime.list_references("architecture")) == 1

    runtime.add_reference_text("architecture", "Second doc: teal buttons.", "colors.md")
    assert {r["filename"] for r in runtime.list_references("architecture")} == {"brand-notes.md", "colors.md"}

    # Same filename replaces in place, doesn't duplicate.
    runtime.add_reference_text("architecture", "Updated brand notes.", "brand-notes.md")
    entries = runtime.list_references("architecture")
    assert len(entries) == 2
    brand = next(e for e in entries if e["filename"] == "brand-notes.md")
    assert "Updated" in brand["content"]

    runtime.remove_reference("architecture", "colors.md")
    assert [r["filename"] for r in runtime.list_references("architecture")] == ["brand-notes.md"]

    # Removing a nonexistent filename is a harmless no-op.
    assert runtime.remove_reference("architecture", "nonexistent.md") is None


def test_references_reach_the_agent_as_declared_inputs(runtime):
    """Attaching architecture-references must show up in the architecture
    step's inputs on the next run -- otherwise the whole feature is
    unwired."""
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")  # clears the way to the "architecture" step, which is what we're testing here
    runtime.add_reference_text("architecture", "Prefer modular monolith over microservices.", "arch-guidance.md")

    seen_inputs = {}

    def spy(agent_id, outputs, inputs, comments=None, instruction=None):
        if agent_id == "architecture-agent" and "architecture-model" in outputs:
            seen_inputs.update(inputs)
        return ({o: {} if not o.endswith("s") else [] for o in outputs}, {"agent": agent_id, "provider": "stub", "model": "spy"})

    runtime._execute_agent = spy  # type: ignore[assignment]
    runtime.run()
    assert "architecture-references" in seen_inputs
    assert seen_inputs["architecture-references"][0]["content"].startswith("Prefer modular monolith")


def test_retry_uses_current_upstream_artifact_versions_not_stale_pins(runtime):
    """A retry must pull upstream inputs at their latest version, not the
    versions this artifact was originally pinned to. Before this fix,
    retrying mockup-pages v6 fed the agent architecture-model v4 (its
    original pin) even after architecture-model had been regenerated to
    v5 with an entirely different workflow set -- so cross-artifact
    validators saw the wrong data and silently no-op'd."""
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    runtime.run()

    original_arch_version = runtime.store.artifacts.get("architecture-model").metadata.version
    runtime.retry("architecture-model")  # bumps architecture to a new version
    new_arch_version = runtime.store.artifacts.get("architecture-model").metadata.version
    assert new_arch_version == original_arch_version + 1

    runtime.retry("mockup-pages")  # must now read architecture at new_arch_version, not the old pin
    pinned = {ref.logical_id: ref.version for ref in runtime.store.artifacts.get("mockup-pages").metadata.inputs}
    assert pinned.get("architecture-model") == new_arch_version


def test_data_model_entity_add_edit_remove(runtime):
    runtime.run()
    runtime.approve("system-model")
    runtime.run()  # produces data-model v1
    original = runtime.store.artifacts.get("data-model")
    original_count = len(original.content["entities"])

    added = runtime.add_data_model_entity("audit_plan", "An annual audit plan.")
    assert added.metadata.version == original.metadata.version + 1
    assert len(added.content["entities"]) == original_count + 1
    assert added.content["entities"][-1]["name"] == "audit_plan"
    assert runtime.store.artifacts.get("data-model", original.metadata.version).metadata.status == ArtifactStatus.SUPERSEDED

    edited = runtime.edit_data_model_entity(len(added.content["entities"]) - 1, "audit_plan_v2", "Renamed.")
    assert edited.content["entities"][-1]["name"] == "audit_plan_v2"
    # Editing an entity preserves its existing fields.
    assert edited.content["entities"][-1]["fields"] == []

    removed = runtime.remove_data_model_entity(len(edited.content["entities"]) - 1)
    assert len(removed.content["entities"]) == original_count


def test_data_model_field_add_edit_remove(runtime):
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    with_new_entity = runtime.add_data_model_entity("audit_plan")  # appended -- lands at the end, not index 0
    entity_index = len(with_new_entity.content["entities"]) - 1
    assert with_new_entity.content["entities"][entity_index]["fields"] == []

    added = runtime.add_data_model_field(entity_index, "status", "enum(draft,approved)", "Plan lifecycle state.")
    assert added.content["entities"][entity_index]["fields"][-1] == {"name": "status", "type": "enum(draft,approved)", "description": "Plan lifecycle state."}

    field_index = len(added.content["entities"][entity_index]["fields"]) - 1
    edited = runtime.edit_data_model_field(entity_index, field_index, "plan_status", "string", "")
    assert edited.content["entities"][entity_index]["fields"][field_index]["name"] == "plan_status"

    removed = runtime.remove_data_model_field(entity_index, field_index)
    assert removed.content["entities"][entity_index]["fields"] == []


def test_data_model_field_rejects_out_of_range_entity(runtime):
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    with pytest.raises(ValueError, match="entity index"):
        runtime.add_data_model_field(999, "x", "string")


def test_data_model_relationship_add_edit_remove(runtime):
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    original_count = len(runtime.store.artifacts.get("data-model").content["relationships"])

    added = runtime.add_data_model_relationship("artifact", "comment", "one-to-many", "has")
    assert len(added.content["relationships"]) == original_count + 1
    assert added.content["relationships"][-1] == {"from_entity": "artifact", "to_entity": "comment", "cardinality": "one-to-many", "label": "has"}

    index = len(added.content["relationships"]) - 1
    edited = runtime.edit_data_model_relationship(index, "artifact", "comment", "one-to-one", "authored by")
    assert edited.content["relationships"][index]["cardinality"] == "one-to-one"

    removed = runtime.remove_data_model_relationship(index)
    assert len(removed.content["relationships"]) == original_count


def test_render_data_model_erd(runtime):
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    erd = runtime.render_data_model_erd()
    assert erd.startswith("erDiagram")
    assert "artifact" in erd
    assert "comment" in erd
    # Editing data-model and re-rendering picks up the change immediately
    # -- proves the ERD is derived fresh, never stored/versioned itself.
    runtime.add_data_model_entity("audit_plan")
    assert "audit_plan" in runtime.render_data_model_erd()


def test_system_model_survives_retry_after_a_manual_edit(runtime):
    """A manual edit must not corrupt generated_by.agent to "runtime" --
    that value specifically means "deterministic, no underlying agent,
    retry makes no sense" (used for project-inspection). A manually
    edited artifact still has a real originating agent and must stay
    retryable. Reproduces a live bug: editing a system-model item once
    permanently broke "Regenerate system model" with 'deterministic
    inspection artifacts do not support agent retry'."""
    runtime.run()
    assert runtime.store.artifacts.get("system-model").metadata.generated_by.agent == "requirements-agent"

    runtime.add_system_model_item("requirements", "BR-TEMP-001: temporary.")
    assert runtime.store.artifacts.get("system-model").metadata.generated_by.agent == "requirements-agent"

    # Retry must still work -- this raised ValueError before the fix.
    runtime.retry("system-model")
    assert runtime.store.artifacts.get("system-model").metadata.generated_by.agent == "requirements-agent"


def test_data_model_survives_retry_after_a_manual_edit(runtime):
    runtime.run()
    runtime.approve("system-model")
    runtime.run()  # produces data-model
    assert runtime.store.artifacts.get("data-model").metadata.generated_by.agent == "architecture-agent"

    runtime.add_data_model_entity("audit_plan")
    assert runtime.store.artifacts.get("data-model").metadata.generated_by.agent == "architecture-agent"

    runtime.retry("data-model")
    assert runtime.store.artifacts.get("data-model").metadata.generated_by.agent == "architecture-agent"


def test_system_model_item_add_edit_remove(runtime):
    runtime.run()
    original = runtime.store.artifacts.get("system-model")
    original_count = len(original.content["requirements"])

    added = runtime.add_system_model_item("requirements", "BR-NEW-001: A manually added requirement.")
    assert added.metadata.version == original.metadata.version + 1
    assert len(added.content["requirements"]) == original_count + 1
    assert added.content["requirements"][-1] == "BR-NEW-001: A manually added requirement."
    assert runtime.store.artifacts.get("system-model", original.metadata.version).metadata.status == ArtifactStatus.SUPERSEDED

    edited = runtime.edit_system_model_item("requirements", 0, "BR-EDITED-001: Renamed.")
    assert edited.content["requirements"][0] == "BR-EDITED-001: Renamed."
    assert len(edited.content["requirements"]) == original_count + 1  # edit doesn't change count

    removed = runtime.remove_system_model_item("requirements", 0)
    assert len(removed.content["requirements"]) == original_count
    assert "BR-EDITED-001" not in removed.content["requirements"]


def test_system_model_item_edit_rejects_unknown_field(runtime):
    runtime.run()
    with pytest.raises(ValueError, match="not an editable list field"):
        runtime.add_system_model_item("permissions", "x")  # dict field, not a list -- not editable this way


def test_system_model_item_edit_rejects_out_of_range_index(runtime):
    runtime.run()
    with pytest.raises(ValueError, match="out of range"):
        runtime.edit_system_model_item("requirements", 999, "x")
    with pytest.raises(ValueError, match="out of range"):
        runtime.remove_system_model_item("requirements", 999)


def test_system_model_item_add_rejects_empty_value(runtime):
    runtime.run()
    with pytest.raises(ValueError, match="cannot be empty"):
        runtime.add_system_model_item("requirements", "   ")


def test_edit_reference_overwrites_in_place(runtime):
    runtime.add_reference_text("architecture", "Original wording.", "notes.md")
    runtime.edit_reference("architecture", "notes.md", "Updated wording.")
    entries = runtime.list_references("architecture")
    assert len(entries) == 1  # replaced, not appended
    assert entries[0]["content"] == "Updated wording."


def test_edit_reference_rejects_unknown_filename(runtime):
    with pytest.raises(FileNotFoundError):
        runtime.edit_reference("architecture", "nonexistent.md", "text")


def test_retry_screen_regenerates_only_the_target_screen(runtime):
    """The other 9 (or however many) screens' HTML must be byte-identical
    after a single-screen retry -- that's the whole point over a full
    `retry('mockup-pages')`, which rewrites everything."""
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    runtime.run()  # produces mockup-pages v1 with N screens

    before = runtime.store.artifacts.get("mockup-pages").content
    target_id = before[0]["screen_id"]
    untouched_ids = [p["screen_id"] for p in before[1:]]

    def spy(agent_id, outputs, inputs, comments=None, instruction=None):
        assert outputs == ["mockup-page-patch"]
        assert inputs["target_screen_id"] == target_id
        assert "mockup-pages" in inputs and "mockup-spec" in inputs
        return ({"mockup-page-patch": {"screen_id": target_id, "html": "<html>REGENERATED</html>"}}, {"agent": agent_id, "provider": "stub", "model": "spy"})

    runtime._execute_agent = spy  # type: ignore[assignment]
    saved = runtime.retry_screen(target_id)

    after = {p["screen_id"]: p["html"] for p in saved.content}
    assert after[target_id] == "<html>REGENERATED</html>"
    before_by_id = {p["screen_id"]: p["html"] for p in before}
    for screen_id in untouched_ids:
        assert after[screen_id] == before_by_id[screen_id]  # byte-identical, not just "similar"
    assert saved.metadata.version == 2
    assert runtime.store.artifacts.get("mockup-pages", 1).metadata.status == ArtifactStatus.SUPERSEDED


def test_retry_screen_rejects_unknown_screen_id(runtime):
    runtime.run()
    runtime.approve("system-model")
    runtime.run()
    runtime.approve("data-model")
    runtime.run()
    runtime.approve("architecture-model")
    runtime.run()
    with pytest.raises(ValueError, match="no mockup page found"):
        runtime.retry_screen("does-not-exist")


def test_request_changes_is_recorded(runtime):
    runtime.run()
    decision = runtime.request_changes("system-model", "Need another actor")
    assert decision.decision == "changes_requested"
    assert runtime.store.artifacts.get("system-model").metadata.status == ArtifactStatus.CHANGES_REQUESTED


def test_individual_ready_step_execution(runtime):
    report = runtime.run_step("inspect-project")
    assert report.completed_steps == ["inspect-project"]
    assert runtime.store.artifacts.get("project-inspection").metadata.version == 1
    with pytest.raises(ValueError, match="dependencies are incomplete"):
        runtime.run_step("requirements-model")


def test_custom_brd_ids_are_traceable(runtime, tmp_path):
    source = tmp_path / "custom.md"
    source.write_text("# BR-017\nDirector approval is required for high-risk reports.", encoding="utf-8")
    runtime.ingest_brd(source)
    report = runtime.run()
    assert report.pending_approvals == ["system-model"]
    assert runtime.store.artifacts.get("brd").metadata.requirements == ["BR-017"]
    assert "system-model" in runtime.dependencies("BR-017")


def test_repeated_workflow_run_does_not_create_new_artifact_versions(runtime):
    runtime.run()
    first_versions = {item.logical_id: item.version for item in runtime.store.artifacts.list_latest()}
    runtime.run()
    second_versions = {item.logical_id: item.version for item in runtime.store.artifacts.list_latest()}
    assert first_versions == second_versions


def test_failed_stage_can_be_retried_without_restarting_completed_steps(runtime):
    """Renaming architecture-agent breaks the first step that uses it --
    now "data-modeling" (architecture-agent's config is shared by both
    the data-modeling and architecture steps; data-modeling runs first)."""
    runtime.run()
    runtime.approve("system-model")
    agent_path = runtime.store.paths.agents / "architecture.yaml"
    original = agent_path.read_text(encoding="utf-8")
    agent_path.write_text(original.replace("id: architecture-agent", "id: unknown-agent"), encoding="utf-8")
    try:
        failed = runtime.run()
        assert failed.failed_step == "data-modeling"
        assert runtime.state().step_states["requirements"] == StepStatus.COMPLETED
        assert any(event.event_type == "STEP_FAILED" and event.step_id == "data-modeling" for event in runtime.store.read_events())
        agent_path.write_text(original, encoding="utf-8")
        retried = runtime.run()
        assert retried.status == WorkflowStatus.PAUSED
        assert retried.pending_approvals == ["data-model"]
    finally:
        agent_path.write_text(original, encoding="utf-8")
