"""Zero-call forensic recovery gate for the first paid post-research Initial attempt."""
from __future__ import annotations

from decimal import Decimal
import hashlib
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from . import post_research_reopen_initial_production_dispatch_v01 as dispatch


ARTIFACT_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_PAID_FAILURE_RECOVERY_PREFLIGHT_v0_1"
PASS_STATUS = "B4_POST_RESEARCH_REOPEN_INITIAL_PAID_FAILURE_RECOVERY_ZERO_CALL_PASS"
EXPECTED_LEDGER_FILE_SHA256 = "1251ee78a762b673eea167f3c4add16c96989ef8e9ac48c0b60891322f8c8fd6"
EXPECTED_LEDGER_HASH = "f0d40bf9b85ccb8558cb73bd7e3b7973c718df7f7f6d94f99b512a925872ec11"
EXPECTED_OWNER_APPROVAL_HASH = "2caf267c794bfd36dab0e7c52e30d34d4690a5172eeeb5591c467336b68b22ff"
EXPECTED_OLD_EXECUTOR_HEAD = "9effc72995f4c6a85f7c93c69c48657506c18894"
EXPECTED_OLD_READINESS_HASH = "fc1529e49739d8a8801eda73ca386717a3529eea2ae7a4d738f0d8c847894691"
TERMINAL_ERROR = "binary float is forbidden in canonical payloads"
RECOVERY_CLASSIFICATION = "PROVIDER_RESPONSE_RETURNED_CAPTURE_FAILED_LOCAL_SERIALIZATION"


class PaidFailureRecoveryPreflightError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise PaidFailureRecoveryPreflightError(message)


