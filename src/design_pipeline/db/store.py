"""Postgres-backed equivalents of `storage.ProjectStore` / `ArtifactRegistry`.

Both classes here expose the exact same public methods as their filesystem
counterparts (see `storage.py`) so `DesignRuntime` -- and everything built
on it -- can use either backend interchangeably. `PostgresProjectStore`
still keeps a `ProjectPaths` for the config/staging files that remain on
disk in either mode: agent and workflow YAML definitions, and the BRD
upload staging file (see `storage.build_project_store` for why those stay
filesystem-only).

Every table row is scoped by `project_id` (see `db/schema.py`); each
store instance is bound to one project at construction time and every
query/insert filters by that id.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from ..models import (
    Approval,
    ArtifactMetadata,
    ArtifactReference,
    ArtifactStatus,
    Comment,
    DependencyGraph,
    ExecutionEvent,
    ProjectState,
    StepStatus,
    StoredArtifact,
    WorkflowStatus,
    utc_now,
)
from ..storage import DEFAULT_PROJECT_ID, ArtifactRegistry, ProjectPaths, _safe_project_id
from .engine import build_engine
from .migrate import ensure_schema
from .schema import app_settings, approvals, artifacts, comments, dependency_graph, execution_events, metadata as db_metadata, project_config, project_state, projects, tasks


def _metadata_row(project_id: str, metadata: ArtifactMetadata, content: Any) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "logical_id": metadata.logical_id,
        "version": metadata.version,
        "type": metadata.type,
        "status": metadata.status.value,
        "parent_version": metadata.parent_version,
        "inputs": [item.model_dump(mode="json") for item in metadata.inputs],
        "requirements": metadata.requirements,
        "generated_by": metadata.generated_by.model_dump(mode="json"),
        "comments": metadata.comments,
        "approvals": metadata.approvals,
        "error": metadata.error,
        "content": content,
    }


def _row_to_metadata(row: Any) -> ArtifactMetadata:
    return ArtifactMetadata(
        logical_id=row["logical_id"],
        type=row["type"],
        version=row["version"],
        status=ArtifactStatus(row["status"]),
        parent_version=row["parent_version"],
        inputs=[ArtifactReference.model_validate(item) for item in row["inputs"]],
        requirements=list(row["requirements"]),
        generated_by=row["generated_by"],
        # No file backs a Postgres-stored artifact; kept only so the shared
        # `ArtifactMetadata` contract (used by the API/CLI either way) holds.
        content_file=f"v{row['version']}.json",
        comments=list(row["comments"]),
        approvals=list(row["approvals"]),
        error=row["error"],
    )


class PostgresArtifactRegistry:
    def __init__(self, engine: Engine, project_id: str):
        self._engine = engine
        self._project_id = project_id

    def _next_version(self, logical_id: str) -> int:
        with self._engine.connect() as conn:
            highest = conn.execute(
                select(func.max(artifacts.c.version)).where(
                    artifacts.c.project_id == self._project_id,
                    artifacts.c.logical_id == logical_id,
                )
            ).scalar()
        return (highest or 0) + 1

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
        artifact_metadata = ArtifactMetadata(
            logical_id=logical_id,
            type=artifact_type,
            version=version,
            status=status,
            parent_version=parent_version,
            inputs=list(inputs),
            requirements=list(requirements),
            generated_by=generated_by,
            content_file=f"v{version}.json",
            comments=list(comments),
        )
        row = _metadata_row(self._project_id, artifact_metadata, content)
        with self._engine.begin() as conn:
            conn.execute(
                pg_insert(artifacts)
                .values(**row)
                .on_conflict_do_update(index_elements=["project_id", "logical_id", "version"], set_=row)
            )
        return StoredArtifact(metadata=artifact_metadata, content=content)

    def get(self, logical_id: str, version: int | None = None) -> StoredArtifact:
        stmt = select(artifacts).where(
            artifacts.c.project_id == self._project_id,
            artifacts.c.logical_id == logical_id,
        )
        stmt = stmt.order_by(artifacts.c.version.desc()).limit(1) if version is None else stmt.where(artifacts.c.version == version)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        if row is None:
            raise FileNotFoundError(f"artifact not found: {logical_id}")
        return StoredArtifact(metadata=_row_to_metadata(row), content=row["content"])

    def list_versions(self, logical_id: str) -> list[ArtifactMetadata]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(artifacts).where(
                    artifacts.c.project_id == self._project_id,
                    artifacts.c.logical_id == logical_id,
                ).order_by(artifacts.c.version)
            ).mappings().all()
        return [_row_to_metadata(row) for row in rows]

    def list_latest(self) -> list[ArtifactMetadata]:
        latest_versions = (
            select(artifacts.c.logical_id, func.max(artifacts.c.version).label("version"))
            .where(artifacts.c.project_id == self._project_id)
            .group_by(artifacts.c.logical_id)
            .subquery()
        )
        stmt = select(artifacts).join(
            latest_versions,
            and_(
                artifacts.c.logical_id == latest_versions.c.logical_id,
                artifacts.c.version == latest_versions.c.version,
            ),
        ).where(artifacts.c.project_id == self._project_id).order_by(artifacts.c.logical_id)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [_row_to_metadata(row) for row in rows]

    def update_status(self, logical_id: str, status: ArtifactStatus, version: int | None = None, *, error: str | None = None) -> ArtifactMetadata:
        artifact = self.get(logical_id, version)
        if status not in ArtifactRegistry._ALLOWED_TRANSITIONS[artifact.metadata.status]:
            raise ValueError(f"invalid artifact status transition: {artifact.metadata.status.value} -> {status.value}")
        with self._engine.begin() as conn:
            conn.execute(
                update(artifacts)
                .where(
                    artifacts.c.project_id == self._project_id,
                    artifacts.c.logical_id == logical_id,
                    artifacts.c.version == artifact.metadata.version,
                )
                .values(status=status.value, error=error)
            )
        artifact.metadata.status = status
        artifact.metadata.error = error
        return artifact.metadata

    def attach_approval(self, logical_id: str, approval_id: str, version: int | None = None) -> ArtifactMetadata:
        artifact = self.get(logical_id, version)
        if approval_id not in artifact.metadata.approvals:
            artifact.metadata.approvals.append(approval_id)
        with self._engine.begin() as conn:
            conn.execute(
                update(artifacts)
                .where(
                    artifacts.c.project_id == self._project_id,
                    artifacts.c.logical_id == logical_id,
                    artifacts.c.version == artifact.metadata.version,
                )
                .values(approvals=artifact.metadata.approvals)
            )
        return artifact.metadata

    def attach_comment(self, logical_id: str, comment_id: str, version: int | None = None) -> ArtifactMetadata:
        artifact = self.get(logical_id, version)
        if comment_id not in artifact.metadata.comments:
            artifact.metadata.comments.append(comment_id)
        with self._engine.begin() as conn:
            conn.execute(
                update(artifacts)
                .where(
                    artifacts.c.project_id == self._project_id,
                    artifacts.c.logical_id == logical_id,
                    artifacts.c.version == artifact.metadata.version,
                )
                .values(comments=artifact.metadata.comments)
            )
        return artifact.metadata


class PostgresProjectStore:
    def __init__(self, root: Path | str, database_url: str, project_id: str = DEFAULT_PROJECT_ID):
        self.paths = ProjectPaths(root, project_id)
        self._project_id = self.paths.project_id
        # Exposed (not just `_engine`) so `DesignRuntime` can reach the raw
        # URL for the deployment-wide `app_settings` helpers below, which
        # aren't scoped to one project and so don't belong on this class.
        self.database_url = database_url
        self._engine = build_engine(database_url)
        # Idempotent: safe to call on every construction (e.g. every CLI
        # invocation), including against a brand new, empty database.
        db_metadata.create_all(self._engine, checkfirst=True)
        # create_all above only creates brand-new tables -- it does nothing
        # for a table that already existed with an older column set (see
        # migrate.py's docstring for the production incident this caused).
        ensure_schema(self._engine)
        self.artifacts = PostgresArtifactRegistry(self._engine, self._project_id)

    def initialize(self, project_id: str | None = None) -> ProjectState:
        for directory in (self.paths.agents, self.paths.workflows, self.paths.input):
            directory.mkdir(parents=True, exist_ok=True)
        if self.is_initialized():
            return self.load_state()
        state = ProjectState(project_id=project_id or self._project_id)
        self.save_state(state)
        self.save_dependency_graph(DependencyGraph())
        return state

    def is_initialized(self) -> bool:
        with self._engine.connect() as conn:
            return conn.execute(
                select(project_state.c.project_id).where(project_state.c.project_id == self._project_id)
            ).first() is not None

    def load_state(self) -> ProjectState:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(project_state).where(project_state.c.project_id == self._project_id)
            ).mappings().first()
        if row is None:
            raise FileNotFoundError("project is not initialized; run `design init`")
        return ProjectState(
            project_id=row["project_id"],
            workflow_id=row["workflow_id"],
            workflow_status=WorkflowStatus(row["workflow_status"]),
            step_states={key: StepStatus(value) for key, value in row["step_states"].items()},
            pending_approvals=list(row["pending_approvals"]),
            updated_at=row["updated_at"],
        )

    def save_state(self, state: ProjectState) -> None:
        state.updated_at = utc_now()
        row = {
            "project_id": self._project_id,
            "workflow_id": state.workflow_id,
            "workflow_status": state.workflow_status.value,
            "step_states": {key: value.value for key, value in state.step_states.items()},
            "pending_approvals": state.pending_approvals,
            "updated_at": state.updated_at,
        }
        with self._engine.begin() as conn:
            conn.execute(pg_insert(project_state).values(**row).on_conflict_do_update(index_elements=["project_id"], set_=row))

    def load_dependency_graph(self) -> DependencyGraph:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(dependency_graph).where(dependency_graph.c.project_id == self._project_id)
            ).mappings().first()
        return DependencyGraph(requirements=row["requirements"]) if row else DependencyGraph()

    def save_dependency_graph(self, graph: DependencyGraph) -> None:
        row = {"project_id": self._project_id, "requirements": graph.requirements}
        with self._engine.begin() as conn:
            conn.execute(pg_insert(dependency_graph).values(**row).on_conflict_do_update(index_elements=["project_id"], set_={"requirements": graph.requirements}))

    def append_event(self, event_type: str, *, step_id: str | None = None, artifact_id: str | None = None, details: dict[str, Any] | None = None) -> ExecutionEvent:
        event = ExecutionEvent(event_id=f"event-{uuid4().hex[:12]}", event_type=event_type, step_id=step_id, artifact_id=artifact_id, details=details or {})
        with self._engine.begin() as conn:
            conn.execute(execution_events.insert().values(
                project_id=self._project_id,
                event_id=event.event_id,
                event_type=event.event_type,
                timestamp=event.timestamp,
                step_id=event.step_id,
                artifact_id=event.artifact_id,
                details=event.details,
            ))
        return event

    def read_events(self) -> list[ExecutionEvent]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(execution_events).where(execution_events.c.project_id == self._project_id).order_by(execution_events.c.seq)
            ).mappings().all()
        return [
            ExecutionEvent(event_id=row["event_id"], event_type=row["event_type"], timestamp=row["timestamp"], step_id=row["step_id"], artifact_id=row["artifact_id"], details=row["details"])
            for row in rows
        ]

    def save_comment(self, comment: Comment) -> None:
        row = {
            "id": comment.id,
            "project_id": self._project_id,
            "artifact_id": comment.artifact_id,
            "text": comment.text,
            "author": comment.author,
            "location": comment.location,
            "status": comment.status,
            "created_at": comment.created_at,
            "resolved_at": comment.resolved_at,
        }
        with self._engine.begin() as conn:
            conn.execute(pg_insert(comments).values(**row).on_conflict_do_update(index_elements=["id"], set_=row))

    def list_comments(self, artifact_id: str | None = None) -> list[Comment]:
        stmt = select(comments).where(comments.c.project_id == self._project_id)
        if artifact_id is not None:
            stmt = stmt.where(comments.c.artifact_id == artifact_id)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt.order_by(comments.c.created_at)).mappings().all()
        return [Comment(id=row["id"], artifact_id=row["artifact_id"], text=row["text"], author=row["author"], location=row["location"], status=row["status"], created_at=row["created_at"], resolved_at=row["resolved_at"]) for row in rows]

    def save_approval(self, approval: Approval) -> None:
        row = {
            "id": approval.id,
            "project_id": self._project_id,
            "artifact_id": approval.artifact_id,
            "version": approval.version,
            "decision": approval.decision,
            "reviewer": approval.reviewer,
            "note": approval.note,
            "created_at": approval.created_at,
        }
        with self._engine.begin() as conn:
            conn.execute(pg_insert(approvals).values(**row).on_conflict_do_update(index_elements=["id"], set_=row))

    def save_task(self, task) -> None:
        row = {
            "id": task.id,
            "project_id": self._project_id,
            "objective": task.objective,
            "step_id": task.step_id,
            "handoff": task.handoff.model_dump(mode="json") if task.handoff else None,
            "status": task.status.value,
            "attempts": task.attempts,
        }
        with self._engine.begin() as conn:
            conn.execute(pg_insert(tasks).values(**row).on_conflict_do_update(index_elements=["id"], set_=row))

    def list_tasks(self) -> list:
        from ..models import Task

        with self._engine.connect() as conn:
            rows = conn.execute(
                select(tasks).where(tasks.c.project_id == self._project_id).order_by(tasks.c.id)
            ).mappings().all()
        return [Task(id=row["id"], objective=row["objective"], step_id=row["step_id"], handoff=row["handoff"], status=row["status"], attempts=row["attempts"]) for row in rows]

    # ---- project_config: durable mirror of the on-disk agent/workflow ----
    # YAML and the staged BRD (see schema.py's docstring on project_config
    # for why this exists).
    def load_config(self) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(project_config).where(project_config.c.project_id == self._project_id)
            ).mappings().first()
        return dict(row) if row else None

    def save_config(self, agent_files: dict[str, str], workflow_file: str) -> None:
        # Upsert that never touches staged_brd_* -- those are written
        # independently by save_staged_brd, on its own schedule (whenever a
        # BRD is uploaded), not whenever agent/workflow config is synced.
        with self._engine.begin() as conn:
            conn.execute(
                pg_insert(project_config)
                .values(project_id=self._project_id, agent_files=agent_files, workflow_file=workflow_file, staged_brd_filename=None, staged_brd_content=None, updated_at=utc_now())
                .on_conflict_do_update(
                    index_elements=["project_id"],
                    set_={"agent_files": agent_files, "workflow_file": workflow_file, "updated_at": utc_now()},
                )
            )

    def save_staged_brd(self, filename: str, content: str) -> None:
        # Relies on a project_config row already existing (agent_files/
        # workflow_file are NOT NULL) -- true in practice, since every
        # caller of this reaches it through `_require_initialized()`, which
        # always seeds the row first.
        with self._engine.begin() as conn:
            conn.execute(
                update(project_config)
                .where(project_config.c.project_id == self._project_id)
                .values(staged_brd_filename=filename, staged_brd_content=content, updated_at=utc_now())
            )


@lru_cache(maxsize=8)
def _cached_engine(database_url: str) -> Engine:
    # `load_provider_settings`/`update_provider` call the two functions
    # below on essentially every API request (they're read on every
    # `status()`) -- a fresh Engine (and its own connection pool) per call
    # would be wasteful. Cached per URL and reused for the life of the
    # process; SQLAlchemy Engines are meant to be long-lived and are
    # threadsafe to share this way.
    return build_engine(database_url)


_app_settings_schema_ready: set[str] = set()


def _ready_engine(database_url: str) -> Engine:
    engine = _cached_engine(database_url)
    if database_url not in _app_settings_schema_ready:
        db_metadata.create_all(engine, checkfirst=True)
        ensure_schema(engine)
        _app_settings_schema_ready.add(database_url)
    return engine


def pg_get_app_setting(database_url: str, key: str) -> str | None:
    """Read one deployment-wide setting (see schema.py's `app_settings`)."""
    engine = _ready_engine(database_url)
    with engine.connect() as conn:
        row = conn.execute(select(app_settings.c.value).where(app_settings.c.key == key)).first()
    return row[0] if row else None


def pg_set_app_setting(database_url: str, key: str, value: str) -> None:
    engine = _ready_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            pg_insert(app_settings)
            .values(key=key, value=value, updated_at=utc_now())
            .on_conflict_do_update(index_elements=["key"], set_={"value": value, "updated_at": utc_now()})
        )


