from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .judge_eval_preflight import EXPECTED_JUDGE_EVAL_CASE_IDS
from .model_policy import (
    JUDGE_MODEL_LADDER,
    MODEL_POLICY_VERSION,
    CouncilModelStage,
    StageModelSelectionStatus,
)


JUDGE_SELECTED_MODEL_AUTHORITY_VERSION = "B4_JUDGE_SELECTED_MODEL_AUTHORITY_v0_1"
JUDGE_REPLAY_CONTRACT_VERSION = "B4_JUDGE_DURABLE_RESULT_HASH_REPLAY_v0_1"
EXPECTED_RECEIPTS = len(JUDGE_MODEL_LADDER) * len(EXPECTED_JUDGE_EVAL_CASE_IDS)


class JudgeSelectedModelAuthorityError(ValueError):
    pass


def _sha(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise JudgeSelectedModelAuthorityError(f"{field_name} must be lowercase SHA-256")
    if any(ch not in "0123456789abcdef" for ch in value):
        raise JudgeSelectedModelAuthorityError(f"{field_name} must be lowercase SHA-256")
    return value


def _decimal(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise JudgeSelectedModelAuthorityError(f"{field_name} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise JudgeSelectedModelAuthorityError(f"{field_name} invalid decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise JudgeSelectedModelAuthorityError(f"{field_name} invalid decimal")
    return parsed


def expected_judge_candidates() -> dict[str, dict[str, Any]]:
    return {
        candidate.candidate_key: {
            "candidate_key": candidate.candidate_key,
            "model": candidate.model,
            "reasoning_effort": candidate.reasoning_effort,
            "ladder_position": candidate.ladder_position,
        }
        for candidate in JUDGE_MODEL_LADDER
    }


def verify_judge_selected_model_authority(payload: Mapping[str, Any]) -> str:
    selection_hash = _sha(payload.get("selection_hash"), field_name="selection_hash")
    if selection_hash != canonical_sha256(payload, exclude_fields=("selection_hash",)):
        raise JudgeSelectedModelAuthorityError("Judge selected-model authority hash mismatch")
    if payload.get("artifact_version") != JUDGE_SELECTED_MODEL_AUTHORITY_VERSION:
        raise JudgeSelectedModelAuthorityError("unexpected Judge selected-model authority version")
    if payload.get("replay_contract_version") != JUDGE_REPLAY_CONTRACT_VERSION:
        raise JudgeSelectedModelAuthorityError("Judge selected-model replay contract mismatch")
    if payload.get("stage") != CouncilModelStage.JUDGE.value:
        raise JudgeSelectedModelAuthorityError("Judge selected-model authority stage mismatch")
    if payload.get("model_policy_version") != MODEL_POLICY_VERSION:
        raise JudgeSelectedModelAuthorityError("Judge selected-model policy mismatch")
    if payload.get("selection_status") != StageModelSelectionStatus.SELECTED.value:
        raise JudgeSelectedModelAuthorityError("Judge selected-model authority is not SELECTED")
    if payload.get("semantic_replay_receipts_complete") != EXPECTED_RECEIPTS:
        raise JudgeSelectedModelAuthorityError("Judge authority does not replay all 21 receipts")
    if payload.get("replayed_result_hash_count") != EXPECTED_RECEIPTS:
        raise JudgeSelectedModelAuthorityError("Judge authority lacks 21 replayed result hashes")
    replay_passed = payload.get("semantic_replay_passed_cases")
    if type(replay_passed) is not int or replay_passed < 0 or replay_passed > EXPECTED_RECEIPTS:
        raise JudgeSelectedModelAuthorityError("Judge semantic replay pass count invalid")
    if payload.get("cost_receipt_status") != "COMPLETE":
        raise JudgeSelectedModelAuthorityError("Judge selected-model cost receipts incomplete")
    _decimal(payload.get("actual_paid_eval_cost_usd"), field_name="actual_paid_eval_cost_usd")

    selected = payload.get("selected_candidate")
    candidates = expected_judge_candidates()
    if not isinstance(selected, Mapping):
        raise JudgeSelectedModelAuthorityError("Judge selected candidate missing")
    selected_key = selected.get("candidate_key")
    if not isinstance(selected_key, str) or selected_key not in candidates:
        raise JudgeSelectedModelAuthorityError("Judge selected candidate is outside frozen ladder")
    if dict(selected) != candidates[selected_key]:
        raise JudgeSelectedModelAuthorityError("Judge selected candidate differs from frozen ladder")

    summaries = payload.get("full_ladder_pass_summary")
    if not isinstance(summaries, Mapping) or tuple(summaries) != tuple(candidates):
        raise JudgeSelectedModelAuthorityError("Judge full-ladder summary missing")
    for candidate_key, summary in summaries.items():
        if not isinstance(summary, Mapping):
            raise JudgeSelectedModelAuthorityError("Judge candidate summary malformed")
        if summary.get("required_cases") != len(EXPECTED_JUDGE_EVAL_CASE_IDS):
            raise JudgeSelectedModelAuthorityError("Judge candidate required-case count drift")
        if summary.get("passed_cases") != len(EXPECTED_JUDGE_EVAL_CASE_IDS):
            raise JudgeSelectedModelAuthorityError("Judge selected-model freeze requires all cases pass")
        if summary.get("all_required_checks_passed") is not True:
            raise JudgeSelectedModelAuthorityError("Judge selected-model freeze requires all checks pass")
        if summary.get("critical_safety_failures") != 0:
            raise JudgeSelectedModelAuthorityError("Judge selected-model freeze has critical safety failure")
        _decimal(summary.get("estimated_cost_usd"), field_name=f"{candidate_key}.estimated_cost_usd")
        _sha(summary.get("record_hash"), field_name=f"{candidate_key}.record_hash")

    for field_name in (
        "model_eval_artifact_hash",
        "paid_authorization_artifact_hash",
        "receipt_manifest_hash",
        "judge_entry_preflight_artifact_hash",
        "rebuttal_council_freeze_artifact_hash",
        "request_preflight_artifact_hash",
        "request_manifest_hash",
        "cost_preflight_artifact_hash",
        "runner_dry_artifact_hash",
    ):
        _sha(payload.get(field_name), field_name=field_name)

    if payload.get("judge_eval_authorization_consumed") is not True:
        raise JudgeSelectedModelAuthorityError("Judge eval authorization is not consumed")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise JudgeSelectedModelAuthorityError(
                f"Judge selected-model zero-call invariant violated: {field}"
            )
    if payload.get("production_judge_authorized") is not False:
        raise JudgeSelectedModelAuthorityError("selected-model authority grants production Judge")
    if payload.get("rerun_authorized") is not False:
        raise JudgeSelectedModelAuthorityError("selected-model authority grants rerun")
    if payload.get("live_money") != "PROHIBITED":
        raise JudgeSelectedModelAuthorityError("Judge selected-model live-money invariant drift")
    return selection_hash
