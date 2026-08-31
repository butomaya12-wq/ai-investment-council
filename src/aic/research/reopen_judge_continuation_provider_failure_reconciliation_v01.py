from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256


VERSION = "B3_RESEARCH_REOPEN_CONTINUATION_PROVIDER_FAILURE_RECONCILIATION_v0_1"
PASS_STATUS = "B3_RESEARCH_REOPEN_CONTINUATION_PROVIDER_FAILURE_RECONCILIATION_ZERO_CALL_PASS"
NEXT_GATE = "B3_RESEARCH_REOPEN_CONTINUATION_WIRE_REPAIR_RUNNER_DRY_ZERO_CALL"

EXPECTED_AUTH_HASH = "fc09d7598f336e09c70f4afc541b666aa54d826e3c31d378dffd114d2c0572b3"
EXPECTED_RESULT_HASH = "ea8a28425b9b628f1441e89af89122e7b1337f6ba889b4965177078fa6835df3"
EXPECTED_RUNTIME_CODE_SHA = "742b702db3332d047b32870fa0f3f535f4c70772"
EXPECTED_CONTINUATION_PREFLIGHT_HASH = "d50605627567787317c90ac56fb16e4fea1f4b5a3326439383296a4ec6e96fe4"
EXPECTED_CONTINUATION_MANIFEST_HASH = "7be13f17d4ab17c86adae8e170fcf1578a09cc3239c26228f822a5f3008525aa"
EXPECTED_DRY_HASH = "da497cd1c999c4d7eab15223c7bd297963e42d120c34bbc5e1bf08cdc1f32d48"
EXPECTED_FIRST_DISPATCH_HASH = "9f8833f4e733fda7af038c1517461b05ee4dbf8ec5d301ea6f3199a5059b205a"
EXPECTED_LAST_DISPATCH_HASH = "3e40ae5ca5a0b38c773bfd9b11b0157c2a1275bd8dfb39ee2c54c305c8a34fc7"
EXPECTED_FAILURE_REASON = "next_page_token must be non-empty"


class ContinuationProviderFailureReconciliationError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ContinuationProviderFailureReconciliationError(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, f"{field} missing")
    _need(observed == canonical_sha256(payload, exclude_fields=(field,)), f"{field} self-hash mismatch")
    return observed


def _event_hash(payload: Mapping[str, Any]) -> str:
    observed = payload.get("event_hash")
    _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, "journal event_hash missing")
    _need(observed == canonical_sha256(payload, exclude_fields=("event_hash",)), "journal event self-hash mismatch")
    return observed


def read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuationProviderFailureReconciliationError(f"unable to read {label}") from exc
    _need(isinstance(payload, dict), f"{label} root must be object")
    return payload


