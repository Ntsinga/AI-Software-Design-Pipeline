"""Command-line interface for the Design Pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .runtime import DesignRuntime


def _runtime(path: str) -> DesignRuntime:
    return DesignRuntime(Path(path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="design", description="Run the provider-neutral Design Pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="initialize a project")
    init.add_argument("path", nargs="?", default=".")
    init.add_argument("--project-id")

    ingest = subparsers.add_parser("ingest", help="ingest a text BRD document")
    ingest.add_argument("path")
    ingest.add_argument("document")

    for command, help_text in (("status", "show project status"), ("run", "run or resume the workflow"), ("artifacts", "list latest artifact versions"), ("history", "show execution history")):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("path", nargs="?", default=".")
        if command == "run":
            command_parser.add_argument("--step", help="run one ready workflow step")

    approve = subparsers.add_parser("approve", help="approve an artifact")
    approve.add_argument("path")
    approve.add_argument("artifact_id")
    approve.add_argument("--version", type=int)
    approve.add_argument("--note")

    changes = subparsers.add_parser("request-changes", help="request changes to an artifact")
    changes.add_argument("path")
    changes.add_argument("artifact_id")
    changes.add_argument("--version", type=int)
    changes.add_argument("--note")

    retry = subparsers.add_parser("retry", help="retry one artifact")
    retry.add_argument("path")
    retry.add_argument("artifact_id")
    retry.add_argument("--instruction")

    comment = subparsers.add_parser("comment", help="add a structured artifact comment")
    comment.add_argument("path")
    comment.add_argument("artifact_id")
    comment.add_argument("text")

    dependencies = subparsers.add_parser("dependencies", help="show artifacts associated with a requirement")
    dependencies.add_argument("path")
    dependencies.add_argument("requirement_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        runtime = _runtime(args.path)
        if args.command == "init":
            result = runtime.initialize(args.project_id).model_dump(mode="json")
        elif args.command == "ingest":
            result = runtime.ingest_brd(args.document).model_dump(mode="json")
        elif args.command == "status":
            result = runtime.status()
        elif args.command == "run":
            result = runtime.run(args.step).model_dump(mode="json")
        elif args.command == "artifacts":
            result = [item.model_dump(mode="json") for item in runtime.store.artifacts.list_latest()]
        elif args.command == "history":
            result = [item.model_dump(mode="json") for item in runtime.store.read_events()]
        elif args.command == "approve":
            result = runtime.approve(args.artifact_id, args.version, note=args.note).model_dump(mode="json")
        elif args.command == "request-changes":
            result = runtime.request_changes(args.artifact_id, args.note, version=args.version).model_dump(mode="json")
        elif args.command == "retry":
            result = runtime.retry(args.artifact_id, args.instruction).model_dump(mode="json")
        elif args.command == "comment":
            result = runtime.add_comment(args.artifact_id, args.text).model_dump(mode="json")
        elif args.command == "dependencies":
            result = {"requirement_id": args.requirement_id, "artifacts": runtime.dependencies(args.requirement_id)}
        else:
            raise ValueError(f"unknown command: {args.command}")
        print(json.dumps(result, indent=2, default=str))
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
