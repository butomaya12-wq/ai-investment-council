from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .judge_model_selection_v01 import EXPECTED_SELECTED_JUDGE
from .model_policy import (
    INITIAL_MODEL_LADDER,
    JUDGE_MODEL_LADDER,
    MODEL_POLICY_VERSION,
    REBUTTAL_MODEL_LADDER,
    STAGE_MAX_OUTPUT_TOKENS,
    CouncilModelStage,
)


ARTIFACT_VERSION = "B4_REOPEN_LIFECYCLE_PLAN_v0_1"
PASS_STATUS = "B4_REOPEN_LIFECYCLE_PLAN_ZERO_CALL_PASS"
NEXT_GATE = "B4_REOPEN_PRODUCTION_COST_PREFLIGHT_ZERO_CALL"

EXPECTED_OVERLAY_HASH = "ff4d3357ee49927b7ed07bb8fa70cbbca162f6110b74bb9e7f93f2c3dc654ab0"
EXPECTED_OVERLAY_STATUS = "B4_REOPEN_INPUT_OVERLAY_ZERO_CALL_PASS"
EXPECTED_CLOSURE_HASH = "af8f48ae8e6984c73c7ff447eeb523fbda72855ee49460bdc60f0634be4216e6"
EXPECTED_INITIAL_SELECTION_HASH = "0554900c0e7c1b696a681301d249d011f6d500331fe53751998024477269d1e0"
EXPECTED_INITIAL_SELECTED = {
    "candidate_key": "L2",
    "stage": "INITIAL",
    "model": "gpt-5.6-terra",
    "reasoning_effort": "low",
    "ladder_position": 2,
}
EXPECTED_REBUTTAL_SELECTED = {
    "candidate_key": "R3",
    "stage": "REBUTTAL",
    "model": "gpt-5.6-sol",
    "reasoning_effort": "medium",
    "ladder_position": 3,
}
EXPECTED_JUDGE_SELECTED = {
    "candidate_key": "J1",
    "stage": "JUDGE",
    "model": "gpt-5.6-terra",
    "reasoning_effort": "medium",
    "ladder_position": 1,
}


class B4ReopenLifecyclePlanError(ValueError):
    pass


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenLifecyclePlanError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise B4ReopenLifecyclePlanError(f"{label} root must be an object")
    return value


def _verify_self_hash(payload: Mapping[str, Any], *, field: str, label: str) -> str:
    observed = payload.get(field)
    if not isinstance(observed, str) or len(observed) != 64:
        raise B4ReopenLifecyclePlanError(f"{label} {field} missing")
    if observed != canonical_sha256(payload, exclude_fields=(field,)):
        raise B4ReopenLifecyclePlanError(f"{label} {field} self-hash mismatch")
    return observed


def _candidate_payload(candidate: Any) -> dict[str, Any]:
    return {
        "candidate_key": candidate.candidate_key,
        "stage": candidate.stage.value,
        "model": candidate.model,
        "reasoning_effort": candidate.reasoning_effort,
        "ladder_position": candidate.ladder_position,
    }


