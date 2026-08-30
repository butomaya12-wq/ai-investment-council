from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from aic.council.initial_runtime_preflight import (
    build_initial_runtime_request_preflight,
)
from aic.council.model_selection import load_initial_selected_model_authority


DEFAULT_SOURCE_PREFLIGHT = Path(".aic-runtime/b4_initial_request_preflight.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_initial_runtime_request_preflight_v0_1.json")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read runtime artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"runtime artifact root must be object: {path}")
    return value


def _git_context() -> str:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("unable to prove local git execution context") from exc
    if status.strip():
        raise ValueError("Initial runtime request preflight requires a clean git worktree")
    return head


def _summary(artifact: dict[str, Any], *, output_path: Path) -> dict[str, Any]:
    selected = artifact["selected_candidate"]
    variants = artifact["selected_request_variants"]
    return {
        "artifact_version": artifact["artifact_version"],
        "status": artifact["status"],
        "code_commit_sha": artifact["code_commit_sha"],
        "selected_candidate": selected,
        "logical_call_count": artifact["logical_call_count"],
        "planned_paid_calls_max": artifact["planned_paid_calls_max"],
        "automatic_repair_calls_authorized": artifact[
            "automatic_repair_calls_authorized"
        ],
        "max_request_body_utf8_bytes": max(
            item["request_body_utf8_bytes"] for item in variants
        ),
        "max_output_tokens_per_call": artifact["max_output_tokens_per_call"],
        "request_manifest_hash": artifact["request_manifest_hash"],
        "selected_model_authority_selection_hash": artifact[
            "selected_model_authority_selection_hash"
        ],
        "model_calls": artifact["model_calls"],
        "provider_reads": artifact["provider_reads"],
        "broker_writes": artifact["broker_writes"],
        "alpaca_orders": artifact["alpaca_orders"],
        "live_money": artifact["live_money"],
        "artifact_hash": artifact["artifact_hash"],
        "output_path": str(output_path),
    }


def main() -> int:
    try:
        source = _read_json(DEFAULT_SOURCE_PREFLIGHT)
        authority = load_initial_selected_model_authority()
        artifact = build_initial_runtime_request_preflight(
            source,
            authority=authority,
            code_commit_sha=_git_context(),
        )
        DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUTPUT.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(json.dumps(_summary(artifact, output_path=DEFAULT_OUTPUT), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            f"B4 Initial runtime request preflight failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
