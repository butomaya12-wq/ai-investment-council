from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256

from .judge_eval_preflight import (
    EXPECTED_JUDGE_EVAL_CASE_IDS,
    EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX,
    build_judge_eval_cases,
    score_judge_eval_case,
)
from .judge_eval_runtime import JUDGE_EVAL_RUNTIME_VERSION
from .model_policy import (
    JUDGE_MODEL_LADDER,
    MODEL_POLICY_VERSION,
    CouncilModelStage,
    StageModelEvalResult,
    select_stage_model_from_eval,
)
from .proposal import JudgeDecisionProposalDraft


JUDGE_SELECTED_MODEL_AUTHORITY_VERSION = "B4_JUDGE_SELECTED_MODEL_AUTHORITY_v0_1"
JUDGE_SELECTED_MODEL_REPLAY_VERSION = "B4_JUDGE_DURABLE_RESULT_HASH_REPLAY_v0_1"
JUDGE_SELECTED_MODEL_AUTHORITY_STATUS = "SELECTED_MODEL_AUTHORITY_FROZEN"
EXPECTED_JUDGE_EVAL_ARTIFACT_VERSION = "B4_JUDGE_MODEL_EVAL_ARTIFACT_v0_1"
EXPECTED_JUDGE_EVAL_ARTIFACT_HASH = (
    "b1a509699d4753f019db4dfb21fd7a0cd33ed7872e7d67a98c280dde36649e5d"
)
EXPECTED_JUDGE_EVAL_RUN_ID = (
    "AIC-B4-JUDGE-EVAL-20260830T132815326431Z-4a1caeb8a184"
)
EXPECTED_JUDGE_EVAL_SOURCE_HEAD = (
    "372141b928ad126e3989d3df2ccfa1d48392952b"
)
EXPECTED_JUDGE_EVAL_PAID_AUTHORIZATION_HASH = (
    "75b95f381471cdf50d638382b2d4f119b21cce3f7c4becbb3b976eaba8dcbabd"
)
EXPECTED_JUDGE_EVAL_RECEIPT_MANIFEST_HASH = (
    "8478cf40a495c81335358bcaad8b954fb5b018925efcdd95a5de5879737d32e6"
)
EXPECTED_JUDGE_EVAL_ACTUAL_COST_USD = Decimal("0.4492515")
EXPECTED_SELECTED_JUDGE = {
    "candidate_key": "J1",
    "model": "gpt-5.6-terra",
    "reasoning_effort": "medium",
    "ladder_position": 1,
}


class JudgeModelSelectionReplayError(ValueError):
    pass


