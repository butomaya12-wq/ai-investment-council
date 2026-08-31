from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.data.providers.alpaca_cli_news import CLI_PROFILE_CREDENTIAL_PLACEHOLDER
from aic.data.providers.alpaca_news import AlpacaNewsReadError
from aic.data.providers.alpaca_news_reopen import AlpacaNewsReopenRead, ReopenAlpacaCliNewsTransport, read_alpaca_news_window_for_reopen
from aic.data.providers.alpaca_news_reopen_continuation import read_alpaca_news_continuation_from_saved_token
from aic.domain.canonical import canonical_sha256

from . import reopen_judge_residual_external_read_continuation_runtime_v01 as v01


AUTH_VERSION = "B3_RESEARCH_REOPEN_CONTINUATION_WIRE_REPAIR_AUTHORIZATION_v0_2"
DRY_VERSION = "B3_RESEARCH_REOPEN_CONTINUATION_WIRE_REPAIR_RUNNER_DRY_v0_2"
RESULT_VERSION = "B3_RESEARCH_REOPEN_CONTINUATION_WIRE_REPAIR_RESULT_v0_2"
JOURNAL_EVENT_VERSION = "B3_RESEARCH_REOPEN_CONTINUATION_WIRE_REPAIR_JOURNAL_EVENT_v0_2"
READY_STATUS = "READY_FOR_EXPLICIT_OWNER_B3_RESEARCH_REOPEN_CONTINUATION_WIRE_REPAIR_AUTHORIZATION_V02"
SUCCESS_STATUS = "B3_RESEARCH_REOPEN_CONTINUATION_WIRE_REPAIR_PASS_FROZEN"
BLOCKED_STATUS = "B3_RESEARCH_REOPEN_CONTINUATION_WIRE_REPAIR_BLOCKED"
NEXT_GATE_SUCCESS = "B3_RESEARCH_REOPEN_POST_CONTINUATION_READ_EVIDENCE_RECONCILIATION_ZERO_CALL"
NEXT_GATE_FAILURE = "ZERO_CALL_DURABLE_CONTINUATION_WIRE_REPAIR_PROVIDER_READ_FAILURE_RECONCILIATION"
OWNER_APPROVAL_ID = "OWNER-B3-RESEARCH-REOPEN-CONTINUATION-WIRE-REPAIR-V02"

EXPECTED_FAILURE_RECONCILIATION_HASH = "1b4fcc0ce1ed27dcbf422095fdede67153c63861b18211962958d3ecf6d199b4"
EXPECTED_CONSUMED_AUTHORIZATION_HASH = "fc09d7598f336e09c70f4afc541b666aa54d826e3c31d378dffd114d2c0572b3"
EXPECTED_CONSUMED_RESULT_HASH = "ea8a28425b9b628f1441e89af89122e7b1337f6ba889b4965177078fa6835df3"
EXPECTED_FAILURE_RECONCILIATION_CODE_SHA = "194f9a21ecd71d6c227c10bff0f19fa694de3530"
WIRE_REPAIR_RULE = "NORMALIZE_NEXT_PAGE_TOKEN_EMPTY_STRING_TO_NULL_TERMINAL_BEFORE_CANONICAL_PAGINATION_STATE"
RAW_PERSISTENCE_RULE = "DURABLE_RAW_RESPONSE_SNAPSHOT_BEFORE_PARSE_OR_VALIDATION"

MAX_DISPATCH_ATTEMPTS = v01.MAX_DISPATCH_ATTEMPTS
PROFILE = v01.PROFILE
BUNDLE_IDS = v01.BUNDLE_IDS
EXPECTED_REOPEN_CUTOFF_UTC = v01.EXPECTED_REOPEN_CUTOFF_UTC
EXPECTED_CONTINUATION_PREFLIGHT_HASH = v01.EXPECTED_CONTINUATION_PREFLIGHT_HASH
EXPECTED_CONTINUATION_MANIFEST_HASH = v01.EXPECTED_CONTINUATION_MANIFEST_HASH
EXPECTED_ORIGINAL_PREFLIGHT_HASH = v01.EXPECTED_ORIGINAL_PREFLIGHT_HASH
EXPECTED_ORIGINAL_MANIFEST_HASH = v01.EXPECTED_ORIGINAL_MANIFEST_HASH
EXPECTED_ORIGINAL_RESULT_HASH = v01.EXPECTED_ORIGINAL_RESULT_HASH
EXPECTED_RETAINED_NVDA_EVIDENCE_HASH = v01.EXPECTED_RETAINED_NVDA_EVIDENCE_HASH
EXPECTED_NVDA_START_TOKEN = v01.EXPECTED_NVDA_START_TOKEN
EXPECTED_NVDA_START_TOKEN_HASH = v01.EXPECTED_NVDA_START_TOKEN_HASH
EXPECTED_TEMPLATE_HASHES = v01.EXPECTED_TEMPLATE_HASHES


