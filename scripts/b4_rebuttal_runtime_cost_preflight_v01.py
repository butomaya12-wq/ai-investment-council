from __future__ import annotations

import json
from pathlib import Path
import sys

from aic.council.rebuttal_runtime_preflight import (
    build_rebuttal_runtime_cost_preflight,
)


DEFAULT_REQUEST = Path(".aic-runtime/b4_rebuttal_runtime_request_preflight_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_rebuttal_runtime_cost_preflight_v0_1.json")


def _read(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return raw


def main() -> int:
    try:
        artifact = build_rebuttal_runtime_cost_preflight(_read(DEFAULT_REQUEST))
        DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUTPUT.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "status": artifact["status"],
            "code_commit_sha": artifact["code_commit_sha"],
            "runtime_request_preflight_artifact_hash": artifact["runtime_request_preflight_artifact_hash"],
            "runtime_request_manifest_hash": artifact["runtime_request_manifest_hash"],
            "selected_model_authority_selection_hash": artifact["selected_model_authority_selection_hash"],
            "selected_candidate": artifact["selected_candidate"],
            "planned_paid_calls_max": artifact["planned_paid_calls_max"],
            "max_request_body_utf8_bytes": artifact["max_request_body_utf8_bytes"],
            "max_output_tokens_per_call": artifact["max_output_tokens_per_call"],
            "pricing_version": artifact["pricing_version"],
            "pricing_hash": artifact["pricing_hash"],
            "cache_write_input_rate_multiplier": artifact["cache_write_input_rate_multiplier"],
            "total_rebuttal_runtime_cost_upper_bound_usd": artifact["total_rebuttal_runtime_cost_upper_bound_usd"],
            "owner_cost_approval_required": True,
            "automatic_repair_calls_authorized": False,
            "model_calls": 0,
            "provider_reads": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "live_money": "PROHIBITED",
            "production_rebuttal_authorized": False,
            "judge_authorized": False,
            "rerun_authorized": False,
            "artifact_hash": artifact["artifact_hash"],
            "output_path": str(DEFAULT_OUTPUT),
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            f"B4 Rebuttal runtime cost preflight failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
