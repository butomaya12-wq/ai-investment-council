from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from aic.council.rebuttal_eval_preflight import build_rebuttal_eval_request_preflight


SOURCE_PATH = Path(".aic-runtime/b4_rebuttal_source_preflight_v0_1.json")
OUTPUT_PATH = Path(".aic-runtime/b4_rebuttal_eval_request_preflight_v0_1.json")


def _head() -> str:
    value = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if len(value) != 40:
        raise RuntimeError("unable to resolve exact git HEAD")
    return value


def main() -> int:
    if not SOURCE_PATH.exists():
        print("missing Rebuttal production source-preflight artifact", file=sys.stderr)
        return 2
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    artifact = build_rebuttal_eval_request_preflight(
        code_commit_sha=_head(),
        source_preflight=source,
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        "status": artifact["status"],
        "artifact_hash": artifact["artifact_hash"],
        "request_manifest_hash": artifact["request_manifest_hash"],
        "planned_paid_calls_max": artifact["planned_paid_calls_max"],
        "candidate_keys": artifact["candidate_keys"],
        "case_ids": artifact["case_ids"],
        "max_request_body_utf8_bytes": artifact["max_request_body_utf8_bytes"],
        "max_output_tokens_per_call": artifact["max_output_tokens_per_call"],
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
