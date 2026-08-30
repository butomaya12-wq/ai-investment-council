from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from aic.council.model_input import build_initial_model_inputs
from aic.council.models import CouncilInputFreezeArtifact
from aic.council.rebuttal_model_selection_v02 import (
    verify_rebuttal_selected_model_authority_v02,
)
from aic.council.rebuttal_preflight import build_rebuttal_frozen_contexts
from aic.council.rebuttal_runtime import (
    EXPECTED_PRODUCTION_CALLS,
    REBUTTAL_RUNTIME_VERSION,
    build_rebuttal_runtime_plan,
)
from aic.council.rebuttal_runtime_preflight import (
    EXPECTED_INITIAL_FREEZE_HASH,
    EXPECTED_SELECTED,
    EXPECTED_SELECTION_HASH,
    verify_rebuttal_runtime_request_preflight,
)
from aic.domain.canonical import canonical_sha256
from aic.research.handoff import load_real_event_handoff


DEFAULT_INITIAL_FREEZE = Path(".aic-runtime/b4_initial_council_freeze_v0_5.json")
DEFAULT_INPUT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_RUNTIME_PREFLIGHT = Path(".aic-runtime/b4_rebuttal_runtime_request_preflight_v0_1.json")
DEFAULT_SELECTION = Path(".aic-runtime/b4_rebuttal_selected_model_authority_v0_2.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_rebuttal_runtime_dry_v0_1.json")


def _read(path: Path) -> dict:
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
        raise ValueError("Rebuttal runtime dry reconstruction requires clean git worktree")
    return head


def main() -> int:
    try:
        head = _git_context()
        initial_freeze = _read(DEFAULT_INITIAL_FREEZE)
        freeze = CouncilInputFreezeArtifact.model_validate(_read(DEFAULT_INPUT_FREEZE))
        reconciliation = _read(DEFAULT_RECONCILIATION)
        handoff = load_real_event_handoff(DEFAULT_HANDOFF)
        runtime_preflight = _read(DEFAULT_RUNTIME_PREFLIGHT)
        selection = _read(DEFAULT_SELECTION)

        if runtime_preflight.get("code_commit_sha") != head:
            raise ValueError("runtime request preflight is not bound to current HEAD")
        request_preflight_hash = verify_rebuttal_runtime_request_preflight(runtime_preflight)
        selection_hash = verify_rebuttal_selected_model_authority_v02(selection)
        if selection_hash != EXPECTED_SELECTION_HASH:
            raise ValueError("selected-model authority hash drift")
        if selection.get("selected_candidate") != EXPECTED_SELECTED:
            raise ValueError("selected-model authority is not frozen R3")

        initial_inputs = build_initial_model_inputs(freeze, reconciliation, handoff)
        contexts = build_rebuttal_frozen_contexts(
            initial_freeze=initial_freeze,
            freeze=freeze,
            initial_model_inputs=initial_inputs,
            expected_initial_freeze_hash=EXPECTED_INITIAL_FREEZE_HASH,
        )
        plan = build_rebuttal_runtime_plan(
            freeze=freeze,
            contexts=contexts,
            runtime_preflight=runtime_preflight,
            selection_authority=selection,
        )
        if len(plan) != EXPECTED_PRODUCTION_CALLS:
            raise ValueError("runtime dry plan is not exactly three calls")

        plan_rows = [
            {
                "dispatch_index": item.dispatch_index,
                "candidate_id": item.candidate_id,
                "context_hash": item.context_hash,
                "request_hash": item.request.request_hash,
                "request_body_utf8_bytes": item.request_body_utf8_bytes,
                "model": item.request.request_payload["model"],
                "reasoning_effort": item.request.request_payload["reasoning"]["effort"],
                "max_output_tokens": item.request.request_payload["max_output_tokens"],
                "required_unknown_refs": list(item.required_unknown_refs),
            }
            for item in plan
        ]
        artifact = {
            "artifact_version": "B4_REBUTTAL_RUNTIME_DRY_RECONSTRUCTION_v0_1",
            "status": "PASS_ZERO_CALL_REBUTTAL_RUNTIME_DRY_RECONSTRUCTION",
            "runtime_version": REBUTTAL_RUNTIME_VERSION,
            "code_commit_sha": head,
            "initial_council_freeze_artifact_hash": EXPECTED_INITIAL_FREEZE_HASH,
            "b4_input_freeze_artifact_hash": freeze.artifact_hash,
            "selected_model_authority_selection_hash": selection_hash,
            "selected_candidate": dict(EXPECTED_SELECTED),
            "runtime_request_preflight_artifact_hash": request_preflight_hash,
            "runtime_request_manifest_hash": runtime_preflight["request_manifest_hash"],
            "candidate_order": list(freeze.candidate_order),
            "planned_paid_calls_max": EXPECTED_PRODUCTION_CALLS,
            "automatic_repair_calls_authorized": False,
            "plan": plan_rows,
            "plan_manifest_hash": canonical_sha256({"plan": plan_rows}),
            "model_calls": 0,
            "provider_reads": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "live_money": "PROHIBITED",
            "production_rebuttal_authorized": False,
            "judge_authorized": False,
            "rerun_authorized": False,
        }
        artifact["artifact_hash"] = canonical_sha256(artifact)
        DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUTPUT.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "status": artifact["status"],
            "runtime_version": artifact["runtime_version"],
            "code_commit_sha": artifact["code_commit_sha"],
            "initial_council_freeze_artifact_hash": artifact["initial_council_freeze_artifact_hash"],
            "selected_model_authority_selection_hash": artifact["selected_model_authority_selection_hash"],
            "selected_candidate": artifact["selected_candidate"],
            "runtime_request_preflight_artifact_hash": artifact["runtime_request_preflight_artifact_hash"],
            "runtime_request_manifest_hash": artifact["runtime_request_manifest_hash"],
            "candidate_order": artifact["candidate_order"],
            "planned_paid_calls_max": artifact["planned_paid_calls_max"],
            "plan_manifest_hash": artifact["plan_manifest_hash"],
            "plan": artifact["plan"],
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
            f"B4 Rebuttal runtime dry reconstruction failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
