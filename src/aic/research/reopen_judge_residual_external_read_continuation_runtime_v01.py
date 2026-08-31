from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.data.providers.alpaca_cli_news import CLI_PROFILE_CREDENTIAL_PLACEHOLDER
from aic.data.providers.alpaca_news import AlpacaNewsReadError
from aic.data.providers.alpaca_news_reopen import AlpacaNewsReopenRead, ReopenAlpacaCliNewsTransport, read_alpaca_news_window_for_reopen
from aic.data.providers.alpaca_news_reopen_continuation import read_alpaca_news_continuation_from_saved_token
from aic.domain.canonical import canonical_sha256

from . import reopen_judge_durable_provider_read_failure_reconciliation_v02 as reconciliation_v02
from . import reopen_judge_residual_external_read_continuation_preflight_v01 as continuation_preflight_v01
from . import reopen_judge_residual_external_read_runtime_v01 as original_runtime_v01


AUTH_VERSION = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_AUTHORIZATION_v0_1"
DRY_VERSION = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_RUNNER_DRY_v0_1"
RESULT_VERSION = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_RESULT_v0_1"
JOURNAL_EVENT_VERSION = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_JOURNAL_EVENT_v0_1"
READY_STATUS = "READY_FOR_EXPLICIT_OWNER_B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_AUTHORIZATION_V01"
SUCCESS_STATUS = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_PASS_FROZEN"
BLOCKED_STATUS = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_BLOCKED"
NEXT_GATE_SUCCESS = "B3_RESEARCH_REOPEN_POST_CONTINUATION_READ_EVIDENCE_RECONCILIATION_ZERO_CALL"

EXPECTED_CONTINUATION_PREFLIGHT_HASH = "d50605627567787317c90ac56fb16e4fea1f4b5a3326439383296a4ec6e96fe4"
EXPECTED_CONTINUATION_PREFLIGHT_CODE_SHA = "01b45356f58644b9d2eb1b3f912928ee9cc1d906"
EXPECTED_CONTINUATION_MANIFEST_HASH = "7be13f17d4ab17c86adae8e170fcf1578a09cc3239c26228f822a5f3008525aa"
EXPECTED_ORIGINAL_PREFLIGHT_HASH = "610f12652f856166a0661ff92f135ea9e5ea60d263eb663720c479ee3fe5ff45"
EXPECTED_ORIGINAL_MANIFEST_HASH = "13578f74c1b34de0bbe33fc59b0e0648dce47a155900f82e04903c0ab7ffe379"
EXPECTED_ORIGINAL_RESULT_HASH = "45980cba660dff7df1e013808c760a7eae95456e830e734ecd1641021d0cdfc1"
EXPECTED_RETAINED_NVDA_EVIDENCE_HASH = "447ea804f62054b7ac87d07a9f929f5f748187e7ebe9cef3170e14e2284a211d"
EXPECTED_REOPEN_CUTOFF_UTC = "2026-08-31T08:58:17Z"
EXPECTED_NVDA_START_TOKEN = "MTc4ODAwODQzNDAwMDAwMDAwMHw2MTUxMDk3Mw=="
EXPECTED_NVDA_START_TOKEN_HASH = "24988cfddb984da869ce3936857a13f136ca40229dbf71872ecf9a4245dc11c8"
EXPECTED_TEMPLATE_HASHES = (
    "43441e0f5c53299766c05cbd263e9548d0b76c98e947473716d8bc1b53cd094b",
    "c4760775cdf918ce9c59b2f07f12650cdf225e9f1a24855c22414e9ef2986c6e",
    "195311f037e87aa18e8aefbf79acba9d51c3a45dc32fea64df763621e34f19cf",
    "0df40676efd6238e7abe615668d79cb60b88b83b4dee720896dbf0c377900a5a",
    "ec92a3f22b0a3d91968ee55cc632115da6b162cf49631745cd01e14a7c605e44",
    "b1c4c7a3c0f827cc90931bab41054d0105ea6abeaed52d0550e233c1efbb1b77",
)
BUNDLE_IDS = (
    "CR1_MSFT_NEWS_REFRESH",
    "CR2_META_NEWS_REFRESH",
    "CR3_CURRENT_PAPER_POSITIONS",
    "CR4_CURRENT_PORTFOLIO_EQUITY",
    "CR5_DYNAMIC_MARKET_CONTEXT",
    "CR6_NVDA_NEWS_CONTINUATION",
)
MAX_DISPATCH_ATTEMPTS = 11
PROFILE = "paper"