class PostgresProjectRegistry:
    """Postgres equivalent of `storage.ProjectRegistry`. Same interface."""

    def __init__(self, database_url: str):
        self._engine = build_engine(database_url)
        db_metadata.create_all(self._engine, checkfirst=True)
        ensure_schema(self._engine)

    def list_projects(self) -> list[dict[str, str]]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(projects).order_by(projects.c.id)).mappings().all()
        return [{"id": row["id"], "name": row["name"]} for row in rows]

    def create_project(self, name: str) -> dict[str, str]:
        project_id = _safe_project_id(name)
        with self._engine.begin() as conn:
            conn.execute(
                pg_insert(projects)
                .values(id=project_id, name=name, created_at=utc_now())
                .on_conflict_do_nothing(index_elements=["id"])
            )
        return {"id": project_id, "name": name}

    def rename_project(self, project_id: str, name: str) -> dict[str, str]:
        with self._engine.begin() as conn:
            result = conn.execute(update(projects).where(projects.c.id == project_id).values(name=name))
        if result.rowcount == 0:
            raise FileNotFoundError(f"project not found: {project_id}")
        return {"id": project_id, "name": name}

    def delete_project(self, project_id: str) -> None:
        # Every other table is scoped by project_id -- clear all of them
        # before the projects row itself, in one transaction, so a crash
        # partway through can't leave orphaned rows under a project_id that
        # no longer has a `projects` entry at all. Local disk (agent
        # config, the staged BRD) isn't touched here at all -- this class
        # has no filesystem root; RuntimeRegistry.delete_project handles
        # that half unconditionally, on top of calling this.
        with self._engine.begin() as conn:
            for table in (artifacts, comments, approvals, tasks, execution_events, project_config, dependency_graph, project_state):
                conn.execute(delete(table).where(table.c.project_id == project_id))
            result = conn.execute(delete(projects).where(projects.c.id == project_id))
        if result.rowcount == 0:
            raise FileNotFoundError(f"project not found: {project_id}")
