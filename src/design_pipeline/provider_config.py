"""Safe, local-only configuration for optional live model providers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ProviderConfigurationError(ValueError):
    """Raised when a selected provider is not configured completely."""


@dataclass(frozen=True)
class ProviderSettings:
    provider: str = "stub"
    model: str = "deterministic-fixture"
    api_key: str | None = None
    # A single mockups-step response can now hold 30-40+ real, self-
    # contained HTML pages (one per workflow screen + entity CRUD screen) --
    # 3500 was fine for the original handful of screens but silently
    # truncated once the domain grew past ~15 entities, dropping whichever
    # workflow/screens fell near the end of the response. Same "raise as
    # the domain grows" pattern already applied to max_tool_iterations.
    max_output_tokens: int = 16000
    timeout_seconds: float = 300.0
    max_tool_iterations: int = 20

    @property
    def is_live(self) -> bool:
        return self.provider != "stub"

    @property
    def is_configured(self) -> bool:
        return not self.is_live or bool(self.api_key and self.model)

    def public_status(self) -> dict[str, object]:
        """Return settings that are safe to expose through the local API."""
        return {
            "provider": self.provider,
            "model": self.model,
            "mode": "live" if self.is_live else "deterministic",
            "configured": self.is_configured,
        }


def _read_dotenv(path: Path) -> dict[str, str]:
    """Read a small .env subset without exporting secrets into process state."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_provider_settings(root: Path | str, environ: Mapping[str, str] | None = None) -> ProviderSettings:
    """Load project `.env`, with real environment variables taking precedence."""
    file_values = _read_dotenv(Path(root) / ".env")
    environment = os.environ if environ is None else environ

    def value(name: str, default: str = "") -> str:
        return environment.get(name, file_values.get(name, default)).strip()

    provider = value("DESIGN_PIPELINE_PROVIDER", "stub").lower()
    if provider not in {"stub", "openai", "anthropic", "gemini"}:
        raise ProviderConfigurationError("DESIGN_PIPELINE_PROVIDER must be stub, openai, anthropic, or gemini")
    if provider == "stub":
        return ProviderSettings()

    prefix = {"openai": "OPENAI", "anthropic": "ANTHROPIC", "gemini": "GEMINI"}[provider]
    model = value("DESIGN_PIPELINE_MODEL") or value(f"{prefix}_MODEL")
    api_key = value(f"{prefix}_API_KEY") or None
    try:
        max_output_tokens = int(value("DESIGN_PIPELINE_MAX_OUTPUT_TOKENS", "16000"))
        timeout_seconds = float(value("DESIGN_PIPELINE_TIMEOUT_SECONDS", "300"))
        max_tool_iterations = int(value("DESIGN_PIPELINE_MAX_TOOL_ITERATIONS", "20"))
    except ValueError as exc:
        raise ProviderConfigurationError("model token, timeout, and tool-iteration settings must be numeric") from exc
    if max_output_tokens < 1 or timeout_seconds <= 0 or max_tool_iterations < 1:
        raise ProviderConfigurationError("model token, timeout, and tool-iteration settings must be positive")
    return ProviderSettings(provider, model, api_key, max_output_tokens, timeout_seconds, max_tool_iterations)


def update_provider(root: Path | str, provider: str) -> None:
    """Rewrite just the `DESIGN_PIPELINE_PROVIDER=` line in the project's
    `.env`, preserving every other line (comments, keys, ordering). Adds
    the line if `.env` doesn't have one yet. Keys are never touched here --
    only which provider is active.
    """
    provider = provider.strip().lower()
    if provider not in {"stub", "openai", "anthropic", "gemini"}:
        raise ProviderConfigurationError("DESIGN_PIPELINE_PROVIDER must be stub, openai, anthropic, or gemini")
    path = Path(root) / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for index, line in enumerate(lines):
        if line.strip().startswith("DESIGN_PIPELINE_PROVIDER="):
            lines[index] = f"DESIGN_PIPELINE_PROVIDER={provider}"
            break
    else:
        lines.append(f"DESIGN_PIPELINE_PROVIDER={provider}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_database_url(root: Path | str, environ: Mapping[str, str] | None = None) -> str | None:
    """Return `DATABASE_URL` from the real environment or project `.env`, if set.

    Absence means the caller should fall back to filesystem-backed storage;
    presence selects the optional Postgres-backed store.
    """
    file_values = _read_dotenv(Path(root) / ".env")
    environment = os.environ if environ is None else environ
    value = environment.get("DATABASE_URL", file_values.get("DATABASE_URL", "")).strip()
    return value or None


def load_mermaid_api_key(root: Path | str, environ: Mapping[str, str] | None = None) -> str | None:
    """Return `MERMAID_API_KEY`, if set.

    Purely optional: the Mermaid render/validate tool works with no key at
    all. When set, rendered diagrams are additionally persisted to the
    configured Mermaid Chart account.
    """
    file_values = _read_dotenv(Path(root) / ".env")
    environment = os.environ if environ is None else environ
    value = environment.get("MERMAID_API_KEY", file_values.get("MERMAID_API_KEY", "")).strip()
    return value or None