class ResidualExternalReadContinuationRuntimeError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ResidualExternalReadContinuationRuntimeError(message)


def _utc_now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_utc(text: str) -> datetime:
    _need(isinstance(text, str) and text.endswith("Z"), "UTC timestamp required")
    try:
        return datetime.fromisoformat(text[:-1] + "+00:00").astimezone(UTC)
    except ValueError as exc:
        raise ResidualExternalReadContinuationRuntimeError("invalid UTC timestamp") from exc


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, f"{field} missing")
    _need(observed == canonical_sha256(payload, exclude_fields=(field,)), f"{field} self-hash mismatch")
    return observed


def verify_continuation_preflight(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_CONTINUATION_PREFLIGHT_HASH, "continuation preflight hash drift")
    continuation_preflight_v01.verify_preflight(
        payload,
        expected_code_commit_sha=EXPECTED_CONTINUATION_PREFLIGHT_CODE_SHA,
    )
    _need(payload.get("continuation_request_manifest_hash") == EXPECTED_CONTINUATION_MANIFEST_HASH, "continuation manifest drift")
    _need(payload.get("source_original_preflight_hash") == EXPECTED_ORIGINAL_PREFLIGHT_HASH, "original preflight lineage drift")
    _need(payload.get("reopen_cutoff_utc") == EXPECTED_REOPEN_CUTOFF_UTC, "reopen cutoff drift")
    _need(payload.get("provider_dispatch_attempts_max") == MAX_DISPATCH_ATTEMPTS, "continuation dispatch ceiling drift")
    _need(payload.get("request_template_hashes") == list(EXPECTED_TEMPLATE_HASHES), "continuation template hash drift")
    _need(payload.get("provider_reads_authorized") is False, "continuation preflight unexpectedly authorizes reads")
    _need(payload.get("model_calls_authorized") is False, "continuation preflight unexpectedly authorizes model calls")
    return observed


def verify_original_preflight(payload: Mapping[str, Any]) -> str:
    observed = original_runtime_v01.verify_preflight(payload)
    _need(observed == EXPECTED_ORIGINAL_PREFLIGHT_HASH, "original preflight hash drift")
    _need(payload.get("request_manifest_hash") == EXPECTED_ORIGINAL_MANIFEST_HASH, "original manifest drift")
    return observed


def verify_original_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    summary = reconciliation_v02.verify_result_v02(payload)
    observed = _self_hash(payload)
    _need(observed == EXPECTED_ORIGINAL_RESULT_HASH, "original result hash drift")
    bundle_results = payload.get("bundle_results")
    _need(isinstance(bundle_results, list) and len(bundle_results) == 1, "one retained partial bundle required")
    bundle = bundle_results[0]
    _need(isinstance(bundle, Mapping), "retained partial bundle malformed")
    _need(bundle.get("response_artifact_hash") == EXPECTED_RETAINED_NVDA_EVIDENCE_HASH, "retained NVDA evidence hash drift")
    response = bundle.get("response_artifact")
    _need(isinstance(response, Mapping), "retained NVDA response artifact missing")
    typed = AlpacaNewsReopenRead.model_validate(dict(response))
    _need(typed.terminal_next_page_token == EXPECTED_NVDA_START_TOKEN, "retained NVDA terminal token drift")
    _need(len(typed.articles) == 10, "retained NVDA article count drift")
    return {
        **summary,
        "result_artifact_hash": observed,
        "retained_response_hash": EXPECTED_RETAINED_NVDA_EVIDENCE_HASH,
        "retained_typed_response": typed,
    }


