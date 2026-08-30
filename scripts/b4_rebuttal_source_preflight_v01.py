from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from aic.council.model_input import build_initial_model_inputs
from aic.council.models import CouncilInputFreezeArtifact
from aic.council.rebuttal_preflight import (
    build_rebuttal_frozen_contexts,
    build_rebuttal_source_request_preflight,
)
from aic.domain.canonical import canonical_sha256
from aic.research.handoff import load_real_event_handoff


EXPECTED_INITIAL_FREEZE_HASH = "ca7391e5e0c3a754eabc54fbf959b0f36e0986b552d405a06cf649116135361f"
DEFAULT_INITIAL_FREEZE = Path(".aic-runtime/b4_initial_council_freeze_v0_5.json")
DEFAULT_INPUT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_EVAL_PLAN = Path("config/event/b4_stage_eval_plan_v1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_rebuttal_source_preflight_v0_1.json")


def _read_json(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return raw


def _git_context() -> str:
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
    if status.strip():
        raise ValueError("Rebuttal source preflight requires a clean git worktree")
    return head


def main() -> int:
    try:
        initial_freeze = _read_json(DEFAULT_INITIAL_FREEZE)
        freeze = CouncilInputFreezeArtifact.model_validate(_read_json(DEFAULT_INPUT_FREEZE))
        reconciliation = _read_json(DEFAULT_RECONCILIATION)
        handoff = load_real_event_handoff(DEFAULT_HANDOFF)
        eval_plan = _read_json(DEFAULT_EVAL_PLAN)
        initial_inputs = build_initial_model_inputs(freeze, reconciliation, handoff)
        contexts = build_rebuttal_frozen_contexts(
            initial_freeze=initial_freeze,
            freeze=freeze,
            initial_model_inputs=initial_inputs,
            expected_initial_freeze_hash=EXPECTED_INITIAL_FREEZE_HASH,
        )
        artifact = build_rebuttal_source_request_preflight(
            contexts=contexts,
            freeze=freeze,
            code_commit_sha=_git_context(),
            eval_plan=eval_plan,
        )
        expected_hash = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
        if artifact["artifact_hash"] != expected_hash:
            raise ValueError("Rebuttal source preflight artifact hash mismatch")
        DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUTPUT.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "status": artifact["status"],
                    "code_commit_sha": artifact["code_commit_sha"],
                    "initial_council_freeze_artifact_hash": artifact[
                        "initial_council_freeze_artifact_hash"
                    ],
                    "schema_repair_version": artifact["schema_repair_version"],
                    "schema_version": artifact["schema_version"],
                    "promotion_semantics_contract_version": artifact[
                        "promotion_semantics_contract_version"
                    ],
                    "opposing_lane_contract_version": artifact[
                        "opposing_lane_contract_version"
                    ],
                    "claim_type_contract_version": artifact[
                        "claim_type_contract_version"
                    ],
                    "candidate_order": artifact["candidate_order"],
                    "production_rebuttal_calls_after_selection": artifact[
                        "production_rebuttal_calls_after_selection"
                    ],
                    "model_selection_required": artifact["model_selection_required"],
                    "selected_candidate": artifact["selected_candidate"],
                    "eval_candidate_keys": artifact["eval_candidate_keys"],
                    "eval_case_ids": artifact["eval_case_ids"],
                    "eval_paid_call_count_max": artifact["eval_paid_call_count_max"],
                    "request_variant_count": artifact["request_variant_count"],
                    "max_request_body_utf8_bytes": max(
                        row["request_body_utf8_bytes"]
                        for row in artifact["request_variants"]
                    ),
                    "request_manifest_hash": artifact["request_manifest_hash"],
                    "model_calls": 0,
                    "provider_reads": 0,
                    "broker_writes": 0,
                    "alpaca_orders": 0,
                    "live_money": "PROHIBITED",
                    "paid_eval_authorized": False,
                    "production_rebuttal_authorized": False,
                    "judge_authorized": False,
                    "artifact_hash": artifact["artifact_hash"],
                    "output_path": str(DEFAULT_OUTPUT),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            f"B4 Rebuttal source preflight failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
