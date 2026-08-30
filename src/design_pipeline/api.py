"""Minimal FastAPI facade over the shared DesignRuntime."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
except ImportError as exc:  # pragma: no cover - exercised when optional deps are absent
    FastAPI = None  # type: ignore[assignment]
    _fastapi_error = exc

from .runtime import DesignRuntime


class TextRequest(BaseModel):
    text: str = Field(min_length=1)
    author: str = "user"
    location: dict[str, Any] | None = None


class DecisionRequest(BaseModel):
    reviewer: str = "user"
    note: str | None = None
    version: int | None = None


class RetryRequest(BaseModel):
    instruction: str | None = None


class DocumentRequest(BaseModel):
    text: str | None = None
    content_base64: str | None = None
    filename: str = "BRD.md"

    @model_validator(mode="after")
    def exactly_one_document_body(self):
        if bool(self.text and self.text.strip()) == bool(self.content_base64):
            raise ValueError("provide exactly one of text or content_base64")
        return self


def create_app(root: Path | str = "."):
    if FastAPI is None:
        raise RuntimeError("FastAPI is required to create the API app") from _fastapi_error
    app = FastAPI(title="Design Pipeline", version="0.1.0")
    runtime = DesignRuntime(root)
    review_app = Path(__file__).parent / "review_app"
    app.mount("/review-assets", StaticFiles(directory=review_app), name="review-assets")

    def call(function, *args, **kwargs):
        try:
            result = function(*args, **kwargs)
            return result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404 if isinstance(exc, FileNotFoundError) else 400, detail=str(exc)) from exc

    @app.get("/", include_in_schema=False)
    @app.get("/review", include_in_schema=False)
    def review_workspace():
        return FileResponse(review_app / "index.html")

    @app.post("/initialize")
    def initialize(project_id: str | None = None):
        return call(runtime.initialize, project_id)

    @app.post("/documents/brd")
    def ingest_brd(request: DocumentRequest):
        if request.content_base64:
            try:
                content = base64.b64decode(request.content_base64, validate=True)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="content_base64 is not valid base64") from exc
            return call(runtime.ingest_brd_bytes, content, request.filename)
        return call(runtime.ingest_brd_text, request.text, request.filename)

    @app.get("/status")
    def status():
        return call(runtime.status)

    @app.post("/workflow/run")
    def run_workflow():
        return call(runtime.run)

    @app.post("/workflow/restart")
    def restart_workflow():
        return call(runtime.restart_generation)

    @app.post("/workflow/steps/{step_id}/run")
    def run_step(step_id: str):
        return call(runtime.run_step, step_id)

    @app.get("/artifacts")
    def artifacts():
        return [item.model_dump(mode="json") for item in runtime.store.artifacts.list_latest()]

    @app.get("/artifacts/{artifact_id}")
    def artifact(artifact_id: str, version: int | None = None):
        return call(runtime.store.artifacts.get, artifact_id, version)

    @app.get("/artifacts/{artifact_id}/versions")
    def artifact_versions(artifact_id: str):
        return [item.model_dump(mode="json") for item in runtime.store.artifacts.list_versions(artifact_id)]

    @app.post("/artifacts/{artifact_id}/approve")
    def approve(artifact_id: str, request: DecisionRequest | None = None):
        request = request or DecisionRequest()
        return call(runtime.approve, artifact_id, request.version, request.reviewer, request.note)

    @app.post("/artifacts/{artifact_id}/request-changes")
    def request_changes(artifact_id: str, request: DecisionRequest | None = None):
        request = request or DecisionRequest()
        return call(runtime.request_changes, artifact_id, request.note, request.reviewer, request.version)

    @app.post("/artifacts/{artifact_id}/retry")
    def retry(artifact_id: str, request: RetryRequest | None = None):
        return call(runtime.retry, artifact_id, (request or RetryRequest()).instruction)

    @app.post("/artifacts/{artifact_id}/comments")
    def comment(artifact_id: str, request: TextRequest):
        return call(runtime.add_comment, artifact_id, request.text, author=request.author, location=request.location)

    @app.get("/artifacts/{artifact_id}/comments")
    def comments(artifact_id: str):
        return [item.model_dump(mode="json") for item in runtime.store.list_comments(artifact_id)]

    @app.get("/history")
    def history():
        return [item.model_dump(mode="json") for item in runtime.store.read_events()]

    @app.get("/requirements/{requirement_id}/impact")
    def requirement_impact(requirement_id: str):
        return {"requirement_id": requirement_id, "affected_artifacts": runtime.dependencies(requirement_id)}

    return app