def build_dry(
    *,
    continuation_preflight: Mapping[str, Any],
    original_preflight: Mapping[str, Any],
    original_result: Mapping[str, Any],
    code_commit_sha: str,
) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha or "") is not None, "exact code SHA required")
    continuation_hash = verify_continuation_preflight(continuation_preflight)
    original_preflight_hash = verify_original_preflight(original_preflight)
    original_result_summary = verify_original_result(original_result)
    artifact = {
        "artifact_version": DRY_VERSION,
        "status": READY_STATUS,
        "code_commit_sha": code_commit_sha,
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
        "single_authorized_continuation_read_pass_only": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "pagination_incomplete_continue_policy": "RETAIN_PARTIAL_AND_CONTINUE_TO_LATER_BUNDLES",
        "transport_or_validation_error_policy": "STOP_IMMEDIATELY",
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
        "next_gate": "EXPLICIT_OWNER_B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_AUTHORIZATION_V01",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_dry(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload)
    exact = {
        "artifact_version": DRY_VERSION,
        "status": READY_STATUS,
        "code_commit_sha": expected_code_commit_sha,
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
        "single_authorized_continuation_read_pass_only": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "pagination_incomplete_continue_policy": "RETAIN_PARTIAL_AND_CONTINUE_TO_LATER_BUNDLES",
        "transport_or_validation_error_policy": "STOP_IMMEDIATELY",
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
        "next_gate": "EXPLICIT_OWNER_B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_AUTHORIZATION_V01",
    }
    for key, expected in exact.items():
        _need(payload.get(key) == expected, f"runner dry drift: {key}")
    return observed


def build_authorization(
    *,
    dry: Mapping[str, Any],
    owner_approval_id: str,
    owner_approval_at_utc: str,
    code_commit_sha: str,
) -> dict[str, Any]:
    dry_hash = verify_dry(dry, expected_code_commit_sha=code_commit_sha)
    _need(
        owner_approval_id == "OWNER-B3-RESEARCH-REOPEN-RESIDUAL-EXTERNAL-READ-CONTINUATION-V01",
        "owner approval id drift",
    )
    _parse_utc(owner_approval_at_utc)
    artifact = {
        "artifact_version": AUTH_VERSION,
        "status": "AUTHORIZED_EXACTLY_ONE_BOUNDED_CONTINUATION_PROVIDER_READ_PASS",
        "code_commit_sha": code_commit_sha,
        "source_runner_dry_hash": dry_hash,
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
        "authority_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "single_authorized_continuation_read_pass_only": True,
        "pagination_incomplete_continue_policy": "RETAIN_PARTIAL_AND_CONTINUE_TO_LATER_BUNDLES",
        "transport_or_validation_error_policy": "STOP_IMMEDIATELY",
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
class DurableContinuationJournal:
    path: Path
    authorization_hash: str
    attempt_count: int = 0

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
            "recorded_at_utc": _utc_now_text(),
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
            "recorded_at_utc": _utc_now_text(),
            "authorization_artifact_hash": self.authorization_hash,
            "global_dispatch_index": self.attempt_count,
            "bundle_id": bundle_id,
            "dispatch_index_within_bundle": dispatch_index_within_bundle,
            "request_hash": request_hash,
        })

    def response_receipt(self, *, bundle_id: str, dispatch_index_within_bundle: int, response_bytes: bytes) -> str:
        return self._append({
            "event_version": JOURNAL_EVENT_VERSION,
            "event_type": "PROVIDER_RESPONSE_RECEIPT",
            "recorded_at_utc": _utc_now_text(),
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
            "recorded_at_utc": _utc_now_text(),
            "authorization_artifact_hash": self.authorization_hash,
            "bundle_id": bundle_id,
            "reason": reason,
        })


