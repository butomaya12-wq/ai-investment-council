from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import RESEARCH_REOPEN_REQUEST_V1


POSTPROCESS_VERSION = "B4_REOPEN_JUDGE_PROPOSAL_POSTPROCESS_v0_1"
POSTPROCESS_STATUS = "B4_REOPEN_JUDGE_RESEARCH_REOPEN_REQUEST_PERSISTED"
REOPEN_REQUEST_ID = "B4_REOPEN_JUDGE_RESEARCH_REOPEN_REQUEST_001"
NEXT_GATE = "B3_RESEARCH_REOPEN_S00_SCOPE_ZERO_CALL"

EXPECTED_SOURCE_CODE_SHA = "83589097a18b92a3afe4afac60d206f9320e5c27"
EXPECTED_RESULT_HASH = "1f77c26c7198cae5c809b8c2fd36cf03dd1bfceb5a2c6e5ac505b1c4b6334090"
EXPECTED_RUN_ID = "AIC-B4-REOPEN-JUDGE-20260831T062734480149Z-5af852b4e156"
EXPECTED_AUTH_HASH = "8f97c35cebc285f38c40c772f4b5777c68d0b6063cd8590d3f4b59f95065a8d5"
EXPECTED_ATTEMPT_HASH = "07b3d3dd173d1d92c42db102d04d01c3d114edad4e95b1290c74174ddc5c376a"
EXPECTED_RECEIPT_HASH = "d761ba0d1d8b25f944ba40de5ea8d5d0992117a495a1c62684700f29c8213284"
EXPECTED_PROPOSAL_HASH = "fa333d33a578502d0175f9da117772ba1e9571af7322fa5796f12ebce82ab960"
EXPECTED_REQUEST_HASH = "37b8c4bceff2a853bfce1d6333bd9778c0c9132eda358ba430b8ee12ffaf0562"
EXPECTED_REQUEST_MANIFEST_HASH = "7ebbdea18defe1b7c65677e456b8a05334fa152dd412d2ad1694b5c237595d56"
EXPECTED_COST_CEILING_USD = Decimal("0.4877265")
EXPECTED_ACTUAL_COST_USD = Decimal("0.1525175")
EXPECTED_REOPEN_REASONS = (
    "Q4_RECENT_DEVELOPMENTS",
    "B4_MATERIAL_CLAIM_MSFT_RED_TEAM_4f85cd62978ad094a81b",
    "B4_MATERIAL_CLAIM_MSFT_RED_TEAM_f9a27271c11b2a79ac37",
)


class ReopenJudgePostprocessError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReopenJudgePostprocessError(message)