def _self_hash(payload: Mapping[str, Any]) -> str:
    observed = payload.get("artifact_hash")
    _need(isinstance(observed, str) and observed == canonical_sha256(payload, exclude_fields=("artifact_hash",)), "artifact self-hash mismatch")
    return observed


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(cost_preflight: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    request_hashes = dispatch.verify_cost_preflight_for_dispatch(cost_preflight)
    rows = cost_preflight.get("initial_requests")
    _need(isinstance(rows, list) and len(rows) == 9, "frozen request rows missing")
    result: list[Mapping[str, Any]] = []
    for row, request_hash in zip(rows, request_hashes, strict=True):
        _need(isinstance(row, Mapping) and row.get("request_hash") == request_hash, "frozen request lineage drift")
        _need(isinstance(row.get("estimated_max_cost_usd"), str), "frozen request cost missing")
        result.append(row)
    return result


def _verify_ledger(ledger: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> None:
    _need(ledger.get("ledger_version") == "B4_POST_RESEARCH_REOPEN_INITIAL_PAID_DISPATCH_LEDGER_v0_1", "ledger version drift")
    _need(ledger.get("ledger_hash") == EXPECTED_LEDGER_HASH, "ledger hash drift")
    _need(ledger.get("ledger_hash") == canonical_sha256(ledger, exclude_fields=("ledger_hash",)), "ledger self-hash mismatch")
    _need(ledger.get("owner_approval_hash") == EXPECTED_OWNER_APPROVAL_HASH, "ledger owner approval drift")
    entries = ledger.get("entries")
    _need(isinstance(entries, list) and len(entries) == 9, "ledger entries drift")
    for index, (entry, row) in enumerate(zip(entries, rows, strict=True), start=1):
        _need(isinstance(entry, Mapping) and entry.get("dispatch_index") == index, "ledger index drift")
        _need(entry.get("request_hash") == row.get("request_hash"), "ledger request hash drift")
        expected_state = dispatch.DISPATCH_STARTED_UNKNOWN if index == 1 else dispatch.NOT_DISPATCHED
        _need(entry.get("state") == expected_state, "ledger state drift")
    first = entries[0]
    _need(first.get("raw_response_hash") is None and first.get("raw_response_path") is None and first.get("actual_cost_usd") is None, "ledger first-entry recovery facts drift")


def build_failure_recovery_preflight(*, ledger: Mapping[str, Any], ledger_file_sha256: str, cost_preflight: Mapping[str, Any], raw_response_dir_exists: bool, raw_response_file_count: int, fresh_result_exists: bool) -> dict[str, Any]:
    rows = _rows(cost_preflight)
    _need(ledger_file_sha256 == EXPECTED_LEDGER_FILE_SHA256, "ledger file SHA-256 drift")
    _verify_ledger(ledger, rows)
    _need(raw_response_dir_exists is False and raw_response_file_count == 0, "raw response recovery facts drift")
    _need(fresh_result_exists is False, "fresh result must be absent")
    first_cost = Decimal(str(rows[0]["estimated_max_cost_usd"]))
    remaining_cost = sum((Decimal(str(row["estimated_max_cost_usd"])) for row in rows[1:]), Decimal("0"))
    _need(first_cost + remaining_cost == Decimal(dispatch.EXPECTED_MAX_COST_USD), "frozen cost sum drift")
    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION, "status": PASS_STATUS,
        "existing_ledger_file_sha256": ledger_file_sha256, "existing_ledger_hash": EXPECTED_LEDGER_HASH,
        "owner_approval_hash": EXPECTED_OWNER_APPROVAL_HASH, "old_executor_code_commit_sha": EXPECTED_OLD_EXECUTOR_HEAD,
        "old_readiness_artifact_hash": EXPECTED_OLD_READINESS_HASH, "source_cost_preflight_hash": dispatch.EXPECTED_PREFLIGHT_HASH,
        "terminal_error": TERMINAL_ERROR, "recovery_classification": RECOVERY_CLASSIFICATION,
        "raw_response_directory_exists": False, "raw_response_file_count": 0, "fresh_initial_result_exists": False,
        "request_1": {
            "request_hash": rows[0]["request_hash"], "original_ledger_state": dispatch.DISPATCH_STARTED_UNKNOWN,
            "recovery_state": RECOVERY_CLASSIFICATION, "provider_acceptance": "CONFIRMED_BY_CONTROL_FLOW",
            "provider_response_returned": True, "provider_response_id": "UNKNOWN_LOCALLY", "response_content_recoverable_locally": False,
            "automatic_resend_permitted": False, "estimated_max_cost_usd": str(first_cost), "actual_cost_usd": "UNKNOWN",
            "conservative_paid_exposure_max_usd": str(first_cost),
        },
        "requests_2_to_9": {"request_hashes": [row["request_hash"] for row in rows[1:]], "state": dispatch.NOT_DISPATCHED, "estimated_max_cost_usd": str(remaining_cost)},
        "original_total_max_cost_usd": dispatch.EXPECTED_MAX_COST_USD,
        "recovery_options": {
            "option_a_abort_current_initial": {"model_calls_authorized": False, "additional_provider_calls": 0, "fresh_initial_result_created": False},
            "option_b_full_initial_recovery": {"replacement_request_1_required": True, "original_not_dispatched_requests_2_to_9": 8, "additional_provider_calls_required": 9, "total_provider_call_lineage": 10, "existing_call_count_ceiling": 9, "existing_approval_valid": False, "authorization_status": "OWNER_EXCEPTION_REQUIRED", "automatic_retry": False},
        },
        "store_false_no_normal_saved_response_retrieval": True, "speculative_provider_read_authorized": False,
        "model_calls_this_step": 0, "provider_reads_this_step": 0, "broker_writes": 0, "alpaca_orders": 0, "cost_usd_this_step": "0", "live_money": "PROHIBITED",
        "next_gate": "OWNER_GOVERNANCE_DECISION_ABORT_OR_NEW_EXACT_RECOVERY_PREFLIGHT",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    return artifact


def verify_failure_recovery_preflight(payload: Mapping[str, Any], *, ledger: Mapping[str, Any], ledger_file_sha256: str, cost_preflight: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    expected = build_failure_recovery_preflight(ledger=ledger, ledger_file_sha256=ledger_file_sha256, cost_preflight=cost_preflight, raw_response_dir_exists=False, raw_response_file_count=0, fresh_result_exists=False)
    _need(dict(payload) == expected, "recovery preflight drift")
    return observed
