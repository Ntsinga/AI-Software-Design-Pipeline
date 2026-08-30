"""Table definitions for the optional Postgres-backed store.

Mirrors the filesystem layout in `storage.ProjectPaths`/`ArtifactRegistry`
one table at a time: `project_state` and `dependency_graph` are effectively
singleton rows (one project == one database, same as one project == one
`.design/` tree today); `execution_events` replaces the `.jsonl` history log;
`artifacts` merges each version's metadata and content into one row (the
filesystem store splits those across `vN.json` / `vN.meta.json` only because
separate files are easier to write atomically); `comments`, `approvals`, and
`tasks` mirror their `.design/review/*` and `.design/state/tasks/*` files.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

project_state = Table(
    "project_state",
    metadata,
    Column("project_id", String, primary_key=True),
    Column("workflow_id", String, nullable=False),
    Column("workflow_status", String, nullable=False),
    Column("step_states", JSONB, nullable=False),
    Column("pending_approvals", JSONB, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

dependency_graph = Table(
    "dependency_graph",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("requirements", JSONB, nullable=False),
)

execution_events = Table(
    "execution_events",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String, nullable=False, unique=True),
    Column("event_type", String, nullable=False),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("step_id", String, nullable=True),
    Column("artifact_id", String, nullable=True),
    Column("details", JSONB, nullable=False),
)

artifacts = Table(
    "artifacts",
    metadata,
    Column("logical_id", String, primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("type", String, nullable=False),
    Column("status", String, nullable=False),
    Column("parent_version", Integer, nullable=True),
    Column("inputs", JSONB, nullable=False),
    Column("requirements", JSONB, nullable=False),
    Column("generated_by", JSONB, nullable=False),
    Column("comments", JSONB, nullable=False),
    Column("approvals", JSONB, nullable=False),
    Column("error", Text, nullable=True),
    Column("content", JSONB, nullable=False),
)

comments = Table(
    "comments",
    metadata,
    Column("id", String, primary_key=True),
    Column("artifact_id", String, nullable=False, index=True),
    Column("text", Text, nullable=False),
    Column("author", String, nullable=False),
    Column("location", JSONB, nullable=True),
    Column("status", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
)

approvals = Table(
    "approvals",
    metadata,
    Column("id", String, primary_key=True),
    Column("artifact_id", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("decision", String, nullable=False),
    Column("reviewer", String, nullable=False),
    Column("note", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

tasks = Table(
    "tasks",
    metadata,
    Column("id", String, primary_key=True),
    Column("objective", Text, nullable=False),
    Column("step_id", String, nullable=False),
    Column("handoff", JSONB, nullable=True),
    Column("status", String, nullable=False),
    Column("attempts", Integer, nullable=False),
)
