from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256

from .models import CouncilLane
from .reopen_initial_runtime import (
    EXPECTED_CALLS,
    EXPECTED_COST_PREFLIGHT_HASH,
    EXPECTED_MAX_OUTPUT_TOKENS,
    EXPECTED_REQUEST_MANIFEST_HASH,
    EXPECTED_SELECTED_MODEL,
    ReopenInitialRuntimePlanItem,
    receipt_manifest_hash,
    verify_reopen_initial_cost_preflight,
)
from .request import CouncilRequestStage


ARTIFACT_VERSION = "B4_REOPEN_INITIAL_UNKNOWN_DISPATCH_RECOVERY_PLAN_v0_1"
PASS_STATUS = "B4_REOPEN_INITIAL_UNKNOWN_DISPATCH_RECOVERY_ZERO_CALL_PASS"
NEXT_GATE = "B4_REOPEN_INITIAL_RECOVERY_OWNER_COST_APPROVAL"

EXPECTED_SOURCE_RUN_ID = "AIC-B4-REOPEN-INITIAL-RUNTIME-20260830T203150483477Z-5dde9b86342c"
EXPECTED_SOURCE_RUNNER_HEAD = "797ff4fba80927d05b453931bf6a475dd8e74d79"
EXPECTED_SOURCE_AUTHORIZATION_HASH = "75529ab455f64e58ebc2a7bf7434cede9956115acb3fb166f56f31ebb9767d8c"
EXPECTED_BLOCKED_ARTIFACT_HASH = "20cef11aef8047d72220f6d8b75748978303115d5f1a5e75d8efa301c3e6c1d4"
EXPECTED_RECEIPT_MANIFEST_HASH = "be7b39f08978208bc86b55db38d76553c28dc80c29e448a52bb6e72014fc91b3"
EXPECTED_KNOWN_COST_USD = Decimal("0.3255090")
EXPECTED_UNKNOWN_DISPATCH_INDEX = 9
EXPECTED_UNKNOWN_CANDIDATE = "META"
EXPECTED_UNKNOWN_LANE = "RED_TEAM"
EXPECTED_UNKNOWN_STAGE = "RED_TEAM_INITIAL"
EXPECTED_UNKNOWN_REQUEST_HASH = "0608e83b89318a8df6757e2b958f8c65df5496cdfb0d0ae764e43b9f27f622dc"
EXPECTED_UNKNOWN_REQUEST_BYTES = 35024
EXPECTED_UNKNOWN_ATTEMPT_HASH = "5f43aa6a13cd1e87e0b3c5df80e5b1ba45303e58bec178141b724e1a8e12f172"
EXPECTED_UNKNOWN_RECEIPT_HASH = "f16ab2f3bbe1735f0b34f8b908482f1ae161defc22707dc31cac22e51c967412"
EXPECTED_ONE_CALL_RECOVERY_CEILING_USD = Decimal("0.136712")
EXPECTED_PRE_RECOVERY_STAGE_SPEND_UPPER_USD = Decimal("0.4622210")
EXPECTED_POST_RECOVERY_AGGREGATE_SPEND_UPPER_USD = Decimal("0.5989330")


class B4ReopenInitialUnknownDispatchRecoveryError(ValueError):
    pass


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenInitialUnknownDispatchRecoveryError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise B4ReopenInitialUnknownDispatchRecoveryError(f"{label} root must be object")
    return value


def _read_jsonl(path: str | Path, *, label: str) -> list[dict[str, Any]]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise B4ReopenInitialUnknownDispatchRecoveryError(f"unable to read {label}") from exc
    result: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise B4ReopenInitialUnknownDispatchRecoveryError(
                f"{label} line {index} is not JSON"
            ) from exc
        if not isinstance(value, dict):
            raise B4ReopenInitialUnknownDispatchRecoveryError(
                f"{label} line {index} root must be object"
            )
        result.append(value)
    return result


