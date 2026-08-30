from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from aic.council.initial_runtime_cost_v02 import (
    build_initial_runtime_cost_preflight,
    load_initial_runtime_pricing,
)


DEFAULT_REQUEST_PREFLIGHT = Path(
    ".aic-runtime/b4_initial_runtime_request_preflight_v0_1.json"
)
DEFAULT_OUTPUT = Path(
    ".aic-runtime/b4_initial_runtime_cost_preflight_v0_2.json"
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read runtime artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"runtime artifact root must be object: {path}")
    return value


def _summary(artifact: dict[str, Any], *, output_path: Path) -> dict[str, Any]:
    return {
        "artifact_version": artifact["artifact_version"],
        "status": artifact["status"],
        "code_commit_sha": artifact["code_commit_sha"],
        "selected_candidate": artifact["selected_candidate"],
        "planned_paid_calls_max": artifact["planned_paid_calls_max"],
        "automatic_repair_calls_authorized": artifact[
            "automatic_repair_calls_authorized"
        ],
        "max_request_body_utf8_bytes": artifact["max_request_body_utf8_bytes"],
        "input_tokens_upper_bound_per_call": artifact[
            "input_tokens_upper_bound_per_call"
        ],
        "max_output_tokens_per_call": artifact["max_output_tokens_per_call"],
        "pricing_version": artifact["pricing_version"],
        "pricing_hash": artifact["pricing_hash"],
        "cache_write_input_rate_multiplier": artifact[
            "cache_write_input_rate_multiplier"
        ],
        "implicit_prompt_caching_default": artifact[
            "implicit_prompt_caching_default"
        ],
        "worst_case_all_input_tokens_as_cache_write_assumed": artifact[
            "worst_case_all_input_tokens_as_cache_write_assumed"
        ],
        "total_initial_runtime_cost_upper_bound_usd": artifact[
            "total_initial_runtime_cost_upper_bound_usd"
        ],
        "owner_cost_approval_required": artifact["owner_cost_approval_required"],
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
        runtime_preflight = _read_json(DEFAULT_REQUEST_PREFLIGHT)
        pricing = load_initial_runtime_pricing()
        artifact = build_initial_runtime_cost_preflight(
            runtime_preflight,
            pricing=pricing,
        )
        DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUTPUT.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(
            json.dumps(
                _summary(artifact, output_path=DEFAULT_OUTPUT),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(
            f"B4 Initial runtime cost preflight v0.2 failed closed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
