"""Minimal FastAPI facade over the shared DesignRuntime."""

from __future__ import annotations

import base64
import html
import io
import json
import logging
import queue
import re
import threading
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("design_pipeline.api")

from pydantic import BaseModel, Field, model_validator

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, Response, StreamingResponse
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


class AddMockupScreenRequest(BaseModel):
    description: str = Field(min_length=1)
    link_from_screen_id: str | None = None


class SplitMockupScreenRequest(BaseModel):
    extract_description: str = Field(min_length=1)


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
            # Log the full detail server-side too, not just in the HTTP
            # response -- a 400's body is easy to lose (browser dev tools
            # closed, notice dismissed) and this is often the only trace of
            # exactly what a live model returned when it didn't match the
            # declared contract.
            logger.warning("%s failed: %s", function.__qualname__, exc)
            raise HTTPException(status_code=404 if isinstance(exc, FileNotFoundError) else 400, detail=str(exc)) from exc
        except LiveProviderError as exc:
            # The selected model provider's API itself failed (network,
            # rate limit, quota, malformed response) -- a clean, retryable
            # error for the caller, not an unhandled 500.
            logger.warning("%s failed: %s", function.__qualname__, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # A full workflow run/restart executes every remaining step synchronously
    # inside runtime.run()/restart_generation() -- each step can be a live LLM
    # call up to DESIGN_PIPELINE_TIMEOUT_SECONDS, retried up to 3x on
    # validation failure, across several steps. Blocking the HTTP request on
    # that (the old behavior) works locally but exceeds Render's edge-proxy
    # timeout in production: the proxy serves the browser a 503 and drops the
    # connection while this backend keeps running unaware, later logging a
    # normal-looking 200 that never reaches the now-disconnected client.
    # Kick the run off in a background thread instead and let the frontend
    # poll /status (already exposes live workflow_status) until it's done.
    _running_projects: set[str] = set()
    _running_lock = threading.Lock()

    def start_background(project_id: str, fn, *args, grace_period: float = 2.0) -> dict:
        with _running_lock:
            if project_id in _running_projects:
                raise ValueError(f"A workflow run is already in progress for project '{project_id}'")
            _running_projects.add(project_id)

        # Every run()/restart_generation()/run_step() call starts with a fast,
        # non-LLM precondition check (initialized? live provider configured?
        # valid step id?) before doing any slow work. Losing that fast-fail
        # into the background thread would turn a would-be 400/404/502 into a
        # misleading 200 "started" -- so give the thread a brief grace period
        # to hit one of those before treating it as a genuine long-running
        # start. A run that's still going after this window is reported as
        # started; the frontend discovers success/failure via /status polling.
        outcome: queue.Queue = queue.Queue(maxsize=1)

        def _run():
            try:
                fn(*args)
            except Exception as exc:
                # runtime.run()/run_step() already catch step-level failures
                # internally and persist WorkflowStatus.FAILED -- this is
                # only a backstop for anything that escapes that (e.g. a
                # crash before the step loop even starts, or the fast
                # precondition check below), so it doesn't vanish silently in
                # a background thread with no HTTP response left to report it
                # through.
                logger.exception("Background workflow run failed for project '%s'", project_id)
                outcome.put(exc)
            else:
                outcome.put(None)
            finally:
                with _running_lock:
                    _running_projects.discard(project_id)

        threading.Thread(target=_run, daemon=True).start()
        try:
            error = outcome.get(timeout=grace_period)
        except queue.Empty:
            return {"status": "started", "project_id": project_id}
        if error is not None:
            raise error
        return {"status": "started", "project_id": project_id}

    def _safe_export_filename(label: str, fallback: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-")
        return slug or fallback

    def _zip_response(entries: dict[str, str], download_name: str) -> StreamingResponse:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for filename, content in entries.items():
                archive.writestr(filename, content)
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{download_name}"'})

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

    @app.patch("/projects/{project_id}")
    def rename_project(project_id: str, request: ProjectCreation):
        return call(registry.rename_project, project_id, request.name)

    @app.delete("/projects/{project_id}", status_code=204)
    def delete_project(project_id: str):
        call(registry.delete_project, project_id)
        return None

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

    def _export_data_model(project_id: str) -> StreamingResponse:
        runtime = runtime_for(project_id)
        try:
            data_model = runtime.store.artifacts.get("data-model").content
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # erd.mmd -- the diagram's actual portable form (Mermaid source),
        # not a rendered image: this service has no server-side rendering
        # engine (mermaid.min.js only runs client-side, in the browser
        # already looking at it), and a raster/vector export would mean
        # adding a headless-browser dependency for one export button. A
        # .mmd file pastes straight into mermaid.live, and GitHub/Notion/
        # most modern doc tools render ```mermaid fences natively -- so
        # this is usable as-is, not a placeholder for a "real" export.
        mermaid_source = call(runtime.render_data_model_erd)
        entries = {"data-model.json": json.dumps(data_model, indent=2), "erd.mmd": mermaid_source}
        return _zip_response(entries, f"data-model-{_safe_export_filename(project_id, 'export')}.zip")

    @app.get("/projects/{project_id}/data-model/export")
    def export_data_model_scoped(project_id: str):
        return _export_data_model(project_id)

    @app.get("/data-model/export")
    def export_data_model_legacy():
        return _export_data_model(DEFAULT_PROJECT_ID)

    # A share-one-file-over-Teams/Slack export, per direct user request
    # after a first version (one .html per screen + an index.html, zipped)
    # turned out to only work if the recipient kept every file together --
    # sharing just index.html on its own left every link 404ing, since it
    # only pointed at sibling files that weren't there. Every screen is
    # instead embedded inline, one per <iframe srcdoc="...">, in ONE HTML
    # document -- genuinely one file, nothing else required at the far end.
    _MOCKUP_EXPORT_SCRIPT_STRIP = re.compile(r"<script\b[^>]*>([\s\S]*?)</script>", re.IGNORECASE)
    # Same click-to-postMessage contract as app.js's MOCKUP_BRIDGE (minus
    # the comment-mode/pin-locate machinery, meaningless with no review app
    # on the other end to talk to) -- kept in sync by hand: one is browser
    # JS driving a live iframe, this is Python building a static file, so
    # there's no way to literally share the source between them.
    _MOCKUP_EXPORT_BRIDGE = (
        "<script>(function(){"
        "document.addEventListener('click',function(e){"
        "try{"
        "var t=e.target.closest&&e.target.closest('[data-goto]');"
        "if(t){e.preventDefault();parent.postMessage({type:'mockup-goto',screen_id:t.getAttribute('data-goto')},'*');return;}"
        "var a=e.target.closest&&e.target.closest('a[href]');"
        "if(a){var h=a.getAttribute('href');if(!h||h.charAt(0)==='#')e.preventDefault();"
        "else if(!/^(https?:|mailto:|tel:)/.test(h))e.preventDefault();}"
        "}catch(err){}"
        "},true);"
        "document.addEventListener('submit',function(e){e.preventDefault();},true);"
        "})();</script>"
    )

    def _prepare_export_page_html(page_html: str) -> str:
        """Mirror app.js's pageHtml(): strip model-authored <script> blocks
        that would navigate the iframe away or rebind data-goto to their
        own handler (both defeat the bridge, observed live in the app
        itself), then append the bridge above."""
        def strip(match: re.Match) -> str:
            body = match.group(1)
            if re.search(r"\blocation\s*\.\s*(?:href|assign|replace)\b", body):
                return ""
            if re.search(r"querySelectorAll\s*\(\s*['\"]\[data-goto\]", body):
                return ""
            return match.group(0)
        cleaned = _MOCKUP_EXPORT_SCRIPT_STRIP.sub(strip, page_html)
        return cleaned.replace("</body>", _MOCKUP_EXPORT_BRIDGE + "</body>") if "</body>" in cleaned else cleaned + _MOCKUP_EXPORT_BRIDGE

    def _export_mockups(project_id: str) -> Response:
        runtime = runtime_for(project_id)
        try:
            pages = runtime.store.artifacts.get("mockup-pages").content
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not pages:
            raise HTTPException(status_code=404, detail="mockup-pages has no screens to export")
        names: dict[str, str] = {}
        try:
            spec = runtime.store.artifacts.get("mockup-spec").content
            names = {screen["id"]: screen.get("name") or screen["id"] for screen in spec.get("screens", [])}
        except FileNotFoundError:
            pass  # fall back to raw screen_id labels below

        nav_buttons, frames = [], []
        for index, page in enumerate(pages):
            screen_id = page["screen_id"]
            active_class = ' class="active"' if index == 0 else ""
            label = html.escape(names.get(screen_id, screen_id))
            nav_buttons.append(f'<button type="button" data-screen-link="{html.escape(screen_id)}"{active_class}>{label}</button>')
            srcdoc = html.escape(_prepare_export_page_html(page["html"]))
            frames.append(f'<iframe data-screen="{html.escape(screen_id)}"{active_class} sandbox="allow-scripts" srcdoc="{srcdoc}"></iframe>')

        title = html.escape(names.get(pages[0]["screen_id"], project_id)) if pages else html.escape(project_id)
        document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title} mockup export</title>
<style>
html,body{{margin:0;height:100%;font-family:system-ui,-apple-system,Segoe UI,sans-serif;}}
body{{display:flex;}}
#nav{{width:230px;flex:0 0 auto;overflow-y:auto;background:#12213f;padding:14px 10px;box-sizing:border-box;}}
#nav button{{display:block;width:100%;text-align:left;border:0;background:transparent;color:#bbcae8;padding:10px 12px;border-radius:7px;font:inherit;font-size:13px;cursor:pointer;margin-bottom:2px;}}
#nav button:hover,#nav button.active{{background:#233b65;color:#fff;}}
#frames{{flex:1 1 auto;position:relative;}}
#frames iframe{{position:absolute;inset:0;width:100%;height:100%;border:0;display:none;}}
#frames iframe.active{{display:block;}}
</style></head>
<body>
<nav id="nav">{"".join(nav_buttons)}</nav>
<div id="frames">{"".join(frames)}</div>
<script>(function(){{
function activate(id){{
  document.querySelectorAll('#frames iframe').forEach(function(f){{f.classList.toggle('active',f.dataset.screen===id);}});
  document.querySelectorAll('#nav button').forEach(function(b){{b.classList.toggle('active',b.dataset.screenLink===id);}});
}}
document.getElementById('nav').addEventListener('click',function(e){{
  var b=e.target.closest('button[data-screen-link]');
  if(b)activate(b.dataset.screenLink);
}});
window.addEventListener('message',function(e){{
  if(e.data&&e.data.type==='mockup-goto'&&e.data.screen_id)activate(e.data.screen_id);
}});
}})();</script>
</body></html>"""
        filename = f"mockups-{_safe_export_filename(project_id, 'export')}.html"
        return Response(content=document, media_type="text/html", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/projects/{project_id}/mockup-pages/export")
    def export_mockups_scoped(project_id: str):
        return _export_mockups(project_id)

    @app.get("/mockup-pages/export")
    def export_mockups_legacy():
        return _export_mockups(DEFAULT_PROJECT_ID)

    @app.post("/projects/{project_id}/workflow/run")
    def run_workflow_scoped(project_id: str):
        return call(start_background, project_id, runtime_for(project_id).run)

    @app.post("/workflow/run")
    def run_workflow_legacy():
        return call(start_background, DEFAULT_PROJECT_ID, runtime_for(DEFAULT_PROJECT_ID).run)

    @app.post("/projects/{project_id}/workflow/restart")
    def restart_workflow_scoped(project_id: str):
        return call(start_background, project_id, runtime_for(project_id).restart_generation)

    @app.post("/workflow/restart")
    def restart_workflow_legacy():
        return call(start_background, DEFAULT_PROJECT_ID, runtime_for(DEFAULT_PROJECT_ID).restart_generation)

    @app.post("/projects/{project_id}/workflow/steps/{step_id}/run")
    def run_step_scoped(project_id: str, step_id: str):
        return call(start_background, project_id, runtime_for(project_id).run_step, step_id)

    @app.post("/workflow/steps/{step_id}/run")
    def run_step_legacy(step_id: str):
        return call(start_background, DEFAULT_PROJECT_ID, runtime_for(DEFAULT_PROJECT_ID).run_step, step_id)

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

    @app.post("/projects/{project_id}/mockup-pages/screens/add")
    def add_mockup_screen_scoped(project_id: str, request: AddMockupScreenRequest):
        return call(runtime_for(project_id).add_mockup_screen, request.description, request.link_from_screen_id)

    @app.post("/mockup-pages/screens/add")
    def add_mockup_screen_legacy(request: AddMockupScreenRequest):
        return call(runtime_for(DEFAULT_PROJECT_ID).add_mockup_screen, request.description, request.link_from_screen_id)

    @app.post("/projects/{project_id}/mockup-pages/screens/{screen_id}/split")
    def split_mockup_screen_scoped(project_id: str, screen_id: str, request: SplitMockupScreenRequest):
        return call(runtime_for(project_id).split_mockup_screen, screen_id, request.extract_description)

    @app.post("/mockup-pages/screens/{screen_id}/split")
    def split_mockup_screen_legacy(screen_id: str, request: SplitMockupScreenRequest):
        return call(runtime_for(DEFAULT_PROJECT_ID).split_mockup_screen, screen_id, request.extract_description)

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
