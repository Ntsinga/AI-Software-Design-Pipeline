"""Tests for the plan-first mockup-chat orchestration.

Covers plan validation, execution order, cross-step references, fail-stop
behaviour, and the API endpoints.  No Postgres -- all in-memory / tmp_path.
"""

import json
import threading
import time

import pytest

from design_pipeline.mockup_chat import (
    MockupChatPlan,
    MockupChatSession,
    MockupChatSessionStore,
    MockupChatStep,
    StepResult,
    execute_mockup_chat,
    validate_plan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plan(steps: list[dict]) -> MockupChatPlan:
    return MockupChatPlan(
        summary="test plan",
        steps=[MockupChatStep(**s) for s in steps],
    )


def _make_session(project_id: str, plan: MockupChatPlan) -> MockupChatSession:
    return MockupChatSession(
        session_id="test-sess-001",
        project_id=project_id,
        instruction="test instruction",
        plan=plan,
        status="planned",
        steps=[
            StepResult(index=i, description=s.description, operation=s.operation)
            for i, s in enumerate(plan.steps)
        ],
    )


class _FakeStore:
    """Minimal stand-in for the artifact store used by DesignRuntime."""

    def __init__(self, screens: list[dict]):
        self._screens = list(screens)
        self._events: list[dict] = []

    class _paths:
        project_id = "fake-project"

    paths = _paths()

    class _ArtifactProxy:
        def __init__(self, screens):
            self._screens = screens

        def get(self, logical_id):
            if logical_id == "mockup-spec":
                return type("A", (), {"content": {"screens": self._screens}})()
            if logical_id == "mockup-pages":
                return type("A", (), {"content": []})()
            raise FileNotFoundError(logical_id)

    @property
    def artifacts(self):
        return self._ArtifactProxy(self._screens)

    def append_event(self, *args, **kwargs):
        self._events.append({"args": args, "kwargs": kwargs})


class _FakeRuntime:
    """Mimics the subset of DesignRuntime used by execute_mockup_chat."""

    def __init__(self, screens: list[dict] | None = None):
        self.store = _FakeStore(screens or [{"id": "s1"}, {"id": "s2"}])
        self.calls: list[tuple[str, dict]] = []
        self._next_id = 1

    def _require_initialized(self):
        pass

    def retry_screen(self, screen_id, instruction=None):
        self.calls.append(("retry_screen", {"screen_id": screen_id, "instruction": instruction}))

    def add_mockup_screen(self, description, link_from_screen_id=None):
        new_id = f"new-{self._next_id}"
        self._next_id += 1
        self.store._screens.append({"id": new_id})
        self.calls.append(("add_mockup_screen", {"description": description, "link_from_screen_id": link_from_screen_id}))

    def split_mockup_screen(self, screen_id, extract_description):
        new_id = f"split-{self._next_id}"
        self._next_id += 1
        self.store._screens.append({"id": new_id})
        self.calls.append(("split_mockup_screen", {"screen_id": screen_id, "extract_description": extract_description}))


# ---------------------------------------------------------------------------
# validate_plan
# ---------------------------------------------------------------------------

class TestValidatePlan:
    """Unit tests for plan validation (no runtime needed)."""

    def test_valid_plan_returns_no_errors(self):
        plan = _make_plan([
            {"operation": "retry_screen", "description": "fix header", "arguments": {"screen_id": "s1", "instruction": "fix header"}},
            {"operation": "add_screen", "description": "new checkout", "arguments": {"description": "checkout page"}},
            {"operation": "split_screen", "description": "extract detail", "arguments": {"screen_id": "s2", "extract_description": "detail panel"}},
        ])
        errors = validate_plan(plan, {"s1", "s2"})
        assert errors == []

    def test_unknown_operation(self):
        # Build plan manually to bypass Pydantic's Literal validation,
        # since validate_plan must handle bad data from the LLM.
        plan = MockupChatPlan(summary="bad", steps=[])
        bad_step = MockupChatStep.__new__(MockupChatStep)
        object.__setattr__(bad_step, "__dict__", {"operation": "delete_screen", "description": "nope", "arguments": {}})
        object.__setattr__(bad_step, "__pydantic_fields_set__", set())
        plan.steps.append(bad_step)
        errors = validate_plan(plan, {"s1"})
        assert any("unknown operation" in e for e in errors)

    def test_retry_requires_screen_id(self):
        plan = _make_plan([{"operation": "retry_screen", "description": "retry", "arguments": {}}])
        errors = validate_plan(plan, {"s1"})
        assert any("requires 'screen_id'" in e for e in errors)

    def test_retry_validates_screen_exists(self):
        plan = _make_plan([{"operation": "retry_screen", "description": "retry", "arguments": {"screen_id": "nonexistent"}}])
        errors = validate_plan(plan, {"s1"})
        assert any("does not exist" in e for e in errors)

    def test_add_screen_requires_description(self):
        plan = _make_plan([{"operation": "add_screen", "description": "add", "arguments": {}}])
        errors = validate_plan(plan, {"s1"})
        assert any("requires 'description'" in e for e in errors)

    def test_split_requires_extract_description(self):
        plan = _make_plan([{"operation": "split_screen", "description": "split", "arguments": {"screen_id": "s1"}}])
        errors = validate_plan(plan, {"s1"})
        assert any("requires 'extract_description'" in e for e in errors)

    def test_step_ref_forward_reference_rejected(self):
        plan = _make_plan([
            {"operation": "add_screen", "description": "add", "arguments": {"description": "page", "link_from_screen_id": "$step_2"}},
            {"operation": "add_screen", "description": "add2", "arguments": {"description": "page2"}},
        ])
        errors = validate_plan(plan, set())
        assert any("forward or self reference" in e for e in errors)

    def test_step_ref_self_reference_rejected(self):
        plan = _make_plan([
            {"operation": "add_screen", "description": "add", "arguments": {"description": "page", "link_from_screen_id": "$step_1"}},
        ])
        errors = validate_plan(plan, set())
        assert any("forward or self reference" in e for e in errors)

    def test_step_ref_to_retry_rejected(self):
        """$step_1 pointing at a retry_screen step is invalid because retry
        doesn't produce a new screen."""
        plan = _make_plan([
            {"operation": "retry_screen", "description": "fix", "arguments": {"screen_id": "s1"}},
            {"operation": "add_screen", "description": "add", "arguments": {"description": "page", "link_from_screen_id": "$step_1"}},
        ])
        errors = validate_plan(plan, {"s1"})
        assert any("does not produce a new screen" in e for e in errors)

    def test_step_ref_out_of_range(self):
        plan = _make_plan([
            {"operation": "add_screen", "description": "add", "arguments": {"description": "page", "link_from_screen_id": "$step_5"}},
        ])
        errors = validate_plan(plan, set())
        assert any("out of range" in e for e in errors)

    def test_valid_step_ref_accepted(self):
        plan = _make_plan([
            {"operation": "add_screen", "description": "add cart", "arguments": {"description": "cart page"}},
            {"operation": "add_screen", "description": "add checkout", "arguments": {"description": "checkout", "link_from_screen_id": "$step_1"}},
        ])
        errors = validate_plan(plan, set())
        assert errors == []


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------

class TestSessionStore:
    def test_save_and_get(self):
        store = MockupChatSessionStore()
        plan = _make_plan([{"operation": "add_screen", "description": "x", "arguments": {"description": "new"}}])
        session = _make_session("proj1", plan)
        store.save(session)
        assert store.get(session.session_id) is session

    def test_get_missing_returns_none(self):
        store = MockupChatSessionStore()
        assert store.get("nonexistent") is None

    def test_for_project_filters(self):
        store = MockupChatSessionStore()
        plan = _make_plan([{"operation": "add_screen", "description": "x", "arguments": {"description": "new"}}])
        s1 = _make_session("projA", plan)
        s1.session_id = "sess-a"
        s2 = _make_session("projB", plan)
        s2.session_id = "sess-b"
        store.save(s1)
        store.save(s2)
        assert len(store.for_project("projA")) == 1
        assert store.for_project("projA")[0].session_id == "sess-a"


# ---------------------------------------------------------------------------
# execute_mockup_chat
# ---------------------------------------------------------------------------

class TestExecuteMockupChat:
    def test_calls_methods_in_order(self):
        """Three-step plan: retry → add → split. Verify correct order and args."""
        plan = _make_plan([
            {"operation": "retry_screen", "description": "fix header", "arguments": {"screen_id": "s1", "instruction": "bigger header"}},
            {"operation": "add_screen", "description": "new page", "arguments": {"description": "checkout page"}},
            {"operation": "split_screen", "description": "extract detail", "arguments": {"screen_id": "s2", "extract_description": "detail panel"}},
        ])
        runtime = _FakeRuntime()
        session = _make_session("proj1", plan)

        execute_mockup_chat(runtime, session)

        assert session.status == "completed"
        assert all(s.status == "completed" for s in session.steps)
        assert len(runtime.calls) == 3
        assert runtime.calls[0] == ("retry_screen", {"screen_id": "s1", "instruction": "bigger header"})
        assert runtime.calls[1][0] == "add_mockup_screen"
        assert runtime.calls[2][0] == "split_mockup_screen"

    def test_resolves_cross_references(self):
        """Step 2 references $step_1 — the placeholder should be resolved to
        the actual screen ID created by step 1."""
        plan = _make_plan([
            {"operation": "add_screen", "description": "add cart", "arguments": {"description": "cart page"}},
            {"operation": "add_screen", "description": "add checkout", "arguments": {"description": "checkout page", "link_from_screen_id": "$step_1"}},
        ])
        runtime = _FakeRuntime()
        session = _make_session("proj1", plan)

        execute_mockup_chat(runtime, session)

        assert session.status == "completed"
        # Step 1 created "new-1"; step 2 should have received it as link_from_screen_id.
        assert session.steps[0].new_screen_id == "new-1"
        assert runtime.calls[1] == ("add_mockup_screen", {"description": "checkout page", "link_from_screen_id": "new-1"})

    def test_fail_stop_on_error(self):
        """If step 2 of 3 raises, step 3 should not execute."""
        plan = _make_plan([
            {"operation": "retry_screen", "description": "ok", "arguments": {"screen_id": "s1", "instruction": "minor fix"}},
            {"operation": "retry_screen", "description": "boom", "arguments": {"screen_id": "s2", "instruction": "will fail"}},
            {"operation": "add_screen", "description": "never reached", "arguments": {"description": "should not run"}},
        ])
        runtime = _FakeRuntime()

        # Make the second retry_screen call raise.
        call_count = 0
        original_retry = runtime.retry_screen

        def failing_retry(screen_id, instruction=None):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("provider timeout")
            return original_retry(screen_id, instruction)

        runtime.retry_screen = failing_retry

        session = _make_session("proj1", plan)
        execute_mockup_chat(runtime, session)

        assert session.status == "failed"
        assert session.steps[0].status == "completed"
        assert session.steps[1].status == "failed"
        assert "provider timeout" in session.steps[1].error
        assert session.steps[2].status == "pending"
        assert len(runtime.calls) == 1  # only the first retry was recorded

    def test_unresolved_reference_fails(self):
        """If a referenced step didn't produce a screen (e.g. it was a retry),
        the executor should fail gracefully."""
        plan = _make_plan([
            {"operation": "retry_screen", "description": "retry", "arguments": {"screen_id": "s1"}},
            {"operation": "add_screen", "description": "add", "arguments": {"description": "page", "link_from_screen_id": "$step_1"}},
        ])
        runtime = _FakeRuntime()
        session = _make_session("proj1", plan)

        execute_mockup_chat(runtime, session)

        assert session.status == "failed"
        assert session.steps[1].status == "failed"
        assert "unresolved reference" in session.steps[1].error

    def test_split_records_new_screen_id(self):
        plan = _make_plan([
            {"operation": "split_screen", "description": "extract", "arguments": {"screen_id": "s1", "extract_description": "sidebar"}},
        ])
        runtime = _FakeRuntime()
        session = _make_session("proj1", plan)

        execute_mockup_chat(runtime, session)

        assert session.status == "completed"
        assert session.steps[0].new_screen_id == "split-1"

    def test_appends_event_on_success(self):
        plan = _make_plan([
            {"operation": "retry_screen", "description": "fix", "arguments": {"screen_id": "s1", "instruction": "fix"}},
        ])
        runtime = _FakeRuntime()
        session = _make_session("proj1", plan)

        execute_mockup_chat(runtime, session)

        assert len(runtime.store._events) == 1
        assert runtime.store._events[0]["args"][0] == "MOCKUP_CHAT_EXECUTED"


# ---------------------------------------------------------------------------
# API integration
# ---------------------------------------------------------------------------

class TestMockupChatAPI:
    """Integration tests using the FastAPI TestClient."""

    @staticmethod
    def _setup_project_with_mockups(client):
        """Initialize a project, upload a BRD, run the stub workflow until
        mockups exist -- the minimum state needed for mockup-chat to work."""
        client.post("/initialize", params={"project_id": "chat-project"})
        client.post("/documents/brd", json={"filename": "r.md", "text": "# BR-001\nUsers can view a dashboard."})
        client.post("/workflow/run")
        # Poll until idle.
        from test_api import _wait_for_workflow_idle
        _wait_for_workflow_idle(client)
        # Approve every gate until mockup-pages exists.
        for _ in range(10):
            status = client.get("/status").json()
            pending = status.get("pending_approvals", [])
            if not pending:
                break
            for stage in pending:
                client.post(f"/approve/{stage}")
            client.post("/workflow/run")
            _wait_for_workflow_idle(client)
        return client

    def test_plan_requires_live_provider(self, tmp_path, monkeypatch):
        """Stub provider is not allowed for mockup-chat.  We monkeypatch the
        artifact store so that mockup-spec and mockup-pages appear to exist,
        isolating the 'live provider' guard."""
        from design_pipeline.api import create_app
        from fastapi.testclient import TestClient

        client = TestClient(create_app(tmp_path))
        self._setup_project_with_mockups(client)

        # Patch the runtime's store so both mockup artifacts look present.
        from design_pipeline import runtime as rt_mod

        class _FakeArtifacts:
            def __init__(self, real):
                self._real = real
            def get(self, logical_id):
                if logical_id in ("mockup-spec", "mockup-pages"):
                    return type("A", (), {"content": {"screens": [{"id": "s1"}]}})()
                return self._real.get(logical_id)
            def __getattr__(self, name):
                return getattr(self._real, name)

        orig_plan = rt_mod.DesignRuntime._require_initialized

        def _patched_plan(self_rt):
            orig_plan(self_rt)
            if not isinstance(self_rt.store.artifacts, _FakeArtifacts):
                self_rt.store.artifacts = _FakeArtifacts(self_rt.store.artifacts)

        monkeypatch.setattr(rt_mod.DesignRuntime, "_require_initialized", _patched_plan)

        response = client.post("/mockup-chat", json={"instruction": "add a settings screen"})
        assert response.status_code == 400
        assert "live provider" in response.json()["detail"].lower()

    def test_plan_requires_existing_mockups(self, tmp_path):
        """Mockup chat needs generated mockups to inspect."""
        from design_pipeline.api import create_app
        from fastapi.testclient import TestClient

        client = TestClient(create_app(tmp_path))
        client.post("/initialize", params={"project_id": "bare-project"})

        response = client.post("/mockup-chat", json={"instruction": "add a screen"})
        assert response.status_code == 400
        assert "generate mockups" in response.json()["detail"].lower()

    def test_plan_endpoint_with_fake_provider(self, tmp_path, monkeypatch):
        """End-to-end: the plan endpoint returns a valid session."""
        from design_pipeline import providers as providers_mod
        from design_pipeline import runtime as rt_mod
        from design_pipeline.api import create_app
        from design_pipeline.providers import ProviderResponse
        from fastapi.testclient import TestClient

        client = TestClient(create_app(tmp_path))
        self._setup_project_with_mockups(client)

        # Switch to a fake "live" provider that returns a canned plan.
        monkeypatch.setenv("DESIGN_PIPELINE_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "fake")
        monkeypatch.setenv("OPENAI_MODEL", "test-model")

        canned = json.dumps({"mockup-chat-plan": {
            "summary": "Add a settings screen",
            "steps": [{"operation": "add_screen", "description": "new settings page", "arguments": {"description": "settings page"}}],
        }})

        class FakePlanProvider:
            name = "openai"
            model = "test-model"

            def generate(self, request):
                return ProviderResponse(text=canned, provider="openai", model="test-model", tool_calls=[])

        # Patch at the module where create_model_provider is defined, so the
        # local import inside plan_mockup_changes picks up the fake.
        monkeypatch.setattr(providers_mod, "create_model_provider", lambda s: FakePlanProvider())

        # Also ensure the runtime sees mockup artifacts (stub workflow may not
        # have generated them).
        class _FakeArtifacts:
            def __init__(self, real):
                self._real = real
            def get(self, logical_id):
                if logical_id in ("mockup-spec", "mockup-pages"):
                    return type("A", (), {"content": {"screens": [{"id": "s1"}]}})()
                return self._real.get(logical_id)
            def __getattr__(self, name):
                return getattr(self._real, name)

        orig_init = rt_mod.DesignRuntime._require_initialized

        def _patched_init(self_rt):
            orig_init(self_rt)
            if not isinstance(self_rt.store.artifacts, _FakeArtifacts):
                self_rt.store.artifacts = _FakeArtifacts(self_rt.store.artifacts)

        monkeypatch.setattr(rt_mod.DesignRuntime, "_require_initialized", _patched_init)

        response = client.post("/mockup-chat", json={"instruction": "add a settings screen"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "planned"
        assert len(body["plan"]["steps"]) == 1
        assert body["plan"]["steps"][0]["operation"] == "add_screen"
        assert body["session_id"]

    def test_execute_rejects_unknown_session(self, tmp_path):
        from design_pipeline.api import create_app
        from fastapi.testclient import TestClient

        client = TestClient(create_app(tmp_path))
        client.post("/initialize", params={"project_id": "exec-project"})

        response = client.post("/mockup-chat/nonexistent/execute")
        assert response.status_code == 404

    def test_status_returns_404_for_unknown_session(self, tmp_path):
        from design_pipeline.api import create_app
        from fastapi.testclient import TestClient

        client = TestClient(create_app(tmp_path))
        client.post("/initialize", params={"project_id": "status-project"})

        response = client.get("/mockup-chat/nonexistent")
        assert response.status_code == 404