def read_journal(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ContinuationProviderFailureReconciliationError("unable to read continuation journal") from exc
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContinuationProviderFailureReconciliationError("continuation journal contains invalid JSON") from exc
        _need(isinstance(row, dict), "continuation journal row must be object")
        _event_hash(row)
        rows.append(row)
    _need(bool(rows), "continuation journal is empty")
    return rows


def build_reconciliation(
    *,
    authorization: Mapping[str, Any],
    result: Mapping[str, Any],
    journal_rows: list[Mapping[str, Any]],
    code_commit_sha: str,
) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha or "") is not None, "exact code SHA required")

    auth_hash = _self_hash(authorization)
    _need(auth_hash == EXPECTED_AUTH_HASH, "continuation authorization hash drift")
    _need(authorization.get("code_commit_sha") == EXPECTED_RUNTIME_CODE_SHA, "continuation runtime SHA drift")
    _need(authorization.get("source_runner_dry_hash") == EXPECTED_DRY_HASH, "continuation dry lineage drift")
    _need(authorization.get("source_continuation_preflight_hash") == EXPECTED_CONTINUATION_PREFLIGHT_HASH, "continuation preflight lineage drift")
    _need(authorization.get("continuation_request_manifest_hash") == EXPECTED_CONTINUATION_MANIFEST_HASH, "continuation manifest lineage drift")
    _need(authorization.get("provider_dispatch_attempts_max") == 11, "continuation authorization ceiling drift")
    _need(authorization.get("model_calls_authorized") is False, "continuation authorization unexpectedly allows model calls")

    result_hash = _self_hash(result)
    _need(result_hash == EXPECTED_RESULT_HASH, "continuation result hash drift")
    _need(result.get("status") == "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_BLOCKED", "continuation result status drift")
    _need(result.get("authorization_artifact_hash") == auth_hash, "continuation result authorization drift")
    _need(result.get("failed_bundle_id") == "CR1_MSFT_NEWS_REFRESH", "unexpected failed bundle")
    _need(result.get("failure_reason") == EXPECTED_FAILURE_REASON, "unexpected continuation failure reason")
    _need(result.get("provider_dispatch_attempts") == 2, "continuation dispatch count drift")
    _need(result.get("provider_dispatch_attempts_max") == 11, "continuation result ceiling drift")
    _need(result.get("bundle_results") == [], "failed continuation unexpectedly retained normalized bundle output")
    _need(result.get("model_calls") == 0 and result.get("model_synthesis_calls") == 0, "model call drift")
    _need(result.get("broker_writes") == 0 and result.get("alpaca_orders") == 0, "execution drift")
    _need(result.get("live_money") == "PROHIBITED", "live-money invariant drift")

    attempts = [row for row in journal_rows if row.get("event_type") == "PROVIDER_DISPATCH_ATTEMPT"]
    receipts = [row for row in journal_rows if row.get("event_type") == "PROVIDER_RESPONSE_RECEIPT"]
    bindings = [row for row in journal_rows if row.get("event_type") == "DYNAMIC_REQUEST_BINDING"]
    failures = [row for row in journal_rows if row.get("event_type") == "BUNDLE_FAILURE"]

    _need(len(attempts) == 2, "expected exactly two durable provider dispatch attempts")
    _need(len(receipts) == 2, "expected exactly two durable provider response receipts")
    _need(len(bindings) == 0, "dynamic binding occurred before CR1 failure")
    _need(len(failures) == 1, "expected one durable bundle failure event")
    _need(all(row.get("bundle_id") == "CR1_MSFT_NEWS_REFRESH" for row in attempts + receipts + failures), "journal bundle drift")
    _need([row.get("dispatch_index_within_bundle") for row in attempts] == [1, 2], "dispatch index drift")
    _need([row.get("dispatch_index_within_bundle") for row in receipts] == [1, 2], "response index drift")
    _need(attempts[0].get("event_hash") == EXPECTED_FIRST_DISPATCH_HASH, "first dispatch hash drift")
    _need(attempts[-1].get("event_hash") == EXPECTED_LAST_DISPATCH_HASH, "last dispatch hash drift")
    _need(failures[0].get("reason") == EXPECTED_FAILURE_REASON, "durable failure reason drift")

    receipt_payload_fields = {"response_payload", "raw_response", "raw_response_b64", "raw_snapshot_path", "raw_snapshot_sha256"}
    _need(all(not receipt_payload_fields.intersection(row.keys()) for row in receipts), "unexpected raw response persistence shape")
    _need(all(isinstance(row.get("response_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", row["response_sha256"]) for row in receipts), "response receipt SHA drift")
    _need(all(isinstance(row.get("response_bytes"), int) and row["response_bytes"] > 0 for row in receipts), "response byte count drift")

    artifact = {
        "artifact_version": VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_authorization_artifact_hash": auth_hash,
        "source_result_artifact_hash": result_hash,
        "source_runtime_code_commit_sha": EXPECTED_RUNTIME_CODE_SHA,
        "source_continuation_preflight_hash": EXPECTED_CONTINUATION_PREFLIGHT_HASH,
        "source_continuation_request_manifest_hash": EXPECTED_CONTINUATION_MANIFEST_HASH,
        "source_runner_dry_hash": EXPECTED_DRY_HASH,
        "authority_consumed": True,
        "authority_reusable": False,
        "production_rerun_allowed": False,
        "provider_dispatch_attempts_observed": 2,
        "provider_response_receipts_observed": 2,
        "first_dispatch_event_hash": EXPECTED_FIRST_DISPATCH_HASH,
        "last_dispatch_event_hash": EXPECTED_LAST_DISPATCH_HASH,
        "failed_bundle_id": "CR1_MSFT_NEWS_REFRESH",
        "failure_reason": EXPECTED_FAILURE_REASON,
        "failure_class": "ALPACA_CLI_EMPTY_STRING_TERMINAL_PAGE_TOKEN_NOT_NORMALIZED",
        "deterministic_failure_inference": "EXACT_NEXT_PAGE_TOKEN_NON_EMPTY_ERROR_AFTER_SECOND_SUCCESSFUL_RESPONSE_IMPLIES_EMPTY_STRING_TERMINAL_TOKEN",
        "wire_repair_required": True,
        "wire_repair_rule": "NORMALIZE_NEXT_PAGE_TOKEN_EMPTY_STRING_TO_NULL_TERMINAL_BEFORE_CANONICAL_PAGINATION_STATE",
        "provider_response_payloads_durably_retained": False,
        "provider_response_receipts_durably_retained": True,
        "msft_normalized_evidence_recoverable_without_provider_reread": False,
        "msft_must_reread_from_frozen_window_start": True,
        "future_raw_response_persistence_required_before_parse": True,
        "resolved_target_count_this_step": 0,
        "provider_reads_this_step": 0,
        "provider_reads_authorized_this_step": False,
        "model_calls_this_step": 0,
        "model_calls_authorized_this_step": False,
        "model_synthesis_calls_this_step": 0,
        "automatic_retries": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
