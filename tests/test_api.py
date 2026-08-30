import base64

from fastapi.testclient import TestClient

from design_pipeline.api import create_app
from test_documents import word_document_bytes


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


def test_api_accepts_base64_word_upload(tmp_path):
    client = TestClient(create_app(tmp_path))
    client.post("/initialize")
    payload = base64.b64encode(word_document_bytes("BR-099", "Word input is supported.")).decode("ascii")
    response = client.post("/documents/brd", json={"filename": "BRD.docx", "content_base64": payload})
    assert response.status_code == 200
    assert response.json()["filename"] == "BRD.docx"
    assert "BR-099" in (tmp_path / ".design" / "input" / "BRD.md").read_text(encoding="utf-8")
