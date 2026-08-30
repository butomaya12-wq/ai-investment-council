from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from aic.council.judge_eval_preflight import (
    build_judge_eval_cost_preflight,
    build_judge_eval_dry,
    build_judge_eval_request_preflight,
    verify_judge_eval_cost_preflight,
    verify_judge_eval_dry,
    verify_judge_eval_request_preflight,
)


ENTRY_PATH = Path(".aic-runtime/b4_judge_entry_preflight_v0_1.json")
REQUEST_PATH = Path(".aic-runtime/b4_judge_model_eval_request_preflight_v0_1.json")
COST_PATH = Path(".aic-runtime/b4_judge_model_eval_cost_preflight_v0_1.json")
DRY_PATH = Path(".aic-runtime/b4_judge_model_eval_dry_v0_1.json")


def _read(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"artifact root must be object: {path}")
    return raw


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> int:
    entry = _read(ENTRY_PATH)
    head = _head()
    request = build_judge_eval_request_preflight(
        code_commit_sha=head,
        entry_preflight=entry,
    )
    cost = build_judge_eval_cost_preflight(request)
    dry = build_judge_eval_dry(
        request_preflight=request,
        cost_preflight=cost,
    )
    verify_judge_eval_request_preflight(request)
    verify_judge_eval_cost_preflight(cost)
    verify_judge_eval_dry(dry)
    _write(REQUEST_PATH, request)
    _write(COST_PATH, cost)
    _write(DRY_PATH, dry)
    print(
        json.dumps(
            {
                "status": "PASS_ZERO_CALL_JUDGE_MODEL_EVAL_PREFLIGHT",
                "code_commit_sha": head,
                "judge_entry_preflight_artifact_hash": request[
                    "judge_entry_preflight_artifact_hash"
                ],
                "eval_request_preflight_artifact_hash": request[
                    "artifact_hash"
                ],
                "eval_request_manifest_hash": request[
                    "request_manifest_hash"
                ],
                "eval_cost_preflight_artifact_hash": cost["artifact_hash"],
                "eval_dry_artifact_hash": dry["artifact_hash"],
                "candidate_keys": dry["candidate_keys"],
                "case_ids": dry["case_ids"],
                "planned_paid_calls_max": dry["planned_paid_calls_max"],
                "max_request_body_utf8_bytes": request[
                    "max_request_body_utf8_bytes"
                ],
                "max_output_tokens_per_call": dry[
                    "max_output_tokens_per_call"
                ],
                "cost_ceiling_usd": dry["cost_ceiling_usd"],
                "pricing_version": dry["pricing_version"],
                "pricing_hash": dry["pricing_hash"],
                "model_calls": 0,
                "provider_reads": 0,
                "broker_writes": 0,
                "alpaca_orders": 0,
                "live_money": "PROHIBITED",
                "paid_eval_authorized": False,
                "production_judge_authorized": False,
                "rerun_authorized": False,
                "request_output_path": str(REQUEST_PATH),
                "cost_output_path": str(COST_PATH),
                "dry_output_path": str(DRY_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
