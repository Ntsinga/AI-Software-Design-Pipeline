import base64

from fastapi.testclient import TestClient

from design_pipeline.api import create_app
from test_documents import pdf_document_bytes, word_document_bytes


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
    assert response.json()["pending_approvals"] == ["system-model"]
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


def test_live_provider_failure_on_retry_returns_a_clean_502_not_a_raw_500(tmp_path, monkeypatch):
    """`retry()`, unlike `run()`/`run_step()`, has no internal try/except
    around `_execute_agent` -- a live-provider failure there previously
    propagated all the way up as an unhandled 500."""
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")
    client.post("/documents/brd", json={"filename": "requirements.md", "text": "# BR-001\nDo the thing."})
    client.post("/workflow/run")  # produces the stub `brd` artifact, generated_by.agent="requirements-agent"

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


def test_api_accepts_base64_pdf_upload(tmp_path):
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")
    payload = base64.b64encode(pdf_document_bytes("BR-101", "PDF input is supported.")).decode("ascii")
    response = client.post("/documents/brd", json={"filename": "BRD.pdf", "content_base64": payload})
    assert response.status_code == 200
    assert response.json()["filename"] == "BRD.pdf"
    assert "BR-101" in (tmp_path / ".design" / "default" / "input" / "BRD.md").read_text(encoding="utf-8")
