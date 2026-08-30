from __future__ import annotations

import json
from pathlib import Path

from aic.council.rebuttal_model_selection import (
    build_rebuttal_selected_model_authority,
    verify_rebuttal_selected_model_authority,
)
from aic.domain.canonical import canonical_sha256


DEFAULT_EVAL = Path(".aic-runtime/b4_rebuttal_model_eval_v0_1.json")
DEFAULT_RECEIPTS = Path(".aic-runtime/b4_rebuttal_model_eval_paid_receipts_v0_1.jsonl")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_rebuttal_selected_model_authority_v0_1.json")

EXPECTED_SOURCE_HEAD = "52095194976b5e7bfc7ee646e3aaf2d54f80a276"
EXPECTED_EVAL_HASH = "1533a224f9a0c85abb77f42526aeed24e76c7e0453bc85cc5c8f8881669ae414"
EXPECTED_RUN_ID = "AIC-B4-REBUTTAL-EVAL-20260830T090933826374Z-b6f90a8cf401"
EXPECTED_AUTH_HASH = "c45bf9cfcdcc4c91513a710f50d94bd0d260de83e814fe93931246efbc73b202"
EXPECTED_RECEIPT_MANIFEST = "5a34f22d00af8d0377b7cbe7b5dbb77669e0528abebfca23dc9fee0b1c9296df"
EXPECTED_INITIAL_FREEZE = "ca7391e5e0c3a754eabc54fbf959b0f36e0986b552d405a06cf649116135361f"
EXPECTED_REQUEST_PREFLIGHT = "d052d025feb43dc3efbf37905e67851fcc9578d8866752a3c1fbb6380f0e3054"
EXPECTED_REQUEST_MANIFEST = "bdd16f84f008741cc01dd518fbcd5d10aabada8619627660f044bd9e97e5d4bb"
EXPECTED_COST_PREFLIGHT = "c2345970cce1d955392d345f2d19589590a74670e6b0392aed4123a59966ea36"
EXPECTED_ACTUAL_COST = "0.5024980"
EXPECTED_SELECTED = {
    "candidate_key": "R3",
    "model": "gpt-5.6-sol",
    "reasoning_effort": "medium",
    "ladder_position": 3,
}


def _read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _read_receipts(path: Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("receipt journal contains non-object row")
    return rows


def main() -> int:
    eval_artifact = _read_object(DEFAULT_EVAL)
    receipts = _read_receipts(DEFAULT_RECEIPTS)

    if eval_artifact.get("artifact_hash") != EXPECTED_EVAL_HASH:
        raise ValueError("unexpected paid Rebuttal eval artifact hash")
    if eval_artifact.get("artifact_hash") != canonical_sha256(
        eval_artifact, exclude_fields=("artifact_hash",)
    ):
        raise ValueError("paid Rebuttal eval artifact canonical hash mismatch")
    exact = {
        "code_commit_sha": EXPECTED_SOURCE_HEAD,
        "run_id": EXPECTED_RUN_ID,
        "paid_authorization_artifact_hash": EXPECTED_AUTH_HASH,
        "receipt_manifest_hash": EXPECTED_RECEIPT_MANIFEST,
        "initial_council_freeze_artifact_hash": EXPECTED_INITIAL_FREEZE,
        "request_preflight_artifact_hash": EXPECTED_REQUEST_PREFLIGHT,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST,
        "cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT,
        "actual_cost_usd": EXPECTED_ACTUAL_COST,
        "status": "PASS_SELECTED",
        "cost_receipt_status": "COMPLETE",
        "semantic_replay_receipts_complete": 12,
        "dispatch_attempts": 12,
        "model_calls": 12,
        "automatic_repair_calls": 0,
        "production_rebuttal_authorized": False,
        "judge_authorized": False,
        "rerun_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    for field, expected in exact.items():
        if eval_artifact.get(field) != expected:
            raise ValueError(f"paid Rebuttal eval exact evidence drift: {field}")
    selection = eval_artifact.get("selection")
    if not isinstance(selection, dict) or selection.get("selected_candidate") != EXPECTED_SELECTED:
        raise ValueError("paid Rebuttal eval selected candidate drift")

    authority = build_rebuttal_selected_model_authority(eval_artifact, receipts)
    selection_hash = verify_rebuttal_selected_model_authority(authority)
    if authority.get("selected_candidate") != EXPECTED_SELECTED:
        raise ValueError("zero-call selection replay did not select expected R3")
    if authority.get("actual_paid_eval_cost_usd") != EXPECTED_ACTUAL_COST:
        raise ValueError("zero-call selection cost replay drift")
    if authority.get("model_eval_artifact_hash") != EXPECTED_EVAL_HASH:
        raise ValueError("selected-model authority eval binding drift")
    if authority.get("paid_authorization_artifact_hash") != EXPECTED_AUTH_HASH:
        raise ValueError("selected-model authority paid authorization binding drift")
    if authority.get("receipt_manifest_hash") != EXPECTED_RECEIPT_MANIFEST:
        raise ValueError("selected-model authority receipt manifest binding drift")
    if authority.get("semantic_replay_receipts_complete") != 12:
        raise ValueError("selected-model authority replay completeness drift")

    DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT.write_text(
        json.dumps(authority, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "status": "PASS_ZERO_CALL_REBUTTAL_SELECTED_MODEL_FREEZE",
        "source_paid_eval_head": authority["source_git_commit"],
        "paid_run_id": authority["paid_run_id"],
        "model_eval_artifact_hash": authority["model_eval_artifact_hash"],
        "paid_authorization_artifact_hash": authority["paid_authorization_artifact_hash"],
        "receipt_manifest_hash": authority["receipt_manifest_hash"],
        "semantic_replay_receipts_complete": authority["semantic_replay_receipts_complete"],
        "semantic_replay_passed_cases": authority["semantic_replay_passed_cases"],
        "selected_candidate": authority["selected_candidate"],
        "selection_reason_code": authority["selection_reason_code"],
        "selected_eval_metrics": authority["selected_eval_metrics"],
        "full_ladder_pass_summary": authority["full_ladder_pass_summary"],
        "actual_paid_eval_cost_usd": authority["actual_paid_eval_cost_usd"],
        "selection_hash": selection_hash,
        "output_path": str(DEFAULT_OUTPUT),
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "production_rebuttal_authorized": False,
        "judge_authorized": False,
        "rerun_authorized": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
