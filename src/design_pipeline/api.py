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

from .providers import LiveProviderError
from .runtime import DesignRuntime, RuntimeRegistry
from .storage import DEFAULT_PROJECT_ID


class TextRequest(BaseModel):
    text: str = Field(min_length=1)
    author: str = "user"
    location: dict[str, Any] | None = None


class DecisionRequest(BaseModel):
    reviewer: str = "user"
    note: str | None = None
    version: int | None = None


class DataEntityRequest(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


class DataFieldRequest(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    description: str = ""


class DataRelationshipRequest(BaseModel):
    from_entity: str = Field(min_length=1)
    to_entity: str = Field(min_length=1)
    cardinality: str = "one-to-many"
    label: str = ""


class RetryRequest(BaseModel):
    instruction: str | None = None


class ProviderSelection(BaseModel):
    provider: str


class ProjectCreation(BaseModel):
    name: str = Field(min_length=1)


class ReferenceRequest(BaseModel):
    text: str | None = None
    content_base64: str | None = None
    filename: str

    @model_validator(mode="after")
    def exactly_one_body(self):
        if bool(self.text and self.text.strip()) == bool(self.content_base64):
            raise ValueError("provide exactly one of text or content_base64")
        return self


class DocumentRequest(BaseModel):
    text: str | None = None
    content_base64: str | None = None
    filename: str = "BRD.md"

    @model_validator(mode="after")
    def exactly_one_document_body(self):
        if bool(self.text and self.text.strip()) == bool(self.content_base64):
            raise ValueError("provide exactly one of text or content_base64")
        return self


class DesignReferenceRequest(BaseModel):
    # Exactly one acquisition mode: `data` for a structured capture (e.g. a
    # live inspection), `name` to have the live provider research a named
    # app/system from its own knowledge, or `text`/`content_base64` to
    # extract one from an uploaded reference document.
    data: dict[str, Any] | None = None
    name: str | None = None
    notes: str | None = None
    text: str | None = None
    content_base64: str | None = None
    filename: str = "design-reference.md"

    @model_validator(mode="after")
    def exactly_one_acquisition_mode(self):
        modes = [bool(self.data), bool(self.name), bool((self.text and self.text.strip()) or self.content_base64)]
        if sum(modes) != 1:
            raise ValueError("provide exactly one of: data (structured capture), name (research a named reference), or text/content_base64 (file upload)")
        return self


def create_app(root: Path | str = "."):
    if FastAPI is None:
        raise RuntimeError("FastAPI is required to create the API app") from _fastapi_error
    app = FastAPI(title="Design Pipeline", version="0.2.0")
    # RuntimeRegistry.__init__ handles legacy-layout migration; individual
    # projects are lazily created/initialized by `for_project(id)`.
    registry = RuntimeRegistry(root)
    review_app = Path(__file__).parent / "review_app"
    app.mount("/review-assets", StaticFiles(directory=review_app), name="review-assets")

    @app.middleware("http")
    async def _no_cache_review_assets(request, call_next):
        # The review workspace's HTML/JS/CSS change frequently during
        # development; without this, browsers cache /review-assets/app.js
        # aggressively and users see stale UI until a manual hard-refresh.
        # Force revalidation for the app shell and its assets only (the
        # large vendored mermaid.min.js is content-stable, so let it cache).
        response = await call_next(request)
        path = request.url.path
        if path in ("/", "/review") or (path.startswith("/review-assets/") and not path.endswith("mermaid.min.js")):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    def runtime_for(project_id: str) -> DesignRuntime:
        return registry.for_project(project_id or DEFAULT_PROJECT_ID)

    def call(function, *args, **kwargs):
        try:
            result = function(*args, **kwargs)
            return result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404 if isinstance(exc, FileNotFoundError) else 400, detail=str(exc)) from exc
        except LiveProviderError as exc:
            # The selected model provider's API itself failed (network,
            # rate limit, quota, malformed response) -- a clean, retryable
            # error for the caller, not an unhandled 500.
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/", include_in_schema=False)
    @app.get("/review", include_in_schema=False)
    def review_workspace():
        return FileResponse(review_app / "index.html")

    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.responses import HTMLResponse, JSONResponse

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request, exc):
        # Mockup iframes sometimes contain model-generated JS that navigates
        # via `location.href = 'some_screen.html'` -- those hit the review
        # app with an unknown path and default to a JSON 404 body that
        # renders inside the iframe as raw {"detail":"Not Found"}. For an
        # HTML request against an unknown path (never matches one of our
        # real API prefixes), return a blank HTML page instead so the
        # iframe just goes empty. API/JSON callers still get JSON.
        api_prefixes = ("/projects", "/artifacts", "/documents", "/workflow", "/references", "/design-reference", "/history", "/requirements", "/status", "/provider", "/initialize", "/review-assets", "/mockup-pages", "/system-model", "/data-model")
        wants_html = "text/html" in request.headers.get("accept", "") and not request.url.path.startswith(api_prefixes)
        if exc.status_code == 404 and wants_html:
            return HTMLResponse("", status_code=200)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    # ---- Projects registry --------------------------------------------

    @app.get("/projects")
    def list_projects():
        return registry.list_projects()

    @app.post("/projects")
    def create_project(request: ProjectCreation):
        return call(registry.create_project, request.name)

    # ---- Per-project endpoints -----------------------------------------
    # Every endpoint below exists in two forms:
    #   /projects/{project_id}/<path>   -- the canonical, forward-facing shape
    #   /<path>                          -- legacy alias that resolves to the
    #                                       DEFAULT_PROJECT_ID project, for
    #                                       one deprecation cycle so old
    #                                       bookmarks and the Render
    #                                       deployment keep working.
    # Handlers accept an optional `project_id` and delegate to a per-project
    # runtime; the legacy routes just call the same handler with the default.

    def _initialize(project_id, request_project_id):
        return call(runtime_for(project_id).initialize, request_project_id)

    @app.post("/projects/{project_id}/initialize")
    def initialize_scoped(project_id: str, project_label: str | None = None):
        return _initialize(project_id, project_label)

    @app.post("/initialize")
    def initialize_legacy(project_id: str | None = None):
        return _initialize(DEFAULT_PROJECT_ID, project_id)

    def _ingest_brd(project_id, request: DocumentRequest):
        runtime = runtime_for(project_id)
        if request.content_base64:
            try:
                content = base64.b64decode(request.content_base64, validate=True)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="content_base64 is not valid base64") from exc
            return call(runtime.ingest_brd_bytes, content, request.filename)
        return call(runtime.ingest_brd_text, request.text, request.filename)

    @app.post("/projects/{project_id}/documents/brd")
    def ingest_brd_scoped(project_id: str, request: DocumentRequest):
        return _ingest_brd(project_id, request)

    @app.post("/documents/brd")
    def ingest_brd_legacy(request: DocumentRequest):
        return _ingest_brd(DEFAULT_PROJECT_ID, request)

    @app.get("/projects/{project_id}/status")
    def status_scoped(project_id: str):
        return call(runtime_for(project_id).status)

    @app.get("/status")
    def status_legacy():
        return call(runtime_for(DEFAULT_PROJECT_ID).status)

    @app.put("/projects/{project_id}/provider")
    def set_provider_scoped(project_id: str, request: ProviderSelection):
        return call(runtime_for(project_id).set_provider, request.provider)

    @app.put("/provider")
    def set_provider_legacy(request: ProviderSelection):
        return call(runtime_for(DEFAULT_PROJECT_ID).set_provider, request.provider)

    def _set_design_reference(project_id, request: DesignReferenceRequest):
        runtime = runtime_for(project_id)
        if request.data is not None:
            return call(runtime.set_design_reference, request.data)
        if request.name is not None:
            return call(runtime.generate_design_reference, request.name, request.notes)
        if request.content_base64:
            try:
                content = base64.b64decode(request.content_base64, validate=True)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="content_base64 is not valid base64") from exc
            return call(runtime.ingest_design_reference_bytes, content, request.filename)
        return call(runtime.ingest_design_reference_text, request.text, request.filename)

    @app.post("/projects/{project_id}/design-reference")
    def set_design_reference_scoped(project_id: str, request: DesignReferenceRequest):
        return _set_design_reference(project_id, request)

    @app.post("/design-reference")
    def set_design_reference_legacy(request: DesignReferenceRequest):
        return _set_design_reference(DEFAULT_PROJECT_ID, request)

    def _add_reference(project_id, stage, request: ReferenceRequest):
        runtime = runtime_for(project_id)
        if request.content_base64:
            try:
                content = base64.b64decode(request.content_base64, validate=True)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="content_base64 is not valid base64") from exc
            return call(runtime.add_reference_bytes, stage, content, request.filename)
        return call(runtime.add_reference_text, stage, request.text, request.filename)

    @app.post("/projects/{project_id}/references/{stage}")
    def add_reference_scoped(project_id: str, stage: str, request: ReferenceRequest):
        return _add_reference(project_id, stage, request)

    @app.post("/references/{stage}")
    def add_reference_legacy(stage: str, request: ReferenceRequest):
        return _add_reference(DEFAULT_PROJECT_ID, stage, request)

    @app.get("/projects/{project_id}/references/{stage}")
    def list_references_scoped(project_id: str, stage: str):
        return call(runtime_for(project_id).list_references, stage)

    @app.get("/references/{stage}")
    def list_references_legacy(stage: str):
        return call(runtime_for(DEFAULT_PROJECT_ID).list_references, stage)

    def _remove_reference(project_id, stage, filename):
        result = call(runtime_for(project_id).remove_reference, stage, filename)
        return result if result is not None else {"removed": False, "filename": filename}

    @app.delete("/projects/{project_id}/references/{stage}/{filename}")
    def remove_reference_scoped(project_id: str, stage: str, filename: str):
        return _remove_reference(project_id, stage, filename)

    @app.delete("/references/{stage}/{filename}")
    def remove_reference_legacy(stage: str, filename: str):
        return _remove_reference(DEFAULT_PROJECT_ID, stage, filename)

    @app.put("/projects/{project_id}/references/{stage}/{filename}")
    def edit_reference_scoped(project_id: str, stage: str, filename: str, request: TextRequest):
        return call(runtime_for(project_id).edit_reference, stage, filename, request.text)

    @app.put("/references/{stage}/{filename}")
    def edit_reference_legacy(stage: str, filename: str, request: TextRequest):
        return call(runtime_for(DEFAULT_PROJECT_ID).edit_reference, stage, filename, request.text)

    # ---- Direct manual edits to system-model's list fields --------------

    @app.post("/projects/{project_id}/system-model/fields/{field}")
    def add_system_model_item_scoped(project_id: str, field: str, request: TextRequest):
        return call(runtime_for(project_id).add_system_model_item, field, request.text)

    @app.post("/system-model/fields/{field}")
    def add_system_model_item_legacy(field: str, request: TextRequest):
        return call(runtime_for(DEFAULT_PROJECT_ID).add_system_model_item, field, request.text)

    @app.put("/projects/{project_id}/system-model/fields/{field}/{index}")
    def edit_system_model_item_scoped(project_id: str, field: str, index: int, request: TextRequest):
        return call(runtime_for(project_id).edit_system_model_item, field, index, request.text)

    @app.put("/system-model/fields/{field}/{index}")
    def edit_system_model_item_legacy(field: str, index: int, request: TextRequest):
        return call(runtime_for(DEFAULT_PROJECT_ID).edit_system_model_item, field, index, request.text)

    @app.delete("/projects/{project_id}/system-model/fields/{field}/{index}")
    def remove_system_model_item_scoped(project_id: str, field: str, index: int):
        return call(runtime_for(project_id).remove_system_model_item, field, index)

    @app.delete("/system-model/fields/{field}/{index}")
    def remove_system_model_item_legacy(field: str, index: int):
        return call(runtime_for(DEFAULT_PROJECT_ID).remove_system_model_item, field, index)

    # ---- Direct manual edits to data-model -------------------------------

    @app.post("/projects/{project_id}/data-model/entities")
    def add_data_model_entity_scoped(project_id: str, request: DataEntityRequest):
        return call(runtime_for(project_id).add_data_model_entity, request.name, request.description)

    @app.post("/data-model/entities")
    def add_data_model_entity_legacy(request: DataEntityRequest):
        return call(runtime_for(DEFAULT_PROJECT_ID).add_data_model_entity, request.name, request.description)

    @app.put("/projects/{project_id}/data-model/entities/{index}")
    def edit_data_model_entity_scoped(project_id: str, index: int, request: DataEntityRequest):
        return call(runtime_for(project_id).edit_data_model_entity, index, request.name, request.description)

    @app.put("/data-model/entities/{index}")
    def edit_data_model_entity_legacy(index: int, request: DataEntityRequest):
        return call(runtime_for(DEFAULT_PROJECT_ID).edit_data_model_entity, index, request.name, request.description)

    @app.delete("/projects/{project_id}/data-model/entities/{index}")
    def remove_data_model_entity_scoped(project_id: str, index: int):
        return call(runtime_for(project_id).remove_data_model_entity, index)

    @app.delete("/data-model/entities/{index}")
    def remove_data_model_entity_legacy(index: int):
        return call(runtime_for(DEFAULT_PROJECT_ID).remove_data_model_entity, index)

    @app.post("/projects/{project_id}/data-model/entities/{entity_index}/fields")
    def add_data_model_field_scoped(project_id: str, entity_index: int, request: DataFieldRequest):
        return call(runtime_for(project_id).add_data_model_field, entity_index, request.name, request.type, request.description)

    @app.post("/data-model/entities/{entity_index}/fields")
    def add_data_model_field_legacy(entity_index: int, request: DataFieldRequest):
        return call(runtime_for(DEFAULT_PROJECT_ID).add_data_model_field, entity_index, request.name, request.type, request.description)

    @app.put("/projects/{project_id}/data-model/entities/{entity_index}/fields/{field_index}")
    def edit_data_model_field_scoped(project_id: str, entity_index: int, field_index: int, request: DataFieldRequest):
        return call(runtime_for(project_id).edit_data_model_field, entity_index, field_index, request.name, request.type, request.description)

    @app.put("/data-model/entities/{entity_index}/fields/{field_index}")
    def edit_data_model_field_legacy(entity_index: int, field_index: int, request: DataFieldRequest):
        return call(runtime_for(DEFAULT_PROJECT_ID).edit_data_model_field, entity_index, field_index, request.name, request.type, request.description)

    @app.delete("/projects/{project_id}/data-model/entities/{entity_index}/fields/{field_index}")
    def remove_data_model_field_scoped(project_id: str, entity_index: int, field_index: int):
        return call(runtime_for(project_id).remove_data_model_field, entity_index, field_index)

    @app.delete("/data-model/entities/{entity_index}/fields/{field_index}")
    def remove_data_model_field_legacy(entity_index: int, field_index: int):
        return call(runtime_for(DEFAULT_PROJECT_ID).remove_data_model_field, entity_index, field_index)

    @app.post("/projects/{project_id}/data-model/relationships")
    def add_data_model_relationship_scoped(project_id: str, request: DataRelationshipRequest):
        return call(runtime_for(project_id).add_data_model_relationship, request.from_entity, request.to_entity, request.cardinality, request.label)

    @app.post("/data-model/relationships")
    def add_data_model_relationship_legacy(request: DataRelationshipRequest):
        return call(runtime_for(DEFAULT_PROJECT_ID).add_data_model_relationship, request.from_entity, request.to_entity, request.cardinality, request.label)

    @app.put("/projects/{project_id}/data-model/relationships/{index}")
    def edit_data_model_relationship_scoped(project_id: str, index: int, request: DataRelationshipRequest):
        return call(runtime_for(project_id).edit_data_model_relationship, index, request.from_entity, request.to_entity, request.cardinality, request.label)

    @app.put("/data-model/relationships/{index}")
    def edit_data_model_relationship_legacy(index: int, request: DataRelationshipRequest):
        return call(runtime_for(DEFAULT_PROJECT_ID).edit_data_model_relationship, index, request.from_entity, request.to_entity, request.cardinality, request.label)

    @app.delete("/projects/{project_id}/data-model/relationships/{index}")
    def remove_data_model_relationship_scoped(project_id: str, index: int):
        return call(runtime_for(project_id).remove_data_model_relationship, index)

    @app.delete("/data-model/relationships/{index}")
    def remove_data_model_relationship_legacy(index: int):
        return call(runtime_for(DEFAULT_PROJECT_ID).remove_data_model_relationship, index)

    @app.get("/projects/{project_id}/data-model/erd")
    def data_model_erd_scoped(project_id: str):
        return {"mermaid_source": call(runtime_for(project_id).render_data_model_erd)}

    @app.get("/data-model/erd")
    def data_model_erd_legacy():
        return {"mermaid_source": call(runtime_for(DEFAULT_PROJECT_ID).render_data_model_erd)}

    @app.post("/projects/{project_id}/workflow/run")
    def run_workflow_scoped(project_id: str):
        return call(runtime_for(project_id).run)

    @app.post("/workflow/run")
    def run_workflow_legacy():
        return call(runtime_for(DEFAULT_PROJECT_ID).run)

    @app.post("/projects/{project_id}/workflow/restart")
    def restart_workflow_scoped(project_id: str):
        return call(runtime_for(project_id).restart_generation)

    @app.post("/workflow/restart")
    def restart_workflow_legacy():
        return call(runtime_for(DEFAULT_PROJECT_ID).restart_generation)

    @app.post("/projects/{project_id}/workflow/steps/{step_id}/run")
    def run_step_scoped(project_id: str, step_id: str):
        return call(runtime_for(project_id).run_step, step_id)

    @app.post("/workflow/steps/{step_id}/run")
    def run_step_legacy(step_id: str):
        return call(runtime_for(DEFAULT_PROJECT_ID).run_step, step_id)

    @app.get("/projects/{project_id}/artifacts")
    def artifacts_scoped(project_id: str):
        return [item.model_dump(mode="json") for item in runtime_for(project_id).store.artifacts.list_latest()]

    @app.get("/artifacts")
    def artifacts_legacy():
        return [item.model_dump(mode="json") for item in runtime_for(DEFAULT_PROJECT_ID).store.artifacts.list_latest()]

    @app.get("/projects/{project_id}/artifacts/{artifact_id}")
    def artifact_scoped(project_id: str, artifact_id: str, version: int | None = None):
        return call(runtime_for(project_id).store.artifacts.get, artifact_id, version)

    @app.get("/artifacts/{artifact_id}")
    def artifact_legacy(artifact_id: str, version: int | None = None):
        return call(runtime_for(DEFAULT_PROJECT_ID).store.artifacts.get, artifact_id, version)

    @app.get("/projects/{project_id}/artifacts/{artifact_id}/versions")
    def artifact_versions_scoped(project_id: str, artifact_id: str):
        return [item.model_dump(mode="json") for item in runtime_for(project_id).store.artifacts.list_versions(artifact_id)]

    @app.get("/artifacts/{artifact_id}/versions")
    def artifact_versions_legacy(artifact_id: str):
        return [item.model_dump(mode="json") for item in runtime_for(DEFAULT_PROJECT_ID).store.artifacts.list_versions(artifact_id)]

    @app.post("/projects/{project_id}/artifacts/{artifact_id}/approve")
    def approve_scoped(project_id: str, artifact_id: str, request: DecisionRequest | None = None):
        request = request or DecisionRequest()
        return call(runtime_for(project_id).approve, artifact_id, request.version, request.reviewer, request.note)

    @app.post("/artifacts/{artifact_id}/approve")
    def approve_legacy(artifact_id: str, request: DecisionRequest | None = None):
        request = request or DecisionRequest()
        return call(runtime_for(DEFAULT_PROJECT_ID).approve, artifact_id, request.version, request.reviewer, request.note)

    @app.post("/projects/{project_id}/artifacts/{artifact_id}/request-changes")
    def request_changes_scoped(project_id: str, artifact_id: str, request: DecisionRequest | None = None):
        request = request or DecisionRequest()
        return call(runtime_for(project_id).request_changes, artifact_id, request.note, request.reviewer, request.version)

    @app.post("/artifacts/{artifact_id}/request-changes")
    def request_changes_legacy(artifact_id: str, request: DecisionRequest | None = None):
        request = request or DecisionRequest()
        return call(runtime_for(DEFAULT_PROJECT_ID).request_changes, artifact_id, request.note, request.reviewer, request.version)

    @app.post("/projects/{project_id}/artifacts/{artifact_id}/retry")
    def retry_scoped(project_id: str, artifact_id: str, request: RetryRequest | None = None):
        return call(runtime_for(project_id).retry, artifact_id, (request or RetryRequest()).instruction)

    @app.post("/artifacts/{artifact_id}/retry")
    def retry_legacy(artifact_id: str, request: RetryRequest | None = None):
        return call(runtime_for(DEFAULT_PROJECT_ID).retry, artifact_id, (request or RetryRequest()).instruction)

    @app.post("/projects/{project_id}/mockup-pages/screens/{screen_id}/retry")
    def retry_screen_scoped(project_id: str, screen_id: str, request: RetryRequest | None = None):
        return call(runtime_for(project_id).retry_screen, screen_id, (request or RetryRequest()).instruction)

    @app.post("/mockup-pages/screens/{screen_id}/retry")
    def retry_screen_legacy(screen_id: str, request: RetryRequest | None = None):
        return call(runtime_for(DEFAULT_PROJECT_ID).retry_screen, screen_id, (request or RetryRequest()).instruction)

    @app.post("/projects/{project_id}/artifacts/{artifact_id}/comments")
    def comment_scoped(project_id: str, artifact_id: str, request: TextRequest):
        return call(runtime_for(project_id).add_comment, artifact_id, request.text, author=request.author, location=request.location)

    @app.post("/artifacts/{artifact_id}/comments")
    def comment_legacy(artifact_id: str, request: TextRequest):
        return call(runtime_for(DEFAULT_PROJECT_ID).add_comment, artifact_id, request.text, author=request.author, location=request.location)

    @app.get("/projects/{project_id}/artifacts/{artifact_id}/comments")
    def comments_scoped(project_id: str, artifact_id: str):
        return [item.model_dump(mode="json") for item in runtime_for(project_id).store.list_comments(artifact_id)]

    @app.get("/artifacts/{artifact_id}/comments")
    def comments_legacy(artifact_id: str):
        return [item.model_dump(mode="json") for item in runtime_for(DEFAULT_PROJECT_ID).store.list_comments(artifact_id)]

    @app.get("/projects/{project_id}/history")
    def history_scoped(project_id: str):
        return [item.model_dump(mode="json") for item in runtime_for(project_id).store.read_events()]

    @app.get("/history")
    def history_legacy():
        return [item.model_dump(mode="json") for item in runtime_for(DEFAULT_PROJECT_ID).store.read_events()]

    @app.get("/projects/{project_id}/requirements/{requirement_id}/impact")
    def requirement_impact_scoped(project_id: str, requirement_id: str):
        return {"requirement_id": requirement_id, "affected_artifacts": runtime_for(project_id).dependencies(requirement_id)}

    @app.get("/requirements/{requirement_id}/impact")
    def requirement_impact_legacy(requirement_id: str):
        return {"requirement_id": requirement_id, "affected_artifacts": runtime_for(DEFAULT_PROJECT_ID).dependencies(requirement_id)}

    return app