def _validate_overlay(overlay: Mapping[str, Any]) -> None:
    observed = _verify_self_hash(overlay, field="artifact_hash", label="B4 reopen input overlay")
    if observed != EXPECTED_OVERLAY_HASH:
        raise B4ReopenLifecyclePlanError("B4 reopen input overlay hash drift")
    if overlay.get("status") != EXPECTED_OVERLAY_STATUS:
        raise B4ReopenLifecyclePlanError("B4 reopen input overlay is not PASS")
    if overlay.get("source_b3_reopen_closure_hash") != EXPECTED_CLOSURE_HASH:
        raise B4ReopenLifecyclePlanError("B4 reopen closure lineage drift")
    if overlay.get("effective_material_claim_count") != 37:
        raise B4ReopenLifecyclePlanError("effective MaterialClaim count must be 37")
    if overlay.get("legacy_material_claim_count") != 34 or overlay.get("supplemental_claim_count") != 3:
        raise B4ReopenLifecyclePlanError("legacy/supplemental claim counts drift")
    gap = overlay.get("effective_gap_overlay")
    if not isinstance(gap, Mapping):
        raise B4ReopenLifecyclePlanError("effective gap overlay missing")
    if gap.get("effective_unresolved_data_gap_refs") != []:
        raise B4ReopenLifecyclePlanError("effective data gaps remain open")
    if gap.get("effective_unresolved_reopen_reason_codes") != []:
        raise B4ReopenLifecyclePlanError("effective reopen reasons remain open")
    if overlay.get("historical_b4_frozen_outputs_reusable_as_new_model_outputs") is not False:
        raise B4ReopenLifecyclePlanError("historical B4 outputs cannot be reusable as new outputs")
    if overlay.get("new_b4_decision_lifecycle_required") is not True:
        raise B4ReopenLifecyclePlanError("new B4 decision lifecycle is not required")
    if overlay.get("historical_production_judge_rerun_authorized") is not False:
        raise B4ReopenLifecyclePlanError("historical production Judge rerun boundary drift")
    if overlay.get("model_calls_authorized") is not False or overlay.get("provider_reads_authorized") is not False:
        raise B4ReopenLifecyclePlanError("overlay unexpectedly grants paid/provider authority")


def _validate_initial_authority(authority: Mapping[str, Any]) -> None:
    if authority.get("selection_hash") != EXPECTED_INITIAL_SELECTION_HASH:
        raise B4ReopenLifecyclePlanError("Initial selected-model authority hash drift")
    if authority.get("selection_hash") != canonical_sha256(authority, exclude_fields=("selection_hash",)):
        raise B4ReopenLifecyclePlanError("Initial selected-model authority self-hash mismatch")
    if authority.get("model_policy_version") != MODEL_POLICY_VERSION:
        raise B4ReopenLifecyclePlanError("Initial selected-model policy drift")
    if authority.get("selection_status") != "SELECTED":
        raise B4ReopenLifecyclePlanError("Initial selected-model authority is not SELECTED")
    if authority.get("selected_candidate") != EXPECTED_INITIAL_SELECTED:
        raise B4ReopenLifecyclePlanError("Initial selected model changed")
    if authority.get("cost_receipt_status") != "COMPLETE" or authority.get("semantic_replay_receipts_complete") != 36:
        raise B4ReopenLifecyclePlanError("Initial model-eval authority is incomplete")


def _validate_policy_selected_configs() -> None:
    initial = {item.candidate_key: item for item in INITIAL_MODEL_LADDER}
    rebuttal = {item.candidate_key: item for item in REBUTTAL_MODEL_LADDER}
    judge = {item.candidate_key: item for item in JUDGE_MODEL_LADDER}
    if _candidate_payload(initial["L2"]) != EXPECTED_INITIAL_SELECTED:
        raise B4ReopenLifecyclePlanError("L2 model-policy identity drift")
    if _candidate_payload(rebuttal["R3"]) != EXPECTED_REBUTTAL_SELECTED:
        raise B4ReopenLifecyclePlanError("R3 model-policy identity drift")
    if _candidate_payload(judge["J1"]) != EXPECTED_JUDGE_SELECTED:
        raise B4ReopenLifecyclePlanError("J1 model-policy identity drift")
    if dict(EXPECTED_SELECTED_JUDGE) != {
        key: EXPECTED_JUDGE_SELECTED[key]
        for key in ("candidate_key", "model", "reasoning_effort", "ladder_position")
    }:
        raise B4ReopenLifecyclePlanError("frozen Judge selected-model identity drift")