def _decimal(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise JudgeModelSelectionReplayError(f"{field_name} must be decimal string")
    try:
        result = Decimal(value)
    except Exception as exc:
        raise JudgeModelSelectionReplayError(f"{field_name} invalid decimal") from exc
    if not result.is_finite() or result < 0:
        raise JudgeModelSelectionReplayError(f"{field_name} invalid")
    return result


def _verify_eval_artifact(payload: Mapping[str, Any]) -> str:
    observed = payload.get("artifact_hash")
    if observed != EXPECTED_JUDGE_EVAL_ARTIFACT_HASH:
        raise JudgeModelSelectionReplayError("Judge eval artifact hash drift")
    if observed != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise JudgeModelSelectionReplayError("Judge eval artifact self-hash mismatch")
    if payload.get("artifact_version") != EXPECTED_JUDGE_EVAL_ARTIFACT_VERSION:
        raise JudgeModelSelectionReplayError("Judge eval artifact version drift")
    if payload.get("status") != "PASS_SELECTED":
        raise JudgeModelSelectionReplayError("Judge eval is not PASS_SELECTED")
    if payload.get("run_id") != EXPECTED_JUDGE_EVAL_RUN_ID:
        raise JudgeModelSelectionReplayError("Judge eval run ID drift")
    if payload.get("code_commit_sha") != EXPECTED_JUDGE_EVAL_SOURCE_HEAD:
        raise JudgeModelSelectionReplayError("Judge eval source HEAD drift")
    if payload.get("paid_authorization_artifact_hash") != EXPECTED_JUDGE_EVAL_PAID_AUTHORIZATION_HASH:
        raise JudgeModelSelectionReplayError("Judge eval authorization hash drift")
    if payload.get("receipt_manifest_hash") != EXPECTED_JUDGE_EVAL_RECEIPT_MANIFEST_HASH:
        raise JudgeModelSelectionReplayError("Judge eval receipt manifest drift")
    if payload.get("dispatch_attempts") != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
        raise JudgeModelSelectionReplayError("Judge eval dispatch count drift")
    if payload.get("model_calls") != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
        raise JudgeModelSelectionReplayError("Judge eval model-call count drift")
    if payload.get("cost_receipt_status") != "COMPLETE":
        raise JudgeModelSelectionReplayError("Judge eval cost receipt incomplete")
    if _decimal(payload.get("actual_cost_usd"), field_name="actual_cost_usd") != EXPECTED_JUDGE_EVAL_ACTUAL_COST_USD:
        raise JudgeModelSelectionReplayError("Judge eval actual cost drift")
    if payload.get("judge_eval_authorization_consumed") is not True:
        raise JudgeModelSelectionReplayError("Judge eval authorization not consumed")
    if payload.get("automatic_repair_calls") != 0:
        raise JudgeModelSelectionReplayError("Judge eval contains automatic repair")
    if payload.get("production_judge_authorized") is not False:
        raise JudgeModelSelectionReplayError("Judge eval unexpectedly authorizes production Judge")
    if payload.get("rerun_authorized") is not False:
        raise JudgeModelSelectionReplayError("Judge eval unexpectedly authorizes rerun")
    for field in ("provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise JudgeModelSelectionReplayError(f"Judge eval side-effect invariant violated: {field}")
    if payload.get("live_money") != "PROHIBITED":
        raise JudgeModelSelectionReplayError("Judge eval live-money invariant drift")
    return observed


def _expected_order() -> tuple[tuple[str, str], ...]:
    return tuple(
        (candidate.candidate_key, case_id)
        for candidate in JUDGE_MODEL_LADDER
        for case_id in EXPECTED_JUDGE_EVAL_CASE_IDS
    )


def _result_hash_payload(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "eval_version": "B4_JUDGE_MODEL_EVAL_v0_1",
        "runtime_version": JUDGE_EVAL_RUNTIME_VERSION,
        "case_id": receipt["case_id"],
        "name": receipt["case_name"],
        "critical_safety": receipt["critical_safety"],
        "passed": receipt["case_result"] == "PASS",
        "findings": list(receipt["findings"]),
        "response_id": receipt["response_id"],
        "requested_model": receipt["requested_model"],
        "effective_model": receipt["effective_model"],
        "model_calls": 1 if receipt["provider_response_received"] else 0,
        "latency_ms": receipt["latency_ms"],
        "input_tokens": receipt["input_tokens"],
        "cached_tokens": receipt["cached_tokens"],
        "cache_write_tokens": receipt["cache_write_tokens"],
        "output_tokens": receipt["output_tokens"],
        "reasoning_tokens": receipt["reasoning_tokens"],
        "actual_cost_usd": receipt["actual_cost_usd"],
        "cost_receipt_status": receipt["cost_receipt_status"],
        "output_hash": receipt["output_hash"],
        "structured_output_hash": receipt["structured_output_hash"],
    }


def _case_record(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "case_id": receipt["case_id"],
        "name": receipt["case_name"],
        "critical_safety": receipt["critical_safety"],
        "passed": receipt["case_result"] == "PASS",
        "findings": list(receipt["findings"]),
        "response_id": receipt["response_id"],
        "requested_model": receipt["requested_model"],
        "effective_model": receipt["effective_model"],
        "model_calls": 1 if receipt["provider_response_received"] else 0,
        "latency_ms": receipt["latency_ms"],
        "input_tokens": receipt["input_tokens"],
        "cached_tokens": receipt["cached_tokens"],
        "cache_write_tokens": receipt["cache_write_tokens"],
        "output_tokens": receipt["output_tokens"],
        "reasoning_tokens": receipt["reasoning_tokens"],
        "actual_cost_usd": receipt["actual_cost_usd"],
        "cost_receipt_status": receipt["cost_receipt_status"],
        "output_hash": receipt["output_hash"],
        "structured_output_hash": receipt["structured_output_hash"],
        "result_hash": receipt["result_hash"],
    }


def _validate_receipts(
    eval_artifact: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], tuple[StageModelEvalResult, ...]]:
    if len(receipts) != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
        raise JudgeModelSelectionReplayError("Judge selection replay requires exactly 21 receipts")
    if tuple((row.get("candidate_key"), row.get("case_id")) for row in receipts) != _expected_order():
        raise JudgeModelSelectionReplayError("Judge eval receipt order drift")

    case_by_id = {case.case_id: case for case in build_judge_eval_cases()}
    receipt_hashes: list[str] = []
    records: list[dict[str, Any]] = []
    eval_results: list[StageModelEvalResult] = []
    total_cost = Decimal("0")

    offset = 0
    for candidate in JUDGE_MODEL_LADDER:
        candidate_receipts = receipts[offset : offset + len(EXPECTED_JUDGE_EVAL_CASE_IDS)]
        offset += len(EXPECTED_JUDGE_EVAL_CASE_IDS)
        case_records: list[dict[str, Any]] = []
        candidate_cost = Decimal("0")
        candidate_latency = 0
        candidate_tokens = 0
        critical_failures = 0

        for dispatch_index, receipt in enumerate(candidate_receipts, start=offset - len(EXPECTED_JUDGE_EVAL_CASE_IDS) + 1):
            observed_receipt_hash = receipt.get("receipt_hash")
            if not isinstance(observed_receipt_hash, str) or observed_receipt_hash != canonical_sha256(
                receipt, exclude_fields=("receipt_hash",)
            ):
                raise JudgeModelSelectionReplayError("Judge eval receipt self-hash mismatch")
            receipt_hashes.append(observed_receipt_hash)
            if receipt.get("dispatch_index") != dispatch_index:
                raise JudgeModelSelectionReplayError("Judge eval receipt dispatch index drift")
            if receipt.get("run_id") != EXPECTED_JUDGE_EVAL_RUN_ID:
                raise JudgeModelSelectionReplayError("Judge eval receipt run ID drift")
            if receipt.get("code_commit_sha") != EXPECTED_JUDGE_EVAL_SOURCE_HEAD:
                raise JudgeModelSelectionReplayError("Judge eval receipt source HEAD drift")
            if receipt.get("paid_authorization_artifact_hash") != EXPECTED_JUDGE_EVAL_PAID_AUTHORIZATION_HASH:
                raise JudgeModelSelectionReplayError("Judge eval receipt authorization drift")
            if receipt.get("dispatch_attempted") is not True or receipt.get("provider_response_received") is not True:
                raise JudgeModelSelectionReplayError("Judge eval receipt lacks completed provider response")
            if receipt.get("cost_receipt_status") != "COMPLETE":
                raise JudgeModelSelectionReplayError("Judge eval receipt cost incomplete")
            if receipt.get("case_result") != "PASS" or receipt.get("findings") != []:
                raise JudgeModelSelectionReplayError("paid Judge eval receipt is not semantic PASS")
            if receipt.get("automatic_repair_attempted") is not False:
                raise JudgeModelSelectionReplayError("Judge eval receipt contains repair")
            if receipt.get("production_judge_authorized") is not False or receipt.get("rerun_authorized") is not False:
                raise JudgeModelSelectionReplayError("Judge eval receipt grants later-stage authority")
            if receipt.get("broker_writes") != 0 or receipt.get("alpaca_orders") != 0 or receipt.get("live_money") != "PROHIBITED":
                raise JudgeModelSelectionReplayError("Judge eval receipt side-effect invariant drift")
            if receipt.get("requested_model") != candidate.model or receipt.get("reasoning_effort") != candidate.reasoning_effort:
                raise JudgeModelSelectionReplayError("Judge eval receipt model configuration drift")

            structured = receipt.get("structured_output")
            if not isinstance(structured, Mapping):
                raise JudgeModelSelectionReplayError("Judge eval durable structured output missing")
            if receipt.get("structured_output_hash") != canonical_sha256(structured):
                raise JudgeModelSelectionReplayError("Judge eval structured output hash mismatch")
            proposal = JudgeDecisionProposalDraft.model_validate(dict(structured))
            case = case_by_id[str(receipt["case_id"])]
            replay_passed, replay_findings = score_judge_eval_case(proposal, case=case)
            if replay_passed is not True or replay_findings != ():
                raise JudgeModelSelectionReplayError("Judge eval semantic replay no longer passes")
            if receipt.get("result_hash") != canonical_sha256(_result_hash_payload(receipt)):
                raise JudgeModelSelectionReplayError("Judge eval durable result_hash replay mismatch")

            cost = _decimal(receipt.get("actual_cost_usd"), field_name="receipt.actual_cost_usd")
            candidate_cost += cost
            total_cost += cost
            latency = receipt.get("latency_ms")
            input_tokens = receipt.get("input_tokens")
            output_tokens = receipt.get("output_tokens")
            if type(latency) is not int or latency < 0:
                raise JudgeModelSelectionReplayError("Judge eval receipt latency invalid")
            if type(input_tokens) is not int or type(output_tokens) is not int or input_tokens < 0 or output_tokens < 0:
                raise JudgeModelSelectionReplayError("Judge eval receipt token counts invalid")
            candidate_latency += latency
            candidate_tokens += input_tokens + output_tokens
            if receipt.get("critical_safety") is True and receipt.get("case_result") != "PASS":
                critical_failures += 1
            case_records.append(_case_record(receipt))

        eval_result = StageModelEvalResult(
            candidate_key=candidate.candidate_key,
            all_required_checks_passed=True,
            critical_safety_failures=critical_failures,
            estimated_cost_usd=candidate_cost,
            latency_ms=candidate_latency,
            total_tokens=candidate_tokens,
        )
        eval_results.append(eval_result)
        record: dict[str, Any] = {
            "candidate_key": candidate.candidate_key,
            "model": candidate.model,
            "reasoning_effort": candidate.reasoning_effort,
            "ladder_position": candidate.ladder_position,
            "cases": case_records,
            "passed_cases": len(case_records),
            "required_cases": len(case_records),
            "all_required_checks_passed": True,
            "critical_safety_failures": critical_failures,
            "estimated_cost_usd": str(candidate_cost),
            "latency_ms": candidate_latency,
            "total_tokens": candidate_tokens,
        }
        record["record_hash"] = canonical_sha256(record)
        records.append(record)

    if canonical_sha256({"receipt_hashes": receipt_hashes}) != EXPECTED_JUDGE_EVAL_RECEIPT_MANIFEST_HASH:
        raise JudgeModelSelectionReplayError("Judge eval durable receipt manifest replay mismatch")
    if eval_artifact.get("paid_call_receipt_hashes") != receipt_hashes:
        raise JudgeModelSelectionReplayError("Judge eval artifact receipt list differs from journal")
    if eval_artifact.get("candidate_records") != records:
        raise JudgeModelSelectionReplayError("Judge eval candidate records differ from durable receipt replay")
    if total_cost != EXPECTED_JUDGE_EVAL_ACTUAL_COST_USD:
        raise JudgeModelSelectionReplayError("Judge eval receipt total cost drift")
    return records, tuple(eval_results)


def build_judge_selected_model_authority(
    eval_artifact: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    eval_hash = _verify_eval_artifact(eval_artifact)
    records, eval_results = _validate_receipts(eval_artifact, receipts)
    selection = select_stage_model_from_eval(CouncilModelStage.JUDGE, eval_results)
    if selection.selected_candidate is None:
        raise JudgeModelSelectionReplayError("Judge model selection replay produced no passing candidate")
    selected = {
        "candidate_key": selection.selected_candidate.candidate_key,
        "model": selection.selected_candidate.model,
        "reasoning_effort": selection.selected_candidate.reasoning_effort,
        "ladder_position": selection.selected_candidate.ladder_position,
    }
    if selected != EXPECTED_SELECTED_JUDGE:
        raise JudgeModelSelectionReplayError("Judge model selection replay did not select frozen J1")
    expected_selection = eval_artifact.get("selection")
    if not isinstance(expected_selection, Mapping) or expected_selection.get("status") != "SELECTED":
        raise JudgeModelSelectionReplayError("Judge eval stored selection malformed")
    if expected_selection.get("selected_candidate") != selected:
        raise JudgeModelSelectionReplayError("Judge eval stored selection differs from replay")
    if expected_selection.get("reason_code") != selection.reason_code:
        raise JudgeModelSelectionReplayError("Judge eval stored selection reason differs from replay")

    summaries = [
        {
            "candidate_key": record["candidate_key"],
            "model": record["model"],
            "reasoning_effort": record["reasoning_effort"],
            "passed_cases": record["passed_cases"],
            "required_cases": record["required_cases"],
            "critical_safety_failures": record["critical_safety_failures"],
            "estimated_cost_usd": record["estimated_cost_usd"],
            "record_hash": record["record_hash"],
        }
        for record in records
    ]
    authority: dict[str, Any] = {
        "artifact_version": JUDGE_SELECTED_MODEL_AUTHORITY_VERSION,
        "replay_contract_version": JUDGE_SELECTED_MODEL_REPLAY_VERSION,
        "status": JUDGE_SELECTED_MODEL_AUTHORITY_STATUS,
        "stage": "JUDGE",
        "model_policy_version": MODEL_POLICY_VERSION,
        "source_model_eval_artifact_hash": eval_hash,
        "source_model_eval_run_id": EXPECTED_JUDGE_EVAL_RUN_ID,
        "source_git_commit_sha": EXPECTED_JUDGE_EVAL_SOURCE_HEAD,
        "source_paid_authorization_artifact_hash": EXPECTED_JUDGE_EVAL_PAID_AUTHORIZATION_HASH,
        "source_receipt_manifest_hash": EXPECTED_JUDGE_EVAL_RECEIPT_MANIFEST_HASH,
        "semantic_replay_receipt_count": EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX,
        "semantic_replay_passed_cases": EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX,
        "replayed_result_hash_count": EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX,
        "actual_eval_cost_usd": str(EXPECTED_JUDGE_EVAL_ACTUAL_COST_USD),
        "candidate_summaries": summaries,
        "selected_candidate": selected,
        "selection_reason_code": selection.reason_code,
        "selection_rule": "LOWEST_COST_PASSING_CONFIG_THEN_LATENCY_THEN_TOKENS",
        "production_judge_authorized": False,
        "rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    authority["artifact_hash"] = canonical_sha256(authority)
    return authority


def verify_judge_selected_model_authority(payload: Mapping[str, Any]) -> str:
    observed = payload.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise JudgeModelSelectionReplayError("Judge selected-model authority self-hash mismatch")
    if payload.get("artifact_version") != JUDGE_SELECTED_MODEL_AUTHORITY_VERSION:
        raise JudgeModelSelectionReplayError("Judge selected-model authority version drift")
    if payload.get("replay_contract_version") != JUDGE_SELECTED_MODEL_REPLAY_VERSION:
        raise JudgeModelSelectionReplayError("Judge selected-model replay contract drift")
    if payload.get("status") != JUDGE_SELECTED_MODEL_AUTHORITY_STATUS:
        raise JudgeModelSelectionReplayError("Judge selected-model authority is not frozen")
    if payload.get("source_model_eval_artifact_hash") != EXPECTED_JUDGE_EVAL_ARTIFACT_HASH:
        raise JudgeModelSelectionReplayError("Judge selected-model source eval drift")
    if payload.get("source_receipt_manifest_hash") != EXPECTED_JUDGE_EVAL_RECEIPT_MANIFEST_HASH:
        raise JudgeModelSelectionReplayError("Judge selected-model receipt manifest drift")
    if payload.get("selected_candidate") != EXPECTED_SELECTED_JUDGE:
        raise JudgeModelSelectionReplayError("Judge selected-model authority is not J1")
    if payload.get("semantic_replay_receipt_count") != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
        raise JudgeModelSelectionReplayError("Judge selected-model replay receipt count drift")
    if payload.get("semantic_replay_passed_cases") != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
        raise JudgeModelSelectionReplayError("Judge selected-model replay pass count drift")
    if payload.get("replayed_result_hash_count") != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
        raise JudgeModelSelectionReplayError("Judge selected-model result hash replay count drift")
    if payload.get("production_judge_authorized") is not False or payload.get("rerun_authorized") is not False:
        raise JudgeModelSelectionReplayError("Judge selected-model authority grants execution/rerun")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise JudgeModelSelectionReplayError(f"Judge selected-model authority {field} must be zero")
    if payload.get("live_money") != "PROHIBITED":
        raise JudgeModelSelectionReplayError("Judge selected-model live-money invariant drift")
    return observed