def _decimal(value: object, *, field: str) -> Decimal:
    _require(isinstance(value, str), f"{field} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ReopenJudgePostprocessError(f"{field} is invalid") from exc
    _require(parsed.is_finite() and parsed >= 0, f"{field} is invalid")
    return parsed


def _self_hash(payload: Mapping[str, Any], *, field: str) -> str:
    observed = payload.get(field)
    _require(isinstance(observed, str), f"missing {field}")
    expected = canonical_sha256(payload, exclude_fields=(field,))
    _require(observed == expected, f"{field} self-hash mismatch")
    return observed


def verify_judge_result(
    payload: Mapping[str, Any],
    *,
    expected_hash: str = EXPECTED_RESULT_HASH,
    expected_run_id: str = EXPECTED_RUN_ID,
) -> str:
    result_hash = _self_hash(payload, field="artifact_hash")
    _require(result_hash == expected_hash, "Judge V02 result hash drift")
    _require(payload.get("artifact_version") == "B4_REOPEN_JUDGE_PRODUCTION_RESULT_v0_2", "Judge V02 result version drift")
    _require(payload.get("status") == "B4_REOPEN_JUDGE_PROPOSAL_FROZEN", "Judge V02 proposal is not frozen")
    _require(payload.get("run_id") == expected_run_id, "Judge V02 run id drift")
    _require(payload.get("code_commit_sha") == EXPECTED_SOURCE_CODE_SHA, "Judge V02 source code SHA drift")
    _require(payload.get("outcome") == "WATCH", "postprocess is event-bound to WATCH")
    _require(payload.get("primary_candidate_id") == "META", "WATCH primary candidate drift")
    _require(payload.get("watch_candidate_ids") == ["META", "NVDA", "MSFT"], "WATCH candidate order drift")
    _require(payload.get("research_reopen_required") is True, "research reopen must remain required")
    _require(tuple(payload.get("research_reopen_reason_codes") or ()) == EXPECTED_REOPEN_REASONS, "research reopen reason drift")
    _require(payload.get("next_directive") == "RESEARCH_REOPEN_REQUEST", "Judge next directive drift")
    _require(payload.get("next_gate") == "B4_REOPEN_JUDGE_PROPOSAL_POSTPROCESS_ZERO_CALL", "Judge next gate drift")
    _require(payload.get("judge_authorization_consumed") is True, "Judge authority must be consumed")
    _require(payload.get("model_calls") == 1, "Judge source must contain exactly one model call")
    _require(payload.get("automatic_repair_calls") == 0, "Judge repair drift")
    _require(payload.get("automatic_retries") == 0, "Judge retry drift")
    _require(payload.get("rerun_authorized") is False, "Judge rerun must remain forbidden")
    _require(payload.get("cost_receipt_status") == "COMPLETE", "Judge cost receipt incomplete")
    _require(_decimal(payload.get("actual_cost_usd"), field="actual Judge cost") == EXPECTED_ACTUAL_COST_USD, "Judge actual cost drift")
    _require(_decimal(payload.get("approved_cost_ceiling_usd"), field="approved Judge ceiling") == EXPECTED_COST_CEILING_USD, "Judge ceiling drift")
    _require(payload.get("paid_authorization_artifact_hash") == EXPECTED_AUTH_HASH, "Judge authorization lineage drift")
    _require(payload.get("paid_call_receipt_hash") == EXPECTED_RECEIPT_HASH, "Judge receipt lineage drift")
    _require(payload.get("request_preflight_artifact_hash") == "e641189de6eb89991c5088c8f997b8af14a7e9b4aaa9a05537baa8096dac832f", "Judge request preflight drift")
    _require(payload.get("request_manifest_hash") == EXPECTED_REQUEST_MANIFEST_HASH, "Judge request manifest drift")
    _require(payload.get("judge_proposal_hash") == EXPECTED_PROPOSAL_HASH, "Judge proposal hash drift")
    _require(payload.get("structured_output_hash") == EXPECTED_PROPOSAL_HASH, "Judge structured output hash drift")
    proposal = payload.get("judge_proposal")
    _require(isinstance(proposal, Mapping), "Judge proposal missing")
    _require(canonical_sha256(proposal) == EXPECTED_PROPOSAL_HASH, "Judge proposal canonical hash drift")
    _require(proposal.get("outcome") == "WATCH", "Judge proposal outcome drift")
    _require(proposal.get("primary_candidate_id") == "META", "Judge proposal primary candidate drift")
    _require(proposal.get("research_reopen_required") is True, "Judge proposal lost reopen")
    _require(tuple(proposal.get("research_reopen_reason_codes") or ()) == EXPECTED_REOPEN_REASONS, "Judge proposal reopen reason drift")
    _require(proposal.get("next_directive") == "RESEARCH_REOPEN_REQUEST", "Judge proposal directive drift")
    _require(proposal.get("execution_authority") is False, "Judge proposal cannot grant execution authority")
    _require(payload.get("final_decision_created") is False, "FinalDecision already exists unexpectedly")
    _require(payload.get("b5_handoff_created") is False, "B5 handoff already exists unexpectedly")
    _require(payload.get("execution_authority") is False, "Judge result cannot grant execution authority")
    _require(payload.get("provider_reads") == 0, "Judge provider read count drift")
    _require(payload.get("broker_writes") == 0, "Judge broker write count drift")
    _require(payload.get("alpaca_orders") == 0, "Judge Alpaca order count drift")
    _require(payload.get("live_money") == "PROHIBITED", "live money must remain prohibited")
    return result_hash


def verify_paid_authorization(
    payload: Mapping[str, Any],
    *,
    expected_hash: str = EXPECTED_AUTH_HASH,
    expected_run_id: str = EXPECTED_RUN_ID,
) -> str:
    auth_hash = _self_hash(payload, field="artifact_hash")
    _require(auth_hash == expected_hash, "Judge authorization hash drift")
    _require(payload.get("artifact_version") == "B4_REOPEN_JUDGE_PRODUCTION_PAID_AUTHORIZATION_v0_2", "Judge authorization version drift")
    _require(payload.get("status") == "AUTHORIZED_FOR_ONE_B4_REOPEN_JUDGE_V02_RUN", "Judge authorization status drift")
    _require(payload.get("run_id") == expected_run_id, "Judge authorization run id drift")
    _require(payload.get("code_commit_sha") == EXPECTED_SOURCE_CODE_SHA, "Judge authorization code SHA drift")
    _require(payload.get("planned_paid_calls_max") == 1, "Judge authorization call ceiling drift")
    _require(payload.get("automatic_repair_calls_authorized") == 0, "Judge authorization repair drift")
    _require(payload.get("automatic_retries") == 0, "Judge authorization retry drift")
    _require(payload.get("rerun_authorized") is False, "Judge authorization rerun drift")
    _require(payload.get("judge_execution_authority") is False, "Judge authorization cannot grant execution authority")
    _require(payload.get("broker_writes") == 0, "Judge authorization broker write drift")
    _require(payload.get("alpaca_orders") == 0, "Judge authorization order drift")
    _require(payload.get("live_money") == "PROHIBITED", "live money must remain prohibited")
    return auth_hash


def verify_receipt_journal(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_attempt_hash: str = EXPECTED_ATTEMPT_HASH,
    expected_receipt_hash: str = EXPECTED_RECEIPT_HASH,
    expected_run_id: str = EXPECTED_RUN_ID,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    _require(len(rows) == 2, "Judge V02 journal must contain exactly attempt + result")
    attempt, receipt = rows
    attempt_hash = _self_hash(attempt, field="event_hash")
    receipt_hash = _self_hash(receipt, field="receipt_hash")
    _require(attempt_hash == expected_attempt_hash, "Judge dispatch attempt hash drift")
    _require(receipt_hash == expected_receipt_hash, "Judge dispatch receipt hash drift")
    _require(attempt.get("event_type") == "JUDGE_PROVIDER_DISPATCH_ATTEMPT", "Judge attempt event type drift")
    _require(attempt.get("run_id") == expected_run_id, "Judge attempt run id drift")
    _require(attempt.get("dispatch_index") == 1, "Judge attempt index drift")
    _require(attempt.get("authorization_consumed_by_this_attempt") is True, "Judge authority was not durably consumed")
    _require(attempt.get("automatic_retry") is False, "Judge attempt retry drift")
    _require(attempt.get("automatic_repair_attempted") is False, "Judge attempt repair drift")
    _require(attempt.get("judge_execution_authority") is False, "Judge attempt cannot grant execution authority")
    _require(receipt.get("event_type") == "JUDGE_PROVIDER_DISPATCH_RESULT", "Judge receipt event type drift")
    _require(receipt.get("run_id") == expected_run_id, "Judge receipt run id drift")
    _require(receipt.get("dispatch_index") == 1, "Judge receipt index drift")
    _require(receipt.get("dispatch_attempt_event_hash") == attempt_hash, "Judge receipt lost attempt lineage")
    _require(receipt.get("provider_response_received") is True, "Judge provider response missing")
    _require(receipt.get("provider_dispatch_state_unknown") is False, "Judge provider dispatch state unknown")
    _require(receipt.get("cost_receipt_status") == "COMPLETE", "Judge durable cost receipt incomplete")
    _require(_decimal(receipt.get("actual_cost_usd"), field="durable Judge cost") == EXPECTED_ACTUAL_COST_USD, "Judge durable cost drift")
    _require(receipt.get("validation_status") == "PASS", "Judge durable validation failed")
    _require(receipt.get("validation_error") is None, "Judge durable validation error present")
    _require(receipt.get("model_calls") == 1, "Judge durable model-call count drift")
    _require(receipt.get("automatic_retry") is False, "Judge receipt retry drift")
    _require(receipt.get("automatic_repair_attempted") is False, "Judge receipt repair drift")
    _require(receipt.get("judge_execution_authority") is False, "Judge receipt cannot grant execution authority")
    _require(receipt.get("request_hash") == EXPECTED_REQUEST_HASH, "Judge durable request hash drift")
    _require(receipt.get("request_manifest_hash") == EXPECTED_REQUEST_MANIFEST_HASH, "Judge durable request manifest drift")
    _require(receipt.get("judge_proposal_hash") == EXPECTED_PROPOSAL_HASH, "Judge durable proposal hash drift")
    structured = receipt.get("structured_output")
    _require(isinstance(structured, Mapping), "Judge durable structured output missing")
    _require(canonical_sha256(structured) == EXPECTED_PROPOSAL_HASH, "Judge durable structured output hash drift")
    _require(receipt.get("broker_writes") == 0, "Judge receipt broker write drift")
    _require(receipt.get("alpaca_orders") == 0, "Judge receipt order drift")
    _require(receipt.get("live_money") == "PROHIBITED", "live money must remain prohibited")
    return attempt, receipt


def build_research_reopen_request(
    result: Mapping[str, Any],
    receipt: Mapping[str, Any],
):
    run_id = result.get("run_id")
    _require(isinstance(run_id, str) and bool(run_id), "Judge run id missing")
    reason_codes = result.get("research_reopen_reason_codes")
    _require(isinstance(reason_codes, list) and bool(reason_codes), "Judge reopen reasons missing")
    _require(tuple(reason_codes) == EXPECTED_REOPEN_REASONS, "Judge reopen reasons drift")
    requested_at = receipt.get("dispatch_finished_at_utc")
    _require(isinstance(requested_at, str) and bool(requested_at), "Judge completion timestamp missing")
    reopen = RESEARCH_REOPEN_REQUEST_V1.from_unhashed(
        reopen_request_id=REOPEN_REQUEST_ID,
        parent_run_id=run_id,
        parent_decision_id=None,
        trigger_bundle_id=None,
        reason_codes=list(reason_codes),
        source_ref_ids=list(reason_codes),
        requested_at=requested_at,
        new_run_start_state="S00",
    )
    payload = reopen.model_dump(mode="json", exclude_none=False, warnings=False)
    _require(payload["parent_decision_id"] is None, "reopen request must not invent FinalDecision lineage")
    _require(payload["trigger_bundle_id"] is None, "reopen request must not invent trigger bundle lineage")
    _require(payload["new_run_start_state"] == "S00", "reopen request must start linked run at S00")
    _require(payload["request_hash"] == canonical_sha256(payload, exclude_fields=("request_hash",)), "canonical reopen request hash mismatch")
    return reopen


def build_postprocess_artifact(
    *,
    result: Mapping[str, Any],
    authorization: Mapping[str, Any],
    attempt: Mapping[str, Any],
    receipt: Mapping[str, Any],
    reopen_request: Any,
    code_commit_sha: str,
) -> dict[str, Any]:
    _require(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None, "postprocess code SHA must be lowercase git SHA")
    reopen_payload = reopen_request.model_dump(mode="json", exclude_none=False, warnings=False)
    artifact: dict[str, Any] = {
        "artifact_version": POSTPROCESS_VERSION,
        "status": POSTPROCESS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_judge_code_commit_sha": result["code_commit_sha"],
        "source_judge_result_artifact_hash": result["artifact_hash"],
        "source_judge_run_id": result["run_id"],
        "source_paid_authorization_artifact_hash": authorization["artifact_hash"],
        "source_dispatch_attempt_event_hash": attempt["event_hash"],
        "source_paid_call_receipt_hash": receipt["receipt_hash"],
        "source_judge_proposal_hash": result["judge_proposal_hash"],
        "source_outcome": "WATCH",
        "source_primary_candidate_id": "META",
        "source_watch_candidate_ids": ["META", "NVDA", "MSFT"],
        "source_research_reopen_required": True,
        "source_research_reopen_reason_codes": list(EXPECTED_REOPEN_REASONS),
        "source_judge_model_calls": 1,
        "source_judge_actual_cost_usd": "0.1525175",
        "research_reopen_request_count": 1,
        "research_reopen_request_hash": reopen_payload["request_hash"],
        "research_reopen_request": reopen_payload,
        "new_run_start_state": "S00",
        "research_run_started": False,
        "next_directive": "RESEARCH_REOPEN_REQUEST",
        "final_decision_created": False,
        "final_decision_allowed": False,
        "b5_handoff_created": False,
        "b5_handoff_allowed": False,
        "execution_authority": False,
        "paid_model_calls_authorized": False,
        "provider_reads_authorized": False,
        "next_gate": NEXT_GATE,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_postprocess_artifact(payload: Mapping[str, Any]) -> str:
    artifact_hash = _self_hash(payload, field="artifact_hash")
    exact = {
        "artifact_version": POSTPROCESS_VERSION,
        "status": POSTPROCESS_STATUS,
        "source_judge_code_commit_sha": EXPECTED_SOURCE_CODE_SHA,
        "source_judge_result_artifact_hash": EXPECTED_RESULT_HASH,
        "source_judge_run_id": EXPECTED_RUN_ID,
        "source_paid_authorization_artifact_hash": EXPECTED_AUTH_HASH,
        "source_dispatch_attempt_event_hash": EXPECTED_ATTEMPT_HASH,
        "source_paid_call_receipt_hash": EXPECTED_RECEIPT_HASH,
        "source_judge_proposal_hash": EXPECTED_PROPOSAL_HASH,
        "source_outcome": "WATCH",
        "source_primary_candidate_id": "META",
        "source_watch_candidate_ids": ["META", "NVDA", "MSFT"],
        "source_research_reopen_required": True,
        "source_research_reopen_reason_codes": list(EXPECTED_REOPEN_REASONS),
        "source_judge_model_calls": 1,
        "source_judge_actual_cost_usd": "0.1525175",
        "research_reopen_request_count": 1,
        "new_run_start_state": "S00",
        "research_run_started": False,
        "next_directive": "RESEARCH_REOPEN_REQUEST",
        "final_decision_created": False,
        "final_decision_allowed": False,
        "b5_handoff_created": False,
        "b5_handoff_allowed": False,
        "execution_authority": False,
        "paid_model_calls_authorized": False,
        "provider_reads_authorized": False,
        "next_gate": NEXT_GATE,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    for key, expected in exact.items():
        _require(payload.get(key) == expected, f"postprocess artifact drift: {key}")
    raw_reopen = payload.get("research_reopen_request")
    _require(isinstance(raw_reopen, Mapping), "postprocess canonical reopen request missing")
    reopen = RESEARCH_REOPEN_REQUEST_V1.model_validate(dict(raw_reopen))
    _require(payload.get("research_reopen_request_hash") == reopen.request_hash, "postprocess reopen hash lineage drift")
    _require(reopen.request_hash == canonical_sha256(raw_reopen, exclude_fields=("request_hash",)), "postprocess reopen request self-hash drift")
    _require(reopen.parent_run_id == EXPECTED_RUN_ID, "postprocess reopen parent run drift")
    _require(reopen.parent_decision_id is None, "postprocess must not invent FinalDecision")
    _require(reopen.trigger_bundle_id is None, "postprocess must not invent trigger bundle")
    _require(tuple(reopen.reason_codes) == EXPECTED_REOPEN_REASONS, "postprocess reopen reasons drift")
    _require(tuple(reopen.source_ref_ids) == EXPECTED_REOPEN_REASONS, "postprocess reopen source refs drift")
    _require(reopen.new_run_start_state == "S00", "postprocess reopen start-state drift")
    return artifact_hash