def build_b4_reopen_lifecycle_plan(
    *,
    code_commit_sha: str,
    overlay: Mapping[str, Any],
    initial_selected_model_authority: Mapping[str, Any],
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise B4ReopenLifecyclePlanError("exact lowercase git SHA required")
    _validate_overlay(overlay)
    _validate_initial_authority(initial_selected_model_authority)
    _validate_policy_selected_configs()

    production_stages = [
        {
            "stage": "INITIAL",
            "selected_model": dict(EXPECTED_INITIAL_SELECTED),
            "fresh_model_calls_max": 9,
            "reason": "Three candidates x Bull/Bear/Red-Team must see the effective 37-claim post-reopen surface.",
            "max_output_tokens_per_call": STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.INITIAL],
        },
        {
            "stage": "REBUTTAL",
            "selected_model": dict(EXPECTED_REBUTTAL_SELECTED),
            "fresh_model_calls_max": 3,
            "reason": "One fresh rebuttal per candidate must depend on the new Initial freeze.",
            "max_output_tokens_per_call": STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.REBUTTAL],
        },
        {
            "stage": "JUDGE",
            "selected_model": dict(EXPECTED_JUDGE_SELECTED),
            "fresh_model_calls_max": 1,
            "reason": "A new post-reopen Judge decision must depend on the new Initial and Rebuttal freezes.",
            "max_output_tokens_per_call": STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.JUDGE],
        },
    ]
    planned = sum(int(row["fresh_model_calls_max"]) for row in production_stages)
    if planned != 13:
        raise B4ReopenLifecyclePlanError("fresh production call ceiling must equal 13")

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_b4_reopen_input_overlay_hash": EXPECTED_OVERLAY_HASH,
        "source_b3_reopen_closure_hash": EXPECTED_CLOSURE_HASH,
        "effective_material_claim_count": 37,
        "effective_unresolved_data_gap_refs": [],
        "effective_unresolved_reopen_reason_codes": [],
        "historical_b4_outputs_reused_as_new_outputs": False,
        "historical_production_judge_rerun_authorized": False,
        "model_policy_version": MODEL_POLICY_VERSION,
        "selected_model_authority_reuse": {
            "INITIAL": {
                "reused": True,
                "selection_hash": EXPECTED_INITIAL_SELECTION_HASH,
                "selected_model": dict(EXPECTED_INITIAL_SELECTED),
                "eval_calls_repeated": 0,
            },
            "REBUTTAL": {
                "reused": True,
                "selected_model": dict(EXPECTED_REBUTTAL_SELECTED),
                "eval_calls_repeated": 0,
                "exact_historical_authority_revalidation_required_before_paid_dispatch": True,
            },
            "JUDGE": {
                "reused": True,
                "selected_model": dict(EXPECTED_JUDGE_SELECTED),
                "eval_calls_repeated": 0,
                "judge_eval_includes_invest_cases": True,
                "exact_historical_authority_revalidation_required_before_paid_dispatch": True,
            },
        },
        "model_eval_reruns_required": False,
        "planned_model_eval_calls": 0,
        "fresh_production_stages": production_stages,
        "planned_fresh_production_model_calls_max": planned,
        "planned_paid_calls_max": planned,
        "stage_dependencies": [
            "INITIAL_FREEZE_BEFORE_REBUTTAL",
            "REBUTTAL_FREEZE_BEFORE_JUDGE",
            "JUDGE_FINAL_DECISION_BEFORE_B5_HANDOFF",
        ],
        "post_reopen_judge_contract_required": True,
        "historical_reopen_restricted_judge_runtime_reusable": False,
        "historical_reopen_restricted_judge_runtime_reason": "The historical production Judge schema only allowed WATCH/ABSTAIN with RESEARCH_REOPEN_REQUEST; post-reopen B4 must use a new versioned Judge contract that can evaluate INVEST/ABSTAIN/WATCH under closed gaps.",
        "production_request_and_cost_preflight_required": True,
        "owner_cost_approval_required": True,
        "model_calls_authorized": False,
        "provider_reads_authorized": False,
        "planned_provider_reads": 0,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "rerun_authorized": False,
        "broker_writes_authorized": 0,
        "alpaca_orders_authorized": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def load_and_build_b4_reopen_lifecycle_plan(
    *,
    code_commit_sha: str,
    overlay_path: str | Path,
    initial_selected_model_authority_path: str | Path,
) -> dict[str, Any]:
    return build_b4_reopen_lifecycle_plan(
        code_commit_sha=code_commit_sha,
        overlay=_read_object(overlay_path, label="B4 reopen input overlay"),
        initial_selected_model_authority=_read_object(
            initial_selected_model_authority_path,
            label="Initial selected-model authority",
        ),
    )
