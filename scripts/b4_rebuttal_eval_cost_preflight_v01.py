from __future__ import annotations

import json
from pathlib import Path
import sys

from aic.council.rebuttal_eval_preflight import build_rebuttal_eval_cost_preflight
from aic.council.initial_runtime_cost_v02 import load_initial_runtime_pricing


REQUEST_PATH = Path(".aic-runtime/b4_rebuttal_eval_request_preflight_v0_1.json")
OUTPUT_PATH = Path(".aic-runtime/b4_rebuttal_eval_cost_preflight_v0_1.json")


def main() -> int:
    if not REQUEST_PATH.exists():
        print("missing Rebuttal eval request-preflight artifact", file=sys.stderr)
        return 2
    request_preflight = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
    pricing = load_initial_runtime_pricing()
    artifact = build_rebuttal_eval_cost_preflight(
        request_preflight,
        pricing=pricing,
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        "status": artifact["status"],
        "artifact_hash": artifact["artifact_hash"],
        "eval_request_preflight_artifact_hash": artifact["eval_request_preflight_artifact_hash"],
        "eval_request_manifest_hash": artifact["eval_request_manifest_hash"],
        "planned_paid_calls_max": artifact["planned_paid_calls_max"],
        "max_request_body_utf8_bytes": artifact["max_request_body_utf8_bytes"],
        "max_output_tokens_per_call": artifact["max_output_tokens_per_call"],
        "pricing_version": artifact["pricing_version"],
        "pricing_hash": artifact["pricing_hash"],
        "cache_write_input_rate_multiplier": artifact["cache_write_input_rate_multiplier"],
        "total_rebuttal_eval_cost_upper_bound_usd": artifact["total_rebuttal_eval_cost_upper_bound_usd"],
        "owner_cost_approval_required": artifact["owner_cost_approval_required"],
        "model_calls": artifact["model_calls"],
        "provider_reads": artifact["provider_reads"],
        "broker_writes": artifact["broker_writes"],
        "alpaca_orders": artifact["alpaca_orders"],
        "live_money": artifact["live_money"],
        "paid_eval_authorized": artifact["paid_eval_authorized"],
        "production_rebuttal_authorized": artifact["production_rebuttal_authorized"],
        "judge_authorized": artifact["judge_authorized"],
        "output_path": str(OUTPUT_PATH),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