@dataclass
class AuditedContinuationNewsTransport:
    delegate: ReopenAlpacaCliNewsTransport
    journal: DurableContinuationJournal
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
    journal: DurableContinuationJournal,
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
        raise ResidualExternalReadContinuationRuntimeError(f"{bundle_id} provider process failed") from exc
    _need(completed.returncode == 0, f"{bundle_id} provider command failed")
    _need(bool(completed.stdout), f"{bundle_id} returned empty stdout")
    journal.response_receipt(bundle_id=bundle_id, dispatch_index_within_bundle=1, response_bytes=bytes(completed.stdout))
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResidualExternalReadContinuationRuntimeError(f"{bundle_id} returned invalid JSON") from exc
    return payload, hashlib.sha256(completed.stdout).hexdigest(), request_hash


def _position_symbols(payload: Any) -> list[str]:
    _need(isinstance(payload, list), "positions response must be an array")
    out: list[str] = []
    for row in payload:
        _need(isinstance(row, Mapping), "position row malformed")
        symbol = row.get("symbol")
        asset_class = row.get("asset_class")
        if asset_class not in (None, "us_equity"):
            continue
        _need(isinstance(symbol, str) and symbol == symbol.strip().upper() and symbol, "position symbol malformed")
        if symbol not in out:
            out.append(symbol)
    _need(len(out) <= 18, "equity position scope exceeds 18-symbol runtime bound")
    return out


def _final_market_symbols(position_symbols: Sequence[str]) -> list[str]:
    required = ["MSFT", "META"]
    additional = sorted(symbol for symbol in set(position_symbols) if symbol not in required)
    final = required + additional
    _need(len(final) <= 20, "dynamic market symbol scope exceeds 20")
    return final


def _original_request_row(original_preflight: Mapping[str, Any], bundle_id: str) -> Mapping[str, Any]:
    rows = original_preflight.get("request_preflights")
    _need(isinstance(rows, list), "original request preflights missing")
    matches = [row for row in rows if isinstance(row, Mapping) and row.get("bundle_id") == bundle_id]
    _need(len(matches) == 1, f"original request preflight missing: {bundle_id}")
    return matches[0]


def _news_output(*, bundle_id: str, read: AlpacaNewsReopenRead, dispatches: int) -> dict[str, Any]:
    payload = read.model_dump(mode="json")
    return {
        "bundle_id": bundle_id,
        "status": "PASS" if read.pagination_complete else "PARTIAL_PAGINATION_BOUND",
        "provider_dispatch_attempts": dispatches,
        "response_artifact_hash": canonical_sha256(payload),
        "pagination_complete": read.pagination_complete,
        "terminal_next_page_token": read.terminal_next_page_token,
        "article_count": len(read.articles),
        "response_artifact": payload,
    }