def _decimal(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise B4ReopenInitialUnknownDispatchRecoveryError(
            f"{field_name} must be decimal string"
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise B4ReopenInitialUnknownDispatchRecoveryError(f"{field_name} invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise B4ReopenInitialUnknownDispatchRecoveryError(
            f"{field_name} must be finite and non-negative"
        )
    return parsed


def _verify_self_hash(
    payload: Mapping[str, Any],
    *,
    hash_field: str,
    expected_hash: str,
    label: str,
) -> str:
    observed = payload.get(hash_field)
    if observed != expected_hash:
        raise B4ReopenInitialUnknownDispatchRecoveryError(f"{label} hash drift")
    if observed != canonical_sha256(payload, exclude_fields=(hash_field,)):
        raise B4ReopenInitialUnknownDispatchRecoveryError(f"{label} self-hash mismatch")
    return str(observed)


def compute_recovery_cost_bounds(
    *,
    known_cost_usd: Decimal,
    missing_call_ceiling_usd: Decimal,
) -> tuple[Decimal, Decimal]:
    if (
        not known_cost_usd.is_finite()
        or known_cost_usd < 0
        or not missing_call_ceiling_usd.is_finite()
        or missing_call_ceiling_usd < 0
    ):
        raise B4ReopenInitialUnknownDispatchRecoveryError(
            "recovery cost inputs must be finite and non-negative"
        )
    pre_recovery_upper = known_cost_usd + missing_call_ceiling_usd
    post_recovery_aggregate_upper = known_cost_usd + (missing_call_ceiling_usd * 2)
    return pre_recovery_upper, post_recovery_aggregate_upper


def validate_missing_plan_item(
    item: ReopenInitialRuntimePlanItem,
    row: Mapping[str, Any],
) -> Decimal:
    if item.dispatch_index != EXPECTED_UNKNOWN_DISPATCH_INDEX:
        raise B4ReopenInitialUnknownDispatchRecoveryError("missing dispatch index drift")
    if item.candidate_id != EXPECTED_UNKNOWN_CANDIDATE:
        raise B4ReopenInitialUnknownDispatchRecoveryError("missing candidate drift")
    if item.lane != CouncilLane.RED_TEAM:
        raise B4ReopenInitialUnknownDispatchRecoveryError("missing lane drift")
    if item.stage != CouncilRequestStage.RED_TEAM_INITIAL:
        raise B4ReopenInitialUnknownDispatchRecoveryError("missing stage drift")
    if item.request.request_hash != EXPECTED_UNKNOWN_REQUEST_HASH:
        raise B4ReopenInitialUnknownDispatchRecoveryError("missing request hash drift")
    if item.request_body_utf8_bytes != EXPECTED_UNKNOWN_REQUEST_BYTES:
        raise B4ReopenInitialUnknownDispatchRecoveryError("missing request byte size drift")
    if item.request.request_payload.get("max_output_tokens") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise B4ReopenInitialUnknownDispatchRecoveryError("missing request output cap drift")
    if item.request.request_payload.get("store") is not False:
        raise B4ReopenInitialUnknownDispatchRecoveryError("missing request must retain store=false")
    expected_row = {
        "candidate_id": EXPECTED_UNKNOWN_CANDIDATE,
        "lane": EXPECTED_UNKNOWN_LANE,
        "stage": EXPECTED_UNKNOWN_STAGE,
        "request_hash": EXPECTED_UNKNOWN_REQUEST_HASH,
        "request_body_utf8_bytes": EXPECTED_UNKNOWN_REQUEST_BYTES,
        "max_output_tokens": EXPECTED_MAX_OUTPUT_TOKENS,
        "model": EXPECTED_SELECTED_MODEL["model"],
        "reasoning_effort": EXPECTED_SELECTED_MODEL["reasoning_effort"],
    }
    for key, expected in expected_row.items():
        if row.get(key) != expected:
            raise B4ReopenInitialUnknownDispatchRecoveryError(
                f"missing cost row drift: {key}"
            )
    ceiling = _decimal(
        row.get("per_call_cost_upper_bound_usd"),
        field_name="missing one-call cost ceiling",
    )
    if ceiling != EXPECTED_ONE_CALL_RECOVERY_CEILING_USD:
        raise B4ReopenInitialUnknownDispatchRecoveryError(
            "missing one-call cost ceiling drift"
        )
    return ceiling


def _verify_source_authorization(auth: Mapping[str, Any]) -> None:
    _verify_self_hash(
        auth,
        hash_field="artifact_hash",
        expected_hash=EXPECTED_SOURCE_AUTHORIZATION_HASH,
        label="source paid authorization",
    )
    if auth.get("status") != "AUTHORIZED_FOR_ONE_B4_REOPEN_INITIAL_RUN":
        raise B4ReopenInitialUnknownDispatchRecoveryError("source authorization status drift")
    if auth.get("run_id") != EXPECTED_SOURCE_RUN_ID:
        raise B4ReopenInitialUnknownDispatchRecoveryError("source authorization run id drift")
    if auth.get("runner_code_commit_sha") != EXPECTED_SOURCE_RUNNER_HEAD:
        raise B4ReopenInitialUnknownDispatchRecoveryError("source authorization HEAD drift")
    if auth.get("source_cost_preflight_artifact_hash") != EXPECTED_COST_PREFLIGHT_HASH:
        raise B4ReopenInitialUnknownDispatchRecoveryError("source authorization cost lineage drift")
    if auth.get("request_manifest_hash") != EXPECTED_REQUEST_MANIFEST_HASH:
        raise B4ReopenInitialUnknownDispatchRecoveryError("source authorization manifest drift")
    if auth.get("planned_paid_calls_max") != EXPECTED_CALLS:
        raise B4ReopenInitialUnknownDispatchRecoveryError("source authorization call ceiling drift")
    if auth.get("authorization_consumption_rule") != "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT":
        raise B4ReopenInitialUnknownDispatchRecoveryError(
            "source authorization consumption rule drift"
        )


def _verify_blocked(blocked: Mapping[str, Any]) -> None:
    _verify_self_hash(
        blocked,
        hash_field="artifact_hash",
        expected_hash=EXPECTED_BLOCKED_ARTIFACT_HASH,
        label="blocked Initial artifact",
    )
    expected = {
        "artifact_version": "B4_REOPEN_INITIAL_COUNCIL_BLOCKED_v0_1",
        "status": "B4_REOPEN_INITIAL_COUNCIL_NOT_FROZEN",
        "run_id": EXPECTED_SOURCE_RUN_ID,
        "code_commit_sha": EXPECTED_SOURCE_RUNNER_HEAD,
        "paid_authorization_artifact_hash": EXPECTED_SOURCE_AUTHORIZATION_HASH,
        "source_cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "authorization_consumed": True,
        "processed_opinion_count": 8,
        "provider_dispatch_attempts": 9,
        "model_calls_known_completed": 8,
        "cost_receipt_status": "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH",
        "blocked_reason": "UNKNOWN_PROVIDER_DISPATCH:ResponsesRuntimeError",
        "receipt_manifest_hash": EXPECTED_RECEIPT_MANIFEST_HASH,
        "initial_freeze_barrier": False,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "rerun_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    for key, value in expected.items():
        if blocked.get(key) != value:
            raise B4ReopenInitialUnknownDispatchRecoveryError(
                f"blocked Initial artifact drift: {key}"
            )
    if _decimal(blocked.get("actual_cost_usd_known"), field_name="known Initial cost") != EXPECTED_KNOWN_COST_USD:
        raise B4ReopenInitialUnknownDispatchRecoveryError("known Initial cost drift")
    records = blocked.get("processed_records")
    if not isinstance(records, list) or len(records) != 8:
        raise B4ReopenInitialUnknownDispatchRecoveryError(
            "blocked artifact must preserve exact eight processed records"
        )
    observed = [(item.get("candidate_id"), item.get("lane")) for item in records if isinstance(item, Mapping)]
    expected_identity = [
        ("NVDA", "BULL"),
        ("NVDA", "BEAR"),
        ("NVDA", "RED_TEAM"),
        ("MSFT", "BULL"),
        ("MSFT", "BEAR"),
        ("MSFT", "RED_TEAM"),
        ("META", "BULL"),
        ("META", "BEAR"),
    ]
    if observed != expected_identity:
        raise B4ReopenInitialUnknownDispatchRecoveryError(
            "blocked processed-record identity coverage drift"
        )


def _verify_journal(
    events: Sequence[Mapping[str, Any]],
    blocked: Mapping[str, Any],
) -> None:
    if len(events) != 18:
        raise B4ReopenInitialUnknownDispatchRecoveryError("source journal must contain exact 18 events")
    attempt_hashes: list[str] = []
    receipt_hashes: list[str] = []
    for dispatch_index in range(1, 10):
        attempt = events[(dispatch_index - 1) * 2]
        receipt = events[(dispatch_index - 1) * 2 + 1]
        attempt_hash = attempt.get("event_hash")
        if not isinstance(attempt_hash, str) or attempt_hash != canonical_sha256(
            attempt, exclude_fields=("event_hash",)
        ):
            raise B4ReopenInitialUnknownDispatchRecoveryError(
                f"dispatch attempt {dispatch_index} self-hash mismatch"
            )
        receipt_hash = receipt.get("receipt_hash")
        if not isinstance(receipt_hash, str) or receipt_hash != canonical_sha256(
            receipt, exclude_fields=("receipt_hash",)
        ):
            raise B4ReopenInitialUnknownDispatchRecoveryError(
                f"paid receipt {dispatch_index} self-hash mismatch"
            )
        if attempt.get("event_type") != "PROVIDER_DISPATCH_ATTEMPT":
            raise B4ReopenInitialUnknownDispatchRecoveryError(
                f"journal event {dispatch_index} attempt type drift"
            )
        if receipt.get("event_type") != "PROVIDER_DISPATCH_RESULT":
            raise B4ReopenInitialUnknownDispatchRecoveryError(
                f"journal event {dispatch_index} receipt type drift"
            )
        if attempt.get("dispatch_index") != dispatch_index or receipt.get("dispatch_index") != dispatch_index:
            raise B4ReopenInitialUnknownDispatchRecoveryError(
                f"journal dispatch index {dispatch_index} drift"
            )
        if attempt.get("request_hash") != receipt.get("request_hash"):
            raise B4ReopenInitialUnknownDispatchRecoveryError(
                f"journal request hash mismatch at {dispatch_index}"
            )
        if receipt.get("dispatch_attempt_event_hash") != attempt_hash:
            raise B4ReopenInitialUnknownDispatchRecoveryError(
                f"journal attempt/receipt binding mismatch at {dispatch_index}"
            )
        if dispatch_index <= 8:
            if (
                receipt.get("provider_response_received") is not True
                or receipt.get("validation_status") != "PASS"
                or receipt.get("cost_receipt_status") != "COMPLETE"
                or receipt.get("actual_cost_usd") is None
                or receipt.get("processed_record_hash") is None
            ):
                raise B4ReopenInitialUnknownDispatchRecoveryError(
                    f"completed source dispatch {dispatch_index} is not reusable"
                )
        else:
            if attempt_hash != EXPECTED_UNKNOWN_ATTEMPT_HASH:
                raise B4ReopenInitialUnknownDispatchRecoveryError("unknown attempt hash drift")
            if receipt_hash != EXPECTED_UNKNOWN_RECEIPT_HASH:
                raise B4ReopenInitialUnknownDispatchRecoveryError("unknown receipt hash drift")
            if attempt.get("candidate_id") != EXPECTED_UNKNOWN_CANDIDATE or attempt.get("lane") != EXPECTED_UNKNOWN_LANE:
                raise B4ReopenInitialUnknownDispatchRecoveryError("unknown attempt identity drift")
            if attempt.get("request_hash") != EXPECTED_UNKNOWN_REQUEST_HASH:
                raise B4ReopenInitialUnknownDispatchRecoveryError("unknown attempt request hash drift")
            if (
                receipt.get("provider_response_received") is not False
                or receipt.get("provider_dispatch_state_unknown") is not True
                or receipt.get("validation_status") != "FAIL"
                or receipt.get("cost_receipt_status") != "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH"
                or receipt.get("actual_cost_usd") is not None
                or receipt.get("validation_error") != "UNKNOWN_PROVIDER_DISPATCH:ResponsesRuntimeError"
            ):
                raise B4ReopenInitialUnknownDispatchRecoveryError(
                    "unknown dispatch receipt state drift"
                )
        attempt_hashes.append(attempt_hash)
        receipt_hashes.append(receipt_hash)

    observed_manifest = receipt_manifest_hash(
        dispatch_attempt_hashes=attempt_hashes,
        paid_call_receipt_hashes=receipt_hashes,
    )
    if observed_manifest != EXPECTED_RECEIPT_MANIFEST_HASH:
        raise B4ReopenInitialUnknownDispatchRecoveryError("source receipt manifest drift")
    if blocked.get("dispatch_attempt_hashes") != attempt_hashes:
        raise B4ReopenInitialUnknownDispatchRecoveryError("blocked attempt hash list drift")
    if blocked.get("paid_call_receipt_hashes") != receipt_hashes:
        raise B4ReopenInitialUnknownDispatchRecoveryError("blocked receipt hash list drift")


def build_recovery_plan_artifact(
    *,
    code_commit_sha: str,
    cost_preflight: Mapping[str, Any],
    source_authorization: Mapping[str, Any],
    blocked_artifact: Mapping[str, Any],
    journal_events: Sequence[Mapping[str, Any]],
    runtime_plan: Sequence[ReopenInitialRuntimePlanItem],
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise B4ReopenInitialUnknownDispatchRecoveryError("recovery plan exact git SHA invalid")
    verify_reopen_initial_cost_preflight(cost_preflight)
    _verify_source_authorization(source_authorization)
    _verify_blocked(blocked_artifact)
    _verify_journal(journal_events, blocked_artifact)
    if len(runtime_plan) != EXPECTED_CALLS:
        raise B4ReopenInitialUnknownDispatchRecoveryError("reconstructed runtime plan must be exact nine calls")
    rows = cost_preflight.get("initial_request_rows")
    if not isinstance(rows, list) or len(rows) != EXPECTED_CALLS:
        raise B4ReopenInitialUnknownDispatchRecoveryError("cost preflight request rows missing")
    missing = runtime_plan[EXPECTED_UNKNOWN_DISPATCH_INDEX - 1]
    one_call_ceiling = validate_missing_plan_item(missing, rows[EXPECTED_UNKNOWN_DISPATCH_INDEX - 1])
    pre_recovery_upper, post_recovery_upper = compute_recovery_cost_bounds(
        known_cost_usd=EXPECTED_KNOWN_COST_USD,
        missing_call_ceiling_usd=one_call_ceiling,
    )
    if pre_recovery_upper != EXPECTED_PRE_RECOVERY_STAGE_SPEND_UPPER_USD:
        raise B4ReopenInitialUnknownDispatchRecoveryError("pre-recovery spend upper bound drift")
    if post_recovery_upper != EXPECTED_POST_RECOVERY_AGGREGATE_SPEND_UPPER_USD:
        raise B4ReopenInitialUnknownDispatchRecoveryError("post-recovery aggregate spend upper bound drift")

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_run_id": EXPECTED_SOURCE_RUN_ID,
        "source_runner_code_commit_sha": EXPECTED_SOURCE_RUNNER_HEAD,
        "source_paid_authorization_artifact_hash": EXPECTED_SOURCE_AUTHORIZATION_HASH,
        "source_blocked_artifact_hash": EXPECTED_BLOCKED_ARTIFACT_HASH,
        "source_receipt_manifest_hash": EXPECTED_RECEIPT_MANIFEST_HASH,
        "source_cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT_HASH,
        "source_request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "source_authority_consumed": True,
        "source_authority_rerun_authorized": False,
        "source_provider_dispatch_attempts": 9,
        "source_model_calls_known_completed": 8,
        "source_processed_opinion_count": 8,
        "source_known_actual_cost_usd": str(EXPECTED_KNOWN_COST_USD),
        "source_cost_receipt_status": "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH",
        "source_initial_freeze_barrier": False,
        "source_rebuttal_authorized": False,
        "source_judge_authorized": False,
        "reusable_processed_opinion_count": 8,
        "reusable_processed_opinions_are_immutable": True,
        "missing_dispatch": {
            "dispatch_index": EXPECTED_UNKNOWN_DISPATCH_INDEX,
            "candidate_id": EXPECTED_UNKNOWN_CANDIDATE,
            "lane": EXPECTED_UNKNOWN_LANE,
            "stage": EXPECTED_UNKNOWN_STAGE,
            "request_hash": EXPECTED_UNKNOWN_REQUEST_HASH,
            "request_body_utf8_bytes": EXPECTED_UNKNOWN_REQUEST_BYTES,
            "dispatch_attempt_event_hash": EXPECTED_UNKNOWN_ATTEMPT_HASH,
            "paid_call_receipt_hash": EXPECTED_UNKNOWN_RECEIPT_HASH,
            "provider_response_received": False,
            "provider_dispatch_state_unknown": True,
            "validation_status": "FAIL",
            "cost_receipt_status": "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH",
            "blocked_reason": "UNKNOWN_PROVIDER_DISPATCH:ResponsesRuntimeError",
        },
        "forensic_classification": "NETWORK_URLERROR_UNKNOWN_PROVIDER_DISPATCH",
        "source_request_store": False,
        "lost_response_retrievable_via_responses_api": False,
        "lost_response_retrieval_reason": (
            "B4 request contract uses store=false; no response ID was received locally, so the lost output cannot be retrieved."
        ),
        "recovery_strategy": "ONE_FRESH_EXACT_REQUEST_FOR_MISSING_META_RED_TEAM_ONLY",
        "recovery_request_exactly_matches_missing_request_hash": True,
        "recovery_selected_model": dict(EXPECTED_SELECTED_MODEL),
        "recovery_max_output_tokens": EXPECTED_MAX_OUTPUT_TOKENS,
        "recovery_paid_calls_max": 1,
        "recovery_request_hash": EXPECTED_UNKNOWN_REQUEST_HASH,
        "recovery_request_body_utf8_bytes": EXPECTED_UNKNOWN_REQUEST_BYTES,
        "recovery_cost_ceiling_usd": str(one_call_ceiling),
        "original_unknown_dispatch_cost_upper_bound_usd": str(one_call_ceiling),
        "initial_spend_known_lower_bound_usd": str(EXPECTED_KNOWN_COST_USD),
        "initial_spend_upper_bound_before_recovery_usd": str(pre_recovery_upper),
        "aggregate_initial_spend_upper_bound_after_one_recovery_usd": str(post_recovery_upper),
        "aggregate_upper_bound_assumption": (
            "Assumes the original unknown ninth dispatch was fully billable at its conservative per-call ceiling and the recovery call is also billed at its full ceiling."
        ),
        "new_owner_cost_approval_required": True,
        "recovery_paid_dispatch_authorized": False,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "recovery_rerun_authorized": False,
        "provider_reads_authorized": False,
        "planned_provider_reads": 0,
        "broker_writes_authorized": 0,
        "alpaca_orders_authorized": 0,
        "live_money": "PROHIBITED",
        "final_initial_freeze_created": False,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def load_and_build_recovery_plan_artifact(
    *,
    code_commit_sha: str,
    cost_preflight_path: str | Path,
    source_authorization_path: str | Path,
    blocked_artifact_path: str | Path,
    receipt_journal_path: str | Path,
    runtime_plan: Sequence[ReopenInitialRuntimePlanItem],
) -> dict[str, Any]:
    return build_recovery_plan_artifact(
        code_commit_sha=code_commit_sha,
        cost_preflight=_read_object(cost_preflight_path, label="reopen Initial cost preflight"),
        source_authorization=_read_object(source_authorization_path, label="source paid authorization"),
        blocked_artifact=_read_object(blocked_artifact_path, label="source blocked Initial artifact"),
        journal_events=_read_jsonl(receipt_journal_path, label="source paid receipt journal"),
        runtime_plan=runtime_plan,
    )
