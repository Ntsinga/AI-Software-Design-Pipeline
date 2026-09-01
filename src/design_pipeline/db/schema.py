"""Table definitions for the optional Postgres-backed store.

Every row is scoped by `project_id`. A `projects` table holds the list of
projects the DB is hosting; every other table carries `project_id` in its
primary key (or as a NOT NULL indexed column for singleton-per-project
rows). One database can hold many independent projects; the runtime
constructs one `PostgresProjectStore(root, url, project_id=...)` per
active project.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

projects = Table(
    "projects",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

project_state = Table(
    "project_state",
    metadata,
    # One row per project.
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
    # One row per project (project_id also serves as the PK).
    Column("project_id", String, primary_key=True),
    Column("requirements", JSONB, nullable=False),
)

execution_events = Table(
    "execution_events",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("project_id", String, nullable=False, index=True),
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
    Column("project_id", String, primary_key=True),
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
    Column("project_id", String, nullable=False, index=True),
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
    Column("project_id", String, nullable=False, index=True),
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
    Column("project_id", String, nullable=False, index=True),
    Column("objective", Text, nullable=False),
    Column("step_id", String, nullable=False),
    Column("handoff", JSONB, nullable=True),
    Column("status", String, nullable=False),
    Column("attempts", Integer, nullable=False),
)

# The agent/workflow YAML and the staged (uploaded-but-not-yet-ingested) BRD
# used to live only on local disk, even in Postgres mode -- fine on a
# persistent disk, but silently wiped on every redeploy/restart on a host
# with an ephemeral filesystem (Render), while `project_state` above kept
# insisting the project was still initialized. `project_config` is the
# durable source of truth for that content now; the on-disk files under
# `ProjectPaths` become a materialized cache of these rows, rewritten
# whenever missing (see `DesignRuntime._require_initialized`).
project_config = Table(
    "project_config",
    metadata,
    Column("project_id", String, primary_key=True),
    Column("agent_files", JSONB, nullable=False),  # {filename: yaml text}
    Column("workflow_file", Text, nullable=False),  # design-pipeline.yaml text
    Column("staged_brd_filename", String, nullable=True),
    Column("staged_brd_content", Text, nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# Deployment-wide (not per-project) settings that today live in a single
# shared `.env` file at the filesystem root -- same ephemeral-disk problem
# as project_config above, just not scoped to any one project. Currently
# holds only the active model provider selection; API keys are deliberately
# never written here -- those stay in real process environment variables
# only (see provider_config.py).
app_settings = Table(
    "app_settings",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", String, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