class ContinuationWireRepairRuntimeError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ContinuationWireRepairRuntimeError(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, f"{field} missing")
    _need(observed == canonical_sha256(payload, exclude_fields=(field,)), f"{field} self-hash mismatch")
    return observed


def verify_failure_reconciliation(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_FAILURE_RECONCILIATION_HASH, "failure reconciliation hash drift")
    exact = {
        "status": "B3_RESEARCH_REOPEN_CONTINUATION_PROVIDER_FAILURE_RECONCILIATION_ZERO_CALL_PASS",
        "code_commit_sha": EXPECTED_FAILURE_RECONCILIATION_CODE_SHA,
        "source_authorization_artifact_hash": EXPECTED_CONSUMED_AUTHORIZATION_HASH,
        "source_result_artifact_hash": EXPECTED_CONSUMED_RESULT_HASH,
        "authority_consumed": True,
        "authority_reusable": False,
        "production_rerun_allowed": False,
        "provider_dispatch_attempts_observed": 2,
        "provider_response_receipts_observed": 2,
        "failed_bundle_id": "CR1_MSFT_NEWS_REFRESH",
        "failure_reason": "next_page_token must be non-empty",
        "failure_class": "ALPACA_CLI_EMPTY_STRING_TERMINAL_PAGE_TOKEN_NOT_NORMALIZED",
        "wire_repair_required": True,
        "wire_repair_rule": WIRE_REPAIR_RULE,
        "provider_response_payloads_durably_retained": False,
        "provider_response_receipts_durably_retained": True,
        "msft_normalized_evidence_recoverable_without_provider_reread": False,
        "msft_must_reread_from_frozen_window_start": True,
        "future_raw_response_persistence_required_before_parse": True,
        "provider_reads_this_step": 0,
        "model_calls_this_step": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": "B3_RESEARCH_REOPEN_CONTINUATION_WIRE_REPAIR_RUNNER_DRY_ZERO_CALL",
    }
    for key, expected in exact.items():
        _need(payload.get(key) == expected, f"failure reconciliation drift: {key}")
    return observed


def verify_continuation_preflight(payload: Mapping[str, Any]) -> str:
    try:
        return v01.verify_continuation_preflight(payload)
    except Exception as exc:
        raise ContinuationWireRepairRuntimeError(str(exc)) from exc


def verify_original_preflight(payload: Mapping[str, Any]) -> str:
    try:
        return v01.verify_original_preflight(payload)
    except Exception as exc:
        raise ContinuationWireRepairRuntimeError(str(exc)) from exc


def verify_original_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return v01.verify_original_result(payload)
    except Exception as exc:
        raise ContinuationWireRepairRuntimeError(str(exc)) from exc


