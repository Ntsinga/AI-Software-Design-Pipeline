"""Multi-project isolation + legacy-alias behavior on the filesystem store."""

from fastapi.testclient import TestClient

from design_pipeline.api import create_app
from design_pipeline.runtime import DesignRuntime, RuntimeRegistry


def test_two_projects_are_isolated(tmp_path):
    registry = RuntimeRegistry(tmp_path)
    registry.create_project("alpha")
    registry.create_project("beta")

    alpha = registry.for_project("alpha")
    beta = registry.for_project("beta")

    # Both are freshly initialized and share nothing.
    alpha.store.artifacts.save("brd", "brd", "alpha's brd content", generated_by={"agent": "runtime"})
    assert [a.logical_id for a in alpha.store.artifacts.list_latest()] == ["brd"]
    assert beta.store.artifacts.list_latest() == []

    beta.store.artifacts.save("brd", "brd", "beta's brd content", generated_by={"agent": "runtime"})
    assert alpha.store.artifacts.get("brd").content == "alpha's brd content"
    assert beta.store.artifacts.get("brd").content == "beta's brd content"


def test_project_registry_lists_created_projects(tmp_path):
    registry = RuntimeRegistry(tmp_path)
    registry.create_project("alpha")
    registry.create_project("beta")
    ids = {entry["id"] for entry in registry.list_projects()}
    assert {"alpha", "beta"}.issubset(ids)


def test_legacy_unprefixed_paths_route_to_default_project(tmp_path):
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")

    # Post via the legacy path
    response = client.post("/documents/brd", json={"filename": "req.md", "text": "# BR-001\nDo the thing."})
    assert response.status_code == 200

    # And read it back via the new project-scoped path -- they must be the same underlying project.
    response = client.get("/projects/default/artifacts")
    assert response.status_code == 200


def test_legacy_layout_is_migrated_into_default_subtree(tmp_path):
    # Simulate the old single-project layout by creating .design/state
    # etc. directly under root, then constructing a store.
    legacy = tmp_path / ".design"
    (legacy / "state").mkdir(parents=True)
    (legacy / "state" / "project-state.yaml").write_text("project_id: legacy\nworkflow_id: initial-design\nworkflow_status: not_started\nstep_states: {}\npending_approvals: []\nupdated_at: '2020-01-01T00:00:00Z'\n", encoding="utf-8")

    # Constructing a DesignRuntime for the default project should migrate the legacy tree in place.
    runtime = DesignRuntime(tmp_path)
    assert (legacy / "default" / "state" / "project-state.yaml").exists()
    assert not (legacy / "state").exists()  # moved, not copied
    # And the state is loadable through the new layout.
    assert runtime.store.load_state().project_id == "legacy"


def test_creating_a_project_via_api_makes_it_visible(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.post("/projects", json={"name": "second-project"})
    assert response.status_code == 200
    projects = client.get("/projects").json()
    assert any(entry["id"] == "second-project" for entry in projects)
