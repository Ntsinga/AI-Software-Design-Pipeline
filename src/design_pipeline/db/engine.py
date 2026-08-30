"""SQLAlchemy engine construction for the optional Postgres-backed store."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def build_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for a Postgres `DATABASE_URL`.

    Normalizes the `postgres://` scheme some providers (Neon included) still
    emit into the `postgresql+psycopg://` form SQLAlchemy's psycopg3 dialect
    expects, so a connection string copied straight out of a Neon/Render
    dashboard works without edits.
    """
    url = database_url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, pool_pre_ping=True, future=True)