def build_dry(
    *,
    failure_reconciliation: Mapping[str, Any],
    continuation_preflight: Mapping[str, Any],
    original_preflight: Mapping[str, Any],
    original_result: Mapping[str, Any],
    code_commit_sha: str,
) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha or "") is not None, "exact code SHA required")
    failure_hash = verify_failure_reconciliation(failure_reconciliation)
    continuation_hash = verify_continuation_preflight(continuation_preflight)
    original_preflight_hash = verify_original_preflight(original_preflight)
    original_result_summary = verify_original_result(original_result)
    artifact = {
        "artifact_version": DRY_VERSION,
        "status": READY_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_failure_reconciliation_hash": failure_hash,
        "source_consumed_authorization_hash": EXPECTED_CONSUMED_AUTHORIZATION_HASH,
        "source_consumed_result_hash": EXPECTED_CONSUMED_RESULT_HASH,
        "source_continuation_preflight_hash": continuation_hash,
        "continuation_request_manifest_hash": EXPECTED_CONTINUATION_MANIFEST_HASH,
        "source_original_preflight_hash": original_preflight_hash,
        "source_original_request_manifest_hash": EXPECTED_ORIGINAL_MANIFEST_HASH,
        "source_original_result_hash": original_result_summary["result_artifact_hash"],
        "retained_nvda_evidence_hash": original_result_summary["retained_response_hash"],
        "nvda_start_page_token_hash": EXPECTED_NVDA_START_TOKEN_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "request_template_hashes": list(EXPECTED_TEMPLATE_HASHES),
        "logical_provider_read_bundle_ids": list(BUNDLE_IDS),
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "msft_reread_from_frozen_window_start": True,
        "wire_repair_rule": WIRE_REPAIR_RULE,
        "empty_terminal_token_normalization_surfaces": ["NEWS", "MULTI_BARS"],
        "raw_response_persistence_rule": RAW_PERSISTENCE_RULE,
        "raw_response_snapshot_before_parse": True,
        "raw_snapshot_event_before_response_receipt": True,
        "raw_snapshot_sha256_bound": True,
        "raw_snapshot_exclusive_create": True,
        "single_authorized_wire_repair_read_pass_only": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "pagination_incomplete_continue_policy": "RETAIN_PARTIAL_AND_CONTINUE_TO_LATER_BUNDLES",
        "transport_or_validation_error_policy": "STOP_IMMEDIATELY_WITH_RAW_RESPONSE_RETAINED_IF_PROVIDER_RETURNED_BYTES",
        "nvda_replay_retained_pages_allowed": False,
        "nvda_continuation_start_token_required": True,
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "conditional_followup_reads_authorized": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": "EXPLICIT_OWNER_B3_RESEARCH_REOPEN_CONTINUATION_WIRE_REPAIR_AUTHORIZATION_V02",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_dry(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload)
    exact = {
        "artifact_version": DRY_VERSION,
        "status": READY_STATUS,
        "code_commit_sha": expected_code_commit_sha,
        "source_failure_reconciliation_hash": EXPECTED_FAILURE_RECONCILIATION_HASH,
        "source_consumed_authorization_hash": EXPECTED_CONSUMED_AUTHORIZATION_HASH,
        "source_consumed_result_hash": EXPECTED_CONSUMED_RESULT_HASH,
        "source_continuation_preflight_hash": EXPECTED_CONTINUATION_PREFLIGHT_HASH,
        "continuation_request_manifest_hash": EXPECTED_CONTINUATION_MANIFEST_HASH,
        "source_original_preflight_hash": EXPECTED_ORIGINAL_PREFLIGHT_HASH,
        "source_original_request_manifest_hash": EXPECTED_ORIGINAL_MANIFEST_HASH,
        "source_original_result_hash": EXPECTED_ORIGINAL_RESULT_HASH,
        "retained_nvda_evidence_hash": EXPECTED_RETAINED_NVDA_EVIDENCE_HASH,
        "nvda_start_page_token_hash": EXPECTED_NVDA_START_TOKEN_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "request_template_hashes": list(EXPECTED_TEMPLATE_HASHES),
        "logical_provider_read_bundle_ids": list(BUNDLE_IDS),
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "msft_reread_from_frozen_window_start": True,
        "wire_repair_rule": WIRE_REPAIR_RULE,
        "empty_terminal_token_normalization_surfaces": ["NEWS", "MULTI_BARS"],
        "raw_response_persistence_rule": RAW_PERSISTENCE_RULE,
        "raw_response_snapshot_before_parse": True,
        "raw_snapshot_event_before_response_receipt": True,
        "raw_snapshot_sha256_bound": True,
        "raw_snapshot_exclusive_create": True,
        "single_authorized_wire_repair_read_pass_only": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "pagination_incomplete_continue_policy": "RETAIN_PARTIAL_AND_CONTINUE_TO_LATER_BUNDLES",
        "transport_or_validation_error_policy": "STOP_IMMEDIATELY_WITH_RAW_RESPONSE_RETAINED_IF_PROVIDER_RETURNED_BYTES",
        "nvda_replay_retained_pages_allowed": False,
        "nvda_continuation_start_token_required": True,
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "conditional_followup_reads_authorized": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": "EXPLICIT_OWNER_B3_RESEARCH_REOPEN_CONTINUATION_WIRE_REPAIR_AUTHORIZATION_V02",
    }
    for key, expected in exact.items():
        _need(payload.get(key) == expected, f"wire-repair dry drift: {key}")
    return observed


def build_authorization(
    *,
    dry: Mapping[str, Any],
    owner_approval_id: str,
    owner_approval_at_utc: str,
    code_commit_sha: str,
) -> dict[str, Any]:
    dry_hash = verify_dry(dry, expected_code_commit_sha=code_commit_sha)
    _need(owner_approval_id == OWNER_APPROVAL_ID, "owner approval id drift")
    try:
        v01._parse_utc(owner_approval_at_utc)
    except Exception as exc:
        raise ContinuationWireRepairRuntimeError(str(exc)) from exc
    artifact = {
        "artifact_version": AUTH_VERSION,
        "status": "AUTHORIZED_EXACTLY_ONE_BOUNDED_CONTINUATION_WIRE_REPAIR_PROVIDER_READ_PASS",
        "code_commit_sha": code_commit_sha,
        "source_runner_dry_hash": dry_hash,
        "source_failure_reconciliation_hash": EXPECTED_FAILURE_RECONCILIATION_HASH,
        "source_continuation_preflight_hash": EXPECTED_CONTINUATION_PREFLIGHT_HASH,
        "continuation_request_manifest_hash": EXPECTED_CONTINUATION_MANIFEST_HASH,
        "source_original_preflight_hash": EXPECTED_ORIGINAL_PREFLIGHT_HASH,
        "source_original_result_hash": EXPECTED_ORIGINAL_RESULT_HASH,
        "retained_nvda_evidence_hash": EXPECTED_RETAINED_NVDA_EVIDENCE_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "request_template_hashes": list(EXPECTED_TEMPLATE_HASHES),
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "owner_approval_id": owner_approval_id,
        "owner_approval_at_utc": owner_approval_at_utc,
        "msft_reread_from_frozen_window_start": True,
        "wire_repair_rule": WIRE_REPAIR_RULE,
        "raw_response_persistence_rule": RAW_PERSISTENCE_RULE,
        "raw_response_snapshot_before_parse": True,
        "authority_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "single_authorized_wire_repair_read_pass_only": True,
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "conditional_followup_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "broker_writes_authorized": False,
        "alpaca_orders_authorized": False,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


@dataclass
class DurableWireRepairJournal:
    path: Path
    raw_dir: Path
    authorization_hash: str
    attempt_count: int = 0
    raw_snapshot_count: int = 0

    def _append(self, payload: Mapping[str, Any]) -> str:
        row = dict(payload)
        row["event_hash"] = canonical_sha256(row)
        raw = (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return str(row["event_hash"])

    def binding(self, *, bundle_id: str, request_hash: str, binding_payload: Mapping[str, Any]) -> str:
        return self._append({
            "event_version": JOURNAL_EVENT_VERSION,
            "event_type": "DYNAMIC_REQUEST_BINDING",
            "recorded_at_utc": v01._utc_now_text(),
            "authorization_artifact_hash": self.authorization_hash,
            "bundle_id": bundle_id,
            "request_hash": request_hash,
            "binding_payload": dict(binding_payload),
        })

    def dispatch_attempt(self, *, bundle_id: str, request_hash: str, dispatch_index_within_bundle: int) -> str:
        self.attempt_count += 1
        _need(self.attempt_count <= MAX_DISPATCH_ATTEMPTS, "provider dispatch ceiling exceeded")
        return self._append({
            "event_version": JOURNAL_EVENT_VERSION,
            "event_type": "PROVIDER_DISPATCH_ATTEMPT",
            "recorded_at_utc": v01._utc_now_text(),
            "authorization_artifact_hash": self.authorization_hash,
            "global_dispatch_index": self.attempt_count,
            "bundle_id": bundle_id,
            "dispatch_index_within_bundle": dispatch_index_within_bundle,
            "request_hash": request_hash,
        })

    def raw_snapshot(self, *, bundle_id: str, dispatch_index_within_bundle: int, response_bytes: bytes) -> tuple[str, str]:
        _need(bool(response_bytes), "raw provider response must be non-empty")
        _need(re.fullmatch(r"[A-Z0-9_]+", bundle_id or "") is not None, "bundle id unsafe for raw snapshot path")
        sha = hashlib.sha256(response_bytes).hexdigest()
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{bundle_id}__{dispatch_index_within_bundle:02d}__{sha}.json"
        path = self.raw_dir / filename
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(response_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        self.raw_snapshot_count += 1
        event_hash = self._append({
            "event_version": JOURNAL_EVENT_VERSION,
            "event_type": "PROVIDER_RAW_RESPONSE_SNAPSHOT",
            "recorded_at_utc": v01._utc_now_text(),
            "authorization_artifact_hash": self.authorization_hash,
            "bundle_id": bundle_id,
            "dispatch_index_within_bundle": dispatch_index_within_bundle,
            "raw_snapshot_file": filename,
            "response_sha256": sha,
            "response_bytes": len(response_bytes),
        })
        return filename, event_hash

    def response_receipt(self, *, bundle_id: str, dispatch_index_within_bundle: int, response_bytes: bytes) -> str:
        return self._append({
            "event_version": JOURNAL_EVENT_VERSION,
            "event_type": "PROVIDER_RESPONSE_RECEIPT",
            "recorded_at_utc": v01._utc_now_text(),
            "authorization_artifact_hash": self.authorization_hash,
            "bundle_id": bundle_id,
            "dispatch_index_within_bundle": dispatch_index_within_bundle,
            "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
            "response_bytes": len(response_bytes),
        })

    def failure(self, *, bundle_id: str, reason: str) -> str:
        return self._append({
            "event_version": JOURNAL_EVENT_VERSION,
            "event_type": "BUNDLE_FAILURE",
            "recorded_at_utc": v01._utc_now_text(),
            "authorization_artifact_hash": self.authorization_hash,
            "bundle_id": bundle_id,
            "reason": reason,
        })


@dataclass
class AuditedWireRepairNewsTransport:
    delegate: ReopenAlpacaCliNewsTransport
    journal: DurableWireRepairJournal
    bundle_id: str
    dispatch_index: int = 0

    def get(self, *, endpoint: str, query: Mapping[str, str], api_key_id: str, api_secret_key: str) -> tuple[int, bytes]:
        self.dispatch_index += 1
        request_hash = canonical_sha256({"endpoint": endpoint, "query": dict(query), "profile": PROFILE})
        self.journal.dispatch_attempt(
            bundle_id=self.bundle_id,
            request_hash=request_hash,
            dispatch_index_within_bundle=self.dispatch_index,
        )
        status, raw = self.delegate.get(
            endpoint=endpoint,
            query=query,
            api_key_id=api_key_id,
            api_secret_key=api_secret_key,
        )
        self.journal.raw_snapshot(
            bundle_id=self.bundle_id,
            dispatch_index_within_bundle=self.dispatch_index,
            response_bytes=raw,
        )
        self.journal.response_receipt(
            bundle_id=self.bundle_id,
            dispatch_index_within_bundle=self.dispatch_index,
            response_bytes=raw,
        )
        return status, raw


def _run_cli_json(
    *,
    bundle_id: str,
    command: Sequence[str],
    journal: DurableWireRepairJournal,
    timeout_seconds: int = 45,
) -> tuple[Any, str, str]:
    executable = shutil.which("alpaca")
    _need(executable is not None, "Alpaca CLI executable unavailable")
    full = [executable, *command, "--profile", PROFILE, "--quiet"]
    request_hash = canonical_sha256({"command": full[1:], "profile": PROFILE})
    journal.dispatch_attempt(bundle_id=bundle_id, request_hash=request_hash, dispatch_index_within_bundle=1)
    env = dict(os.environ)
    env.pop("ALPACA_LIVE_TRADE", None)
    env["ALPACA_QUIET"] = "1"
    try:
        completed = subprocess.run(
            full,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContinuationWireRepairRuntimeError(f"{bundle_id} provider process failed") from exc
    _need(completed.returncode == 0, f"{bundle_id} provider command failed")
    _need(bool(completed.stdout), f"{bundle_id} returned empty stdout")
    raw = bytes(completed.stdout)
    journal.raw_snapshot(bundle_id=bundle_id, dispatch_index_within_bundle=1, response_bytes=raw)
    journal.response_receipt(bundle_id=bundle_id, dispatch_index_within_bundle=1, response_bytes=raw)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContinuationWireRepairRuntimeError(f"{bundle_id} returned invalid JSON") from exc
    return payload, hashlib.sha256(raw).hexdigest(), request_hash


def _normalize_terminal_token_in_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if normalized.get("next_page_token") == "":
        normalized["next_page_token"] = None
    return normalized


def _news_output(*, bundle_id: str, read: AlpacaNewsReopenRead, dispatches: int) -> dict[str, Any]:
    return v01._news_output(bundle_id=bundle_id, read=read, dispatches=dispatches)


def execute_once(
    *,
    continuation_preflight: Mapping[str, Any],
    original_preflight: Mapping[str, Any],
    original_result: Mapping[str, Any],
    authorization: Mapping[str, Any],
    journal_path: Path,
    raw_dir: Path,
) -> dict[str, Any]:
    continuation_hash = verify_continuation_preflight(continuation_preflight)
    original_preflight_hash = verify_original_preflight(original_preflight)
    original_result_summary = verify_original_result(original_result)
    auth_hash = _self_hash(authorization)
    _need(authorization.get("source_failure_reconciliation_hash") == EXPECTED_FAILURE_RECONCILIATION_HASH, "authorization failure-reconciliation drift")
    _need(authorization.get("source_continuation_preflight_hash") == continuation_hash, "authorization continuation preflight drift")
    _need(authorization.get("source_original_preflight_hash") == original_preflight_hash, "authorization original preflight drift")
    _need(authorization.get("source_original_result_hash") == original_result_summary["result_artifact_hash"], "authorization original result drift")
    _need(authorization.get("continuation_request_manifest_hash") == EXPECTED_CONTINUATION_MANIFEST_HASH, "authorization manifest drift")
    _need(authorization.get("provider_dispatch_attempts_max") == MAX_DISPATCH_ATTEMPTS, "authorization ceiling drift")
    _need(authorization.get("raw_response_snapshot_before_parse") is True, "raw response persistence authority drift")
    _need(authorization.get("model_calls_authorized") is False, "model calls forbidden")
    _need(authorization.get("broker_writes_authorized") is False, "broker writes forbidden")

    journal = DurableWireRepairJournal(path=journal_path, raw_dir=raw_dir, authorization_hash=auth_hash)
    outputs: list[dict[str, Any]] = []
    current_bundle = BUNDLE_IDS[0]

    def news_refresh(*, continuation_bundle_id: str, original_bundle_id: str, symbol: str) -> None:
        nonlocal current_bundle
        current_bundle = continuation_bundle_id
        row = v01._original_request_row(original_preflight, original_bundle_id)
        contract = row.get("resolved_request_contract")
        _need(isinstance(contract, Mapping), f"{original_bundle_id} resolved contract missing")
        transport = AuditedWireRepairNewsTransport(
            delegate=ReopenAlpacaCliNewsTransport(profile=PROFILE),
            journal=journal,
            bundle_id=continuation_bundle_id,
        )
        read = read_alpaca_news_window_for_reopen(
            symbol=symbol,
            window_start=v01._parse_utc(str(contract["window_start_utc"])),
            window_end=v01._parse_utc(str(contract["window_end_utc"])),
            research_cutoff=v01._parse_utc(EXPECTED_REOPEN_CUTOFF_UTC),
            page_size=int(contract["page_size"]),
            max_pages=int(contract["max_pages"]),
            api_key_id=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
            api_secret_key=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
            transport=transport,
        )
        outputs.append(_news_output(bundle_id=continuation_bundle_id, read=read, dispatches=transport.dispatch_index))

    try:
        news_refresh(
            continuation_bundle_id="CR1_MSFT_NEWS_REFRESH",
            original_bundle_id="ER2_MSFT_NEWS_REFRESH",
            symbol="MSFT",
        )
        news_refresh(
            continuation_bundle_id="CR2_META_NEWS_REFRESH",
            original_bundle_id="ER3_META_NEWS_REFRESH",
            symbol="META",
        )

        current_bundle = "CR3_CURRENT_PAPER_POSITIONS"
        positions, positions_sha, positions_request_hash = _run_cli_json(
            bundle_id=current_bundle,
            command=["position", "list"],
            journal=journal,
        )
        position_symbols = v01._position_symbols(positions)
        outputs.append({
            "bundle_id": current_bundle,
            "status": "PASS",
            "provider_dispatch_attempts": 1,
            "response_sha256": positions_sha,
            "request_hash": positions_request_hash,
            "equity_position_symbols": position_symbols,
            "response_payload": positions,
        })

        current_bundle = "CR4_CURRENT_PORTFOLIO_EQUITY"
        er5 = v01._original_request_row(original_preflight, "ER5_CURRENT_PORTFOLIO_EQUITY")["resolved_request_contract"]
        _need(isinstance(er5, Mapping), "ER5 contract missing")
        portfolio, portfolio_sha, portfolio_request_hash = _run_cli_json(
            bundle_id=current_bundle,
            command=[
                "account", "portfolio",
                "--start", str(er5["start_utc"]),
                "--end", str(er5["end_utc"]),
                "--timeframe", str(er5["timeframe"]),
                "--intraday-reporting", str(er5["intraday_reporting"]),
            ],
            journal=journal,
        )
        outputs.append({
            "bundle_id": current_bundle,
            "status": "PASS",
            "provider_dispatch_attempts": 1,
            "response_sha256": portfolio_sha,
            "request_hash": portfolio_request_hash,
            "response_payload": portfolio,
        })

        current_bundle = "CR5_DYNAMIC_MARKET_CONTEXT"
        er6 = v01._original_request_row(original_preflight, "ER6_DYNAMIC_MARKET_CONTEXT")["resolved_request_contract"]
        _need(isinstance(er6, Mapping), "ER6 contract missing")
        symbols = v01._final_market_symbols(position_symbols)
        final_market_request = {
            "symbols": symbols,
            "start_utc": er6["start_utc"],
            "end_utc": er6["end_utc"],
            "timeframe": er6["timeframe"],
            "feed": er6["feed"],
            "sort": er6["sort"],
            "limit": er6["limit"],
        }
        final_market_request_hash = canonical_sha256(final_market_request)
        journal.binding(
            bundle_id=current_bundle,
            request_hash=final_market_request_hash,
            binding_payload=final_market_request,
        )
        market_raw, market_sha, market_request_hash = _run_cli_json(
            bundle_id=current_bundle,
            command=[
                "data", "multi-bars",
                "--symbols", ",".join(symbols),
                "--start", str(er6["start_utc"]),
                "--end", str(er6["end_utc"]),
                "--timeframe", str(er6["timeframe"]),
                "--feed", str(er6["feed"]),
                "--sort", str(er6["sort"]),
                "--limit", str(er6["limit"]),
            ],
            journal=journal,
        )
        _need(isinstance(market_raw, Mapping), "CR5 response must be object")
        market = _normalize_terminal_token_in_mapping(market_raw)
        _need(market.get("next_page_token") is None, "CR5 pagination is not terminal within one-page bound")
        outputs.append({
            "bundle_id": current_bundle,
            "status": "PASS",
            "provider_dispatch_attempts": 1,
            "response_sha256": market_sha,
            "request_hash": market_request_hash,
            "final_request_hash": final_market_request_hash,
            "final_symbols": symbols,
            "response_payload": market,
        })

        current_bundle = "CR6_NVDA_NEWS_CONTINUATION"
        retained: AlpacaNewsReopenRead = original_result_summary["retained_typed_response"]
        transport = AuditedWireRepairNewsTransport(
            delegate=ReopenAlpacaCliNewsTransport(profile=PROFILE),
            journal=journal,
            bundle_id=current_bundle,
        )
        continuation = read_alpaca_news_continuation_from_saved_token(
            symbol="NVDA",
            window_start=retained.window_start,
            window_end=retained.window_end,
            research_cutoff=v01._parse_utc(EXPECTED_REOPEN_CUTOFF_UTC),
            start_page_token=EXPECTED_NVDA_START_TOKEN,
            retained_articles=retained.articles,
            page_size=5,
            max_additional_pages=4,
            api_key_id=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
            api_secret_key=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
            transport=transport,
        )
        continuation_payload = continuation.model_dump(mode="json")
        outputs.append({
            "bundle_id": current_bundle,
            "status": "PASS" if continuation.pagination_complete else "PARTIAL_PAGINATION_BOUND",
            "provider_dispatch_attempts": transport.dispatch_index,
            "response_artifact_hash": canonical_sha256(continuation_payload),
            "pagination_complete": continuation.pagination_complete,
            "terminal_next_page_token": continuation.terminal_next_page_token,
            "retained_article_count": continuation.retained_article_count,
            "new_article_count": len(continuation.new_articles),
            "total_article_count": continuation.total_article_count,
            "retained_evidence_hash": EXPECTED_RETAINED_NVDA_EVIDENCE_HASH,
            "response_artifact": continuation_payload,
        })
    except (ContinuationWireRepairRuntimeError, AlpacaNewsReadError, ValueError, OSError) as exc:
        journal.failure(bundle_id=current_bundle, reason=str(exc))
        return _blocked_result(
            authorization_hash=auth_hash,
            continuation_preflight_hash=continuation_hash,
            journal=journal,
            outputs=outputs,
            failed_bundle_id=current_bundle,
            reason=str(exc),
        )

    result = {
        "artifact_version": RESULT_VERSION,
        "status": SUCCESS_STATUS,
        "source_failure_reconciliation_hash": EXPECTED_FAILURE_RECONCILIATION_HASH,
        "source_continuation_preflight_hash": continuation_hash,
        "source_original_preflight_hash": original_preflight_hash,
        "source_original_result_hash": original_result_summary["result_artifact_hash"],
        "authorization_artifact_hash": auth_hash,
        "continuation_request_manifest_hash": EXPECTED_CONTINUATION_MANIFEST_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "provider_dispatch_attempts": journal.attempt_count,
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "raw_response_snapshot_count": journal.raw_snapshot_count,
        "raw_response_snapshot_before_parse": True,
        "bundle_results": outputs,
        "completed_or_partial_bundle_count": len(outputs),
        "partial_bundle_ids": [row["bundle_id"] for row in outputs if row.get("status") == "PARTIAL_PAGINATION_BOUND"],
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "model_calls": 0,
        "model_synthesis_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE_SUCCESS,
    }
    result["artifact_hash"] = canonical_sha256(result)
    return result


def _blocked_result(
    *,
    authorization_hash: str,
    continuation_preflight_hash: str,
    journal: DurableWireRepairJournal,
    outputs: Sequence[Mapping[str, Any]],
    failed_bundle_id: str,
    reason: str,
) -> dict[str, Any]:
    result = {
        "artifact_version": RESULT_VERSION,
        "status": BLOCKED_STATUS,
        "source_failure_reconciliation_hash": EXPECTED_FAILURE_RECONCILIATION_HASH,
        "source_continuation_preflight_hash": continuation_preflight_hash,
        "source_original_preflight_hash": EXPECTED_ORIGINAL_PREFLIGHT_HASH,
        "source_original_result_hash": EXPECTED_ORIGINAL_RESULT_HASH,
        "authorization_artifact_hash": authorization_hash,
        "continuation_request_manifest_hash": EXPECTED_CONTINUATION_MANIFEST_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "provider_dispatch_attempts": journal.attempt_count,
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "raw_response_snapshot_count": journal.raw_snapshot_count,
        "raw_response_snapshot_before_parse": True,
        "bundle_results": list(outputs),
        "failed_bundle_id": failed_bundle_id,
        "failure_reason": reason,
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "model_calls": 0,
        "model_synthesis_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE_FAILURE,
    }
    result["artifact_hash"] = canonical_sha256(result)
    return result
