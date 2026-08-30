"""Git-friendly filesystem persistence for project state and artifacts."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable
from uuid import uuid4

import yaml

from .models import (
    Approval,
    ArtifactMetadata,
    ArtifactReference,
    ArtifactStatus,
    Comment,
    DependencyGraph,
    ExecutionEvent,
    ProjectState,
    StoredArtifact,
    utc_now,
)
from .provider_config import load_database_url

if TYPE_CHECKING:
    from .db.store import PostgresProjectStore


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"cannot serialize {type(value)!r}")


def atomic_write(path: Path, content: str) -> None:
    """Write a file through a same-directory temporary file and replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # OneDrive can transiently hold a just-written project-state file on
        # Windows. Retrying preserves the same atomic replacement semantics.
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.03 * (attempt + 1))
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class ProjectPaths:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.design = self.root / ".design"
        self.agents = self.design / "agents"
        self.workflows = self.design / "workflows"
        self.state = self.design / "state"
        self.input = self.design / "input"
        self.artifacts = self.design / "artifacts"
        self.comments = self.design / "review" / "comments"
        self.approvals = self.design / "review" / "approvals"


class ArtifactRegistry:
    _ALLOWED_TRANSITIONS = {
        ArtifactStatus.DRAFT: {ArtifactStatus.DRAFT, ArtifactStatus.GENERATING, ArtifactStatus.GENERATED, ArtifactStatus.FAILED},
        ArtifactStatus.GENERATING: {ArtifactStatus.GENERATED, ArtifactStatus.FAILED},
        ArtifactStatus.GENERATED: {ArtifactStatus.GENERATED, ArtifactStatus.AWAITING_REVIEW, ArtifactStatus.APPROVED, ArtifactStatus.CHANGES_REQUESTED, ArtifactStatus.SUPERSEDED, ArtifactStatus.FAILED},
        ArtifactStatus.AWAITING_REVIEW: {ArtifactStatus.AWAITING_REVIEW, ArtifactStatus.APPROVED, ArtifactStatus.CHANGES_REQUESTED, ArtifactStatus.SUPERSEDED},
        ArtifactStatus.CHANGES_REQUESTED: {ArtifactStatus.CHANGES_REQUESTED, ArtifactStatus.SUPERSEDED, ArtifactStatus.GENERATING},
        ArtifactStatus.FAILED: {ArtifactStatus.FAILED, ArtifactStatus.GENERATING, ArtifactStatus.SUPERSEDED},
        ArtifactStatus.APPROVED: {ArtifactStatus.APPROVED, ArtifactStatus.SUPERSEDED},
        ArtifactStatus.SUPERSEDED: {ArtifactStatus.SUPERSEDED},
    }

    def __init__(self, paths: ProjectPaths):
        self.paths = paths

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-") or "artifact"

    def _artifact_dir(self, logical_id: str) -> Path:
        return self.paths.artifacts / self._safe_name(logical_id)

    def _content_path(self, logical_id: str, version: int) -> Path:
        return self._artifact_dir(logical_id) / f"v{version}.json"

    def _metadata_path(self, logical_id: str, version: int) -> Path:
        return self._artifact_dir(logical_id) / f"v{version}.meta.json"

    def _next_version(self, logical_id: str) -> int:
        versions = [item.version for item in self.list_versions(logical_id)]
        return max(versions, default=0) + 1

    def save(
        self,
        logical_id: str,
        artifact_type: str,
        content: Any,
        *,
        generated_by: dict[str, Any],
        inputs: Iterable[ArtifactReference] = (),
        requirements: Iterable[str] = (),
        status: ArtifactStatus = ArtifactStatus.GENERATED,
        parent_version: int | None = None,
        version: int | None = None,
        comments: Iterable[str] = (),
    ) -> StoredArtifact:
        version = version or self._next_version(logical_id)
        if parent_version is None and version > 1:
            parent_version = version - 1
        metadata = ArtifactMetadata(
            logical_id=logical_id,
            type=artifact_type,
            version=version,
            status=status,
            parent_version=parent_version,
            inputs=list(inputs),
            requirements=list(requirements),
            generated_by=generated_by,
            content_file=self._content_path(logical_id, version).name,
            comments=list(comments),
        )
        content_path = self._content_path(logical_id, version)
        metadata_path = self._metadata_path(logical_id, version)
        atomic_write(content_path, json.dumps(content, indent=2, ensure_ascii=False, default=_json_default) + "\n")
        atomic_write(metadata_path, metadata.model_dump_json(indent=2) + "\n")
        return StoredArtifact(metadata=metadata, content=content)

    def get(self, logical_id: str, version: int | None = None) -> StoredArtifact:
        if version is None:
            versions = self.list_versions(logical_id)
            if not versions:
                raise FileNotFoundError(f"artifact not found: {logical_id}")
            version = max(item.version for item in versions)
        metadata_path = self._metadata_path(logical_id, version)
        content_path = self._content_path(logical_id, version)
        metadata = ArtifactMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))
        content = json.loads(content_path.read_text(encoding="utf-8"))
        return StoredArtifact(metadata=metadata, content=content)

    def list_versions(self, logical_id: str) -> list[ArtifactMetadata]:
        directory = self._artifact_dir(logical_id)
        if not directory.exists():
            return []
        metadata: list[ArtifactMetadata] = []
        for path in directory.glob("v*.meta.json"):
            try:
                metadata.append(ArtifactMetadata.model_validate_json(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return sorted(metadata, key=lambda item: item.version)

    def list_latest(self) -> list[ArtifactMetadata]:
        if not self.paths.artifacts.exists():
            return []
        latest: list[ArtifactMetadata] = []
        for directory in self.paths.artifacts.iterdir():
            if directory.is_dir():
                versions = self.list_versions(directory.name)
                if versions:
                    latest.append(versions[-1])
        return sorted(latest, key=lambda item: item.logical_id)

    def update_status(self, logical_id: str, status: ArtifactStatus, version: int | None = None, *, error: str | None = None) -> ArtifactMetadata:
        artifact = self.get(logical_id, version)
        if status not in self._ALLOWED_TRANSITIONS[artifact.metadata.status]:
            raise ValueError(f"invalid artifact status transition: {artifact.metadata.status.value} -> {status.value}")
        artifact.metadata.status = status
        artifact.metadata.error = error
        atomic_write(self._metadata_path(logical_id, artifact.metadata.version), artifact.metadata.model_dump_json(indent=2) + "\n")
        return artifact.metadata

    def attach_approval(self, logical_id: str, approval_id: str, version: int | None = None) -> ArtifactMetadata:
        artifact = self.get(logical_id, version)
        if approval_id not in artifact.metadata.approvals:
            artifact.metadata.approvals.append(approval_id)
        atomic_write(self._metadata_path(logical_id, artifact.metadata.version), artifact.metadata.model_dump_json(indent=2) + "\n")
        return artifact.metadata


class ProjectStore:
    def __init__(self, root: Path | str):
        self.paths = ProjectPaths(root)
        self.artifacts = ArtifactRegistry(self.paths)

    def initialize(self, project_id: str | None = None) -> ProjectState:
        for directory in (
            self.paths.agents,
            self.paths.workflows,
            self.paths.state,
            self.paths.input,
            self.paths.artifacts,
            self.paths.comments,
            self.paths.approvals,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        state_path = self.paths.state / "project-state.yaml"
        if state_path.exists():
            return self.load_state()
        state = ProjectState(project_id=project_id or self.paths.root.name)
        self.save_state(state)
        self.save_dependency_graph(DependencyGraph())
        history = self.paths.state / "execution-history.jsonl"
        history.touch(exist_ok=True)
        return state

    def is_initialized(self) -> bool:
        return (self.paths.state / "project-state.yaml").exists()

    def load_state(self) -> ProjectState:
        path = self.paths.state / "project-state.yaml"
        if not path.exists():
            raise FileNotFoundError("project is not initialized; run `design init`")
        return ProjectState.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def save_state(self, state: ProjectState) -> None:
        state.updated_at = utc_now()
        atomic_write(self.paths.state / "project-state.yaml", yaml.safe_dump(state.model_dump(mode="json"), sort_keys=False))

    def load_dependency_graph(self) -> DependencyGraph:
        path = self.paths.state / "dependency-graph.yaml"
        if not path.exists():
            return DependencyGraph()
        return DependencyGraph.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def save_dependency_graph(self, graph: DependencyGraph) -> None:
        atomic_write(self.paths.state / "dependency-graph.yaml", yaml.safe_dump(graph.model_dump(mode="json"), sort_keys=False))

    def append_event(self, event_type: str, *, step_id: str | None = None, artifact_id: str | None = None, details: dict[str, Any] | None = None) -> ExecutionEvent:
        event = ExecutionEvent(event_id=f"event-{uuid4().hex[:12]}", event_type=event_type, step_id=step_id, artifact_id=artifact_id, details=details or {})
        path = self.paths.state / "execution-history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(event.model_dump_json() + "\n")
        return event

    def read_events(self) -> list[ExecutionEvent]:
        path = self.paths.state / "execution-history.jsonl"
        if not path.exists():
            return []
        events: list[ExecutionEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(ExecutionEvent.model_validate_json(line))
        return events

    def save_comment(self, comment: Comment) -> None:
        atomic_write(self.paths.comments / f"{comment.id}.json", comment.model_dump_json(indent=2) + "\n")

    def list_comments(self, artifact_id: str | None = None) -> list[Comment]:
        if not self.paths.comments.exists():
            return []
        comments: list[Comment] = []
        for path in self.paths.comments.glob("*.json"):
            try:
                comment = Comment.model_validate_json(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if artifact_id is None or comment.artifact_id == artifact_id:
                comments.append(comment)
        return sorted(comments, key=lambda item: item.created_at)

    def save_approval(self, approval: Approval) -> None:
        atomic_write(self.paths.approvals / f"{approval.id}.json", approval.model_dump_json(indent=2) + "\n")

    def save_task(self, task) -> None:
        tasks = self.paths.state / "tasks"
        atomic_write(tasks / f"{task.id}.json", task.model_dump_json(indent=2) + "\n")

    def list_tasks(self) -> list:
        tasks = self.paths.state / "tasks"
        if not tasks.exists():
            return []
        from .models import Task
        result = []
        for path in tasks.glob("*.json"):
            try:
                result.append(Task.model_validate_json(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return sorted(result, key=lambda item: item.id)


def build_project_store(root: Path | str, database_url: str | None = None) -> "ProjectStore | PostgresProjectStore":
    """Return the filesystem or Postgres-backed store, whichever applies.

    A `DATABASE_URL` (real environment, or the project's `.env`) selects the
    Postgres-backed store; its absence keeps today's filesystem behavior
    unchanged. This is the only place that decides between the two -- see
    `runtime.DesignRuntime.__init__`, the sole caller.
    """
    if database_url is None:
        database_url = load_database_url(root)
    if database_url:
        from .db.store import PostgresProjectStore

        return PostgresProjectStore(root, database_url)
    return ProjectStore(root)