def execute_once(
    *,
    continuation_preflight: Mapping[str, Any],
    original_preflight: Mapping[str, Any],
    original_result: Mapping[str, Any],
    authorization: Mapping[str, Any],
    journal_path: Path,
) -> dict[str, Any]:
    continuation_hash = verify_continuation_preflight(continuation_preflight)
    original_preflight_hash = verify_original_preflight(original_preflight)
    original_result_summary = verify_original_result(original_result)
    auth_hash = _self_hash(authorization)
    _need(authorization.get("source_continuation_preflight_hash") == continuation_hash, "authorization continuation preflight drift")
    _need(authorization.get("source_original_preflight_hash") == original_preflight_hash, "authorization original preflight drift")
    _need(authorization.get("source_original_result_hash") == original_result_summary["result_artifact_hash"], "authorization original result drift")
    _need(authorization.get("continuation_request_manifest_hash") == EXPECTED_CONTINUATION_MANIFEST_HASH, "authorization manifest drift")
    _need(authorization.get("provider_dispatch_attempts_max") == MAX_DISPATCH_ATTEMPTS, "authorization ceiling drift")
    _need(authorization.get("model_calls_authorized") is False, "model calls forbidden")
    _need(authorization.get("broker_writes_authorized") is False, "broker writes forbidden")

    journal = DurableContinuationJournal(path=journal_path, authorization_hash=auth_hash)
    outputs: list[dict[str, Any]] = []
    current_bundle = BUNDLE_IDS[0]

    def news_refresh(*, continuation_bundle_id: str, original_bundle_id: str, symbol: str) -> None:
        nonlocal current_bundle
        current_bundle = continuation_bundle_id
        row = _original_request_row(original_preflight, original_bundle_id)
        contract = row.get("resolved_request_contract")
        _need(isinstance(contract, Mapping), f"{original_bundle_id} resolved contract missing")
        transport = AuditedContinuationNewsTransport(
            delegate=ReopenAlpacaCliNewsTransport(profile=PROFILE),
            journal=journal,
            bundle_id=continuation_bundle_id,
        )
        read = read_alpaca_news_window_for_reopen(
            symbol=symbol,
            window_start=_parse_utc(str(contract["window_start_utc"])),
            window_end=_parse_utc(str(contract["window_end_utc"])),
            research_cutoff=_parse_utc(EXPECTED_REOPEN_CUTOFF_UTC),
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
        position_symbols = _position_symbols(positions)
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
        er5 = _original_request_row(original_preflight, "ER5_CURRENT_PORTFOLIO_EQUITY")["resolved_request_contract"]
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
        er6 = _original_request_row(original_preflight, "ER6_DYNAMIC_MARKET_CONTEXT")["resolved_request_contract"]
        _need(isinstance(er6, Mapping), "ER6 contract missing")
        symbols = _final_market_symbols(position_symbols)
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
        market, market_sha, market_request_hash = _run_cli_json(
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
        _need(isinstance(market, Mapping), "CR5 response must be object")
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
        transport = AuditedContinuationNewsTransport(
            delegate=ReopenAlpacaCliNewsTransport(profile=PROFILE),
            journal=journal,
            bundle_id=current_bundle,
        )
        continuation = read_alpaca_news_continuation_from_saved_token(
            symbol="NVDA",
            window_start=retained.window_start,
            window_end=retained.window_end,
            research_cutoff=_parse_utc(EXPECTED_REOPEN_CUTOFF_UTC),
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
    except (ResidualExternalReadContinuationRuntimeError, AlpacaNewsReadError, ValueError) as exc:
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
        "source_continuation_preflight_hash": continuation_hash,
        "source_original_preflight_hash": original_preflight_hash,
        "source_original_result_hash": original_result_summary["result_artifact_hash"],
        "authorization_artifact_hash": auth_hash,
        "continuation_request_manifest_hash": EXPECTED_CONTINUATION_MANIFEST_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "provider_dispatch_attempts": journal.attempt_count,
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
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
    journal: DurableContinuationJournal,
    outputs: Sequence[Mapping[str, Any]],
    failed_bundle_id: str,
    reason: str,
) -> dict[str, Any]:
    result = {
        "artifact_version": RESULT_VERSION,
        "status": BLOCKED_STATUS,
        "source_continuation_preflight_hash": continuation_preflight_hash,
        "source_original_preflight_hash": EXPECTED_ORIGINAL_PREFLIGHT_HASH,
        "source_original_result_hash": EXPECTED_ORIGINAL_RESULT_HASH,
        "authorization_artifact_hash": authorization_hash,
        "continuation_request_manifest_hash": EXPECTED_CONTINUATION_MANIFEST_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "provider_dispatch_attempts": journal.attempt_count,
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
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
        "next_gate": "ZERO_CALL_DURABLE_CONTINUATION_PROVIDER_READ_FAILURE_RECONCILIATION",
    }
    result["artifact_hash"] = canonical_sha256(result)
    return result
