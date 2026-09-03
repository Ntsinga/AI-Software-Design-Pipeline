import base64
import html
import io
import json
import time
import zipfile

from fastapi.testclient import TestClient

from design_pipeline.api import create_app
from test_documents import pdf_document_bytes, word_document_bytes


def _wait_for_workflow_idle(client, status_path="/status", timeout=5.0):
    """`/workflow/run` and `/workflow/restart` now kick the run off in a
    background thread and return as soon as it's underway (see api.py's
    start_background), instead of blocking until every step finishes --
    tests that used to read the result straight off the POST response need
    to poll /status until the background thread is done."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(status_path).json()
        if status["workflow_status"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError(f"workflow on {status_path} did not leave 'running' within {timeout}s")


def test_api_uses_shared_runtime(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.get("/")
    assert response.status_code == 200
    assert "Design Pipeline Review" in response.text
    response = client.get("/review-assets/app.js")
    assert response.status_code == 200
    response = client.post("/initialize", params={"project_id": "api-project"})
    assert response.status_code == 200
    response = client.post("/documents/brd", json={"filename": "requirements.md", "text": "# BR-017\nThe reviewer can approve high-risk reports."})
    assert response.status_code == 200
    response = client.post("/workflow/run")
    assert response.status_code == 200
    assert response.json()["status"] == "started"
    status = _wait_for_workflow_idle(client)
    assert status["pending_approvals"] == ["system-model"]
    response = client.get("/artifacts")
    assert response.status_code == 200
    assert {item["logical_id"] for item in response.json()} >= {"brd", "system-model"}
    response = client.get("/artifacts/system-model/versions")
    assert response.status_code == 200
    assert response.json()[0]["version"] == 1
    response = client.get("/requirements/BR-017/impact")
    assert response.status_code == 200
    assert "system-model" in response.json()["affected_artifacts"]


def test_live_restart_requires_a_live_provider(tmp_path):
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")
    response = client.post("/workflow/restart")
    assert response.status_code == 400
    assert "live generation is not selected" in response.json()["detail"]


def test_second_workflow_run_while_one_is_in_progress_is_rejected(tmp_path, monkeypatch):
    """The in-memory `_running_projects` guard (api.py's `start_background`)
    must reject a second `/workflow/run` while the first is still executing
    in its background thread -- otherwise two threads could mutate the same
    cached DesignRuntime/state store concurrently."""
    import threading

    from design_pipeline.runtime import DesignRuntime

    started = threading.Event()
    release = threading.Event()
    original_run = DesignRuntime.run

    def slow_run(self, step_id=None):
        started.set()
        release.wait(timeout=5)
        return original_run(self, step_id)

    monkeypatch.setattr(DesignRuntime, "run", slow_run)

    client = TestClient(create_app(tmp_path))
    client.post("/initialize")
    client.post("/documents/brd", json={"filename": "requirements.md", "text": "# BR-001\nDo the thing."})

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, "/workflow/run")
        assert started.wait(timeout=5)
        response = client.post("/workflow/run")
        assert response.status_code == 400
        assert "already in progress" in response.json()["detail"]
        release.set()
        assert first.result(timeout=5).status_code == 200


def test_live_provider_failure_on_retry_returns_a_clean_502_not_a_raw_500(tmp_path, monkeypatch):
    """`retry()`, unlike `run()`/`run_step()`, has no internal try/except
    around `_execute_agent` -- a live-provider failure there previously
    propagated all the way up as an unhandled 500."""
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")
    client.post("/documents/brd", json={"filename": "requirements.md", "text": "# BR-001\nDo the thing."})
    client.post("/workflow/run")  # produces the stub `brd` artifact, generated_by.agent="requirements-agent"
    _wait_for_workflow_idle(client)

    monkeypatch.setenv("DESIGN_PIPELINE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    class FailingProvider:
        name = "openai"
        model = "test-model"

        def generate(self, request):
            from design_pipeline.providers import LiveProviderError
            raise LiveProviderError("OpenAI request failed: 429 Too Many Requests")

    monkeypatch.setattr("design_pipeline.runtime.create_model_provider", lambda settings: FailingProvider())

    response = client.post("/artifacts/brd/retry")
    assert response.status_code == 502
    assert "429 Too Many Requests" in response.json()["detail"]


def test_provider_can_be_switched_without_touching_keys(tmp_path):
    (tmp_path / ".env").write_text("DESIGN_PIPELINE_PROVIDER=openai\nOPENAI_API_KEY=secret\nGEMINI_API_KEY=gemini-secret\nGEMINI_MODEL=test-gemini-model\n", encoding="utf-8")
    client = TestClient(create_app(tmp_path))
    response = client.put("/provider", json={"provider": "gemini"})
    assert response.status_code == 200
    assert response.json() == {"provider": "gemini", "model": "test-gemini-model", "mode": "live", "configured": True}
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DESIGN_PIPELINE_PROVIDER=gemini" in env_text
    assert "OPENAI_API_KEY=secret" in env_text  # keys untouched


def test_invalid_provider_selection_is_rejected(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.put("/provider", json={"provider": "not-a-real-provider"})
    assert response.status_code == 400


def test_design_reference_structured_capture_endpoint(tmp_path):
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")
    response = client.post("/design-reference", json={"data": {"colors": {"brand": "#714B67"}}})
    assert response.status_code == 200
    assert response.json()["content"]["colors"]["brand"] == "#714B67"


def test_design_reference_file_upload_endpoint(tmp_path):
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")
    response = client.post("/design-reference", json={"text": "# Notes\nUse rounded corners.", "filename": "notes.md"})
    assert response.status_code == 200
    assert "rounded corners" in response.json()["content"]["notes"]


def test_design_reference_requires_exactly_one_mode(tmp_path):
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")
    response = client.post("/design-reference", json={})
    assert response.status_code == 422
    response = client.post("/design-reference", json={"data": {"a": 1}, "name": "WhatsApp"})
    assert response.status_code == 422


def test_references_api_lifecycle(tmp_path):
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")

    response = client.post("/references/architecture", json={"text": "Prefer teal accents.", "filename": "brand.md"})
    assert response.status_code == 200

    response = client.get("/references/architecture")
    assert response.status_code == 200
    assert [r["filename"] for r in response.json()] == ["brand.md"]

    response = client.delete("/references/architecture/brand.md")
    assert response.status_code == 200
    assert client.get("/references/architecture").json() == []


def test_api_accepts_base64_word_upload(tmp_path):
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")
    payload = base64.b64encode(word_document_bytes("BR-099", "Word input is supported.")).decode("ascii")
    response = client.post("/documents/brd", json={"filename": "BRD.docx", "content_base64": payload})
    assert response.status_code == 200
    assert response.json()["filename"] == "BRD.docx"
    assert "BR-099" in (tmp_path / ".design" / "default" / "input" / "BRD.md").read_text(encoding="utf-8")


def test_project_can_be_renamed(tmp_path):
    client = TestClient(create_app(tmp_path))
    project = client.post("/projects", json={"name": "Original Name"}).json()

    response = client.patch(f"/projects/{project['id']}", json={"name": "Renamed Project"})
    assert response.status_code == 200
    assert response.json() == {"id": project["id"], "name": "Renamed Project"}

    # The id (the storage slug) never changes -- only the display name --
    # so the project is still reachable at the same URL.
    listed = {entry["id"]: entry["name"] for entry in client.get("/projects").json()}
    assert listed[project["id"]] == "Renamed Project"
    assert client.get(f"/projects/{project['id']}/status").status_code == 200


def test_renaming_an_unknown_project_404s(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.patch("/projects/does-not-exist", json={"name": "New Name"})
    assert response.status_code == 404


def test_project_can_be_deleted(tmp_path):
    client = TestClient(create_app(tmp_path))
    project = client.post("/projects", json={"name": "Throwaway"}).json()
    client.post(f"/projects/{project['id']}/documents/brd", json={"filename": "requirements.md", "text": "# BR-001\nSomething."})

    response = client.delete(f"/projects/{project['id']}")
    assert response.status_code == 204

    assert project["id"] not in {entry["id"] for entry in client.get("/projects").json()}
    # Gone, not just delisted -- the underlying project state is gone too.
    assert client.get(f"/projects/{project['id']}/status").status_code == 404
    assert not (tmp_path / ".design" / project["id"]).exists()


def test_deleting_an_unknown_project_404s(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.delete("/projects/does-not-exist")
    assert response.status_code == 404


def test_a_new_project_can_reuse_an_id_after_the_original_is_deleted(tmp_path):
    """The runtime cache (RuntimeRegistry._runtimes) must be invalidated on
    delete -- otherwise a same-named project created right after would
    silently resurrect the deleted one's cached, stale in-memory state."""
    client = TestClient(create_app(tmp_path))
    first = client.post("/projects", json={"name": "reused-name"}).json()
    client.get(f"/projects/{first['id']}/status")  # populate the runtime cache
    client.delete(f"/projects/{first['id']}")

    second = client.post("/projects", json={"name": "reused-name"}).json()
    assert second["id"] == first["id"]
    status = client.get(f"/projects/{second['id']}/status").json()
    assert status["workflow_status"] == "not_started"
    assert status["artifacts"] == []


def test_api_accepts_base64_pdf_upload(tmp_path):
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")
    payload = base64.b64encode(pdf_document_bytes("BR-101", "PDF input is supported.")).decode("ascii")
    response = client.post("/documents/brd", json={"filename": "BRD.pdf", "content_base64": payload})
    assert response.status_code == 200
    assert response.json()["filename"] == "BRD.pdf"


def _run_to_completion(client):
    """Drive a freshly-initialized project through every approval gate via
    the same HTTP endpoints the review app itself uses, ending with
    data-model, mockup-spec, and mockup-pages all generated."""
    client.post("/initialize")
    client.post("/documents/brd", json={"filename": "requirements.md", "text": "# BR-001\nA reviewer can approve a report."})
    client.post("/workflow/run")
    _wait_for_workflow_idle(client)
    for artifact_id in ("system-model", "data-model", "architecture-model"):
        response = client.post(f"/artifacts/{artifact_id}/approve")
        assert response.status_code == 200
        assert client.post("/workflow/run").status_code == 200
        _wait_for_workflow_idle(client)


def test_data_model_export_returns_a_zip_with_json_and_erd_source(tmp_path):
    client = TestClient(create_app(tmp_path))
    _run_to_completion(client)

    response = client.get("/data-model/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert 'filename="data-model-default.zip"' in response.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert names == {"data-model.json", "erd.mmd"}
        data_model = json.loads(archive.read("data-model.json"))
        assert data_model["entities"]
        erd_source = archive.read("erd.mmd").decode("utf-8")
        assert "erDiagram" in erd_source


def test_data_model_export_404s_before_a_data_model_exists(tmp_path):
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")
    response = client.get("/data-model/export")
    assert response.status_code == 404


def test_mockups_export_returns_one_self_contained_html_file(tmp_path):
    # Single file, not a .zip of many -- so sharing just that one file
    # (Teams, Slack, email) still works. Every screen lives inline as its
    # own <iframe srcdoc="...">, switched by a small nav + the same
    # data-goto -> postMessage contract app.js's own bridge uses live.
    client = TestClient(create_app(tmp_path))
    _run_to_completion(client)
    spec = client.get("/artifacts/mockup-spec").json()["content"]
    pages = client.get("/artifacts/mockup-pages").json()["content"]

    response = client.get("/mockup-pages/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'filename="mockups-default.html"' in response.headers["content-disposition"]

    document = response.text
    # One embedded iframe and one nav button per screen -- nothing to fetch
    # separately, the whole thing is this one response body.
    assert document.count("<iframe ") == len(pages)
    assert document.count("data-screen-link=") == len(pages)
    for screen in spec["screens"]:
        assert html.escape(screen["name"]) in document

    # The first screen's original visible markup survives inside its
    # srcdoc (HTML-escaped, since it's an attribute value), and the click
    # handler that turns data-goto into a screen switch is present.
    first_page = pages[0]
    assert html.escape(first_page["html"].split("</body>")[0]) in document
    # The bridge script lives inside each iframe's srcdoc attribute, so its
    # source appears HTML-escaped in the outer document's raw text.
    assert html.escape("parent.postMessage({type:'mockup-goto'") in document
    assert "function activate(id)" in document


def test_mockups_export_404s_before_mockups_exist(tmp_path):
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")
    response = client.get("/mockup-pages/export")
    assert response.status_code == 404
