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
from aic.data.providers.alpaca_news_reopen import (
    ReopenAlpacaCliNewsTransport,
    read_alpaca_news_window_for_reopen,
)
from aic.domain.canonical import canonical_sha256

from . import reopen_judge_residual_external_read_preflight_v01 as preflight_v01


AUTH_VERSION = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_AUTHORIZATION_v0_1"
DRY_VERSION = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_RUNNER_DRY_v0_1"
RESULT_VERSION = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_RESULT_v0_1"
JOURNAL_EVENT_VERSION = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_JOURNAL_EVENT_v0_1"
READY_STATUS = "READY_FOR_EXPLICIT_OWNER_B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_AUTHORIZATION_V01"
SUCCESS_STATUS = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_PASS_FROZEN"
BLOCKED_STATUS = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_BLOCKED"
NEXT_GATE_SUCCESS = "B3_RESEARCH_REOPEN_POST_READ_EVIDENCE_RECONCILIATION_ZERO_CALL"

EXPECTED_PREFLIGHT_HASH = "610f12652f856166a0661ff92f135ea9e5ea60d263eb663720c479ee3fe5ff45"
EXPECTED_REQUEST_MANIFEST_HASH = "13578f74c1b34de0bbe33fc59b0e0648dce47a155900f82e04903c0ab7ffe379"
EXPECTED_REOPEN_CUTOFF_UTC = "2026-08-31T08:58:17Z"
EXPECTED_PLAN_HASH = "a37196c7998c87e2e3723f58dbfb88a58e985493497e1bab3587194b70398aa3"
EXPECTED_TEMPLATE_HASHES = (
    "b3d9424bafb31a5660b3d2fe2665e492cae5fdc9d0d09d4e12839ef2057bb4bb",
    "43441e0f5c53299766c05cbd263e9548d0b76c98e947473716d8bc1b53cd094b",
    "c4760775cdf918ce9c59b2f07f12650cdf225e9f1a24855c22414e9ef2986c6e",
    "195311f037e87aa18e8aefbf79acba9d51c3a45dc32fea64df763621e34f19cf",
    "0df40676efd6238e7abe615668d79cb60b88b83b4dee720896dbf0c377900a5a",
    "ec92a3f22b0a3d91968ee55cc632115da6b162cf49631745cd01e14a7c605e44",
)
BUNDLE_IDS = (
    "ER1_NVDA_NEWS_REFRESH",
    "ER2_MSFT_NEWS_REFRESH",
    "ER3_META_NEWS_REFRESH",
    "ER4_CURRENT_PAPER_POSITIONS",
    "ER5_CURRENT_PORTFOLIO_EQUITY",
    "ER6_DYNAMIC_MARKET_CONTEXT",
)
MAX_DISPATCH_ATTEMPTS = 9
PROFILE = "paper"


class ResidualExternalReadRuntimeError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ResidualExternalReadRuntimeError(message)


def _utc_now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_utc(text: str) -> datetime:
    _need(isinstance(text, str) and text.endswith("Z"), "UTC timestamp required")
    try:
        return datetime.fromisoformat(text[:-1] + "+00:00").astimezone(UTC)
    except ValueError as exc:
        raise ResidualExternalReadRuntimeError("invalid UTC timestamp") from exc


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, f"{field} missing")
    _need(observed == canonical_sha256(payload, exclude_fields=(field,)), f"{field} self-hash mismatch")
    return observed


def verify_preflight(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_PREFLIGHT_HASH, "preflight hash drift")
    _need(payload.get("status") == preflight_v01.PASS_STATUS, "preflight status drift")
    _need(payload.get("source_residual_external_read_plan_hash") == EXPECTED_PLAN_HASH, "plan lineage drift")
    _need(payload.get("request_manifest_hash") == EXPECTED_REQUEST_MANIFEST_HASH, "request manifest drift")
    _need(payload.get("reopen_cutoff_utc") == EXPECTED_REOPEN_CUTOFF_UTC, "reopen cutoff drift")
    _need(payload.get("provider_dispatch_attempts_max") == MAX_DISPATCH_ATTEMPTS, "dispatch ceiling drift")
    _need(payload.get("provider_reads_authorized") is False, "preflight unexpectedly authorizes reads")
    _need(payload.get("model_calls_authorized") is False, "preflight unexpectedly authorizes model calls")
    rows = payload.get("request_preflights")
    _need(isinstance(rows, list) and len(rows) == 6, "six request preflights required")
    _need(tuple(row.get("bundle_id") for row in rows if isinstance(row, Mapping)) == BUNDLE_IDS, "bundle order drift")
    _need(tuple(row.get("request_template_hash") for row in rows if isinstance(row, Mapping)) == EXPECTED_TEMPLATE_HASHES, "template hash drift")
    return observed


def build_dry(*, preflight: Mapping[str, Any], code_commit_sha: str) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha or "") is not None, "exact code SHA required")
    preflight_hash = verify_preflight(preflight)
    artifact = {
        "artifact_version": DRY_VERSION,
        "status": READY_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_preflight_hash": preflight_hash,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "request_template_hashes": list(EXPECTED_TEMPLATE_HASHES),
        "logical_provider_read_bundle_ids": list(BUNDLE_IDS),
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "single_authorized_read_pass_only": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "stop_on_bundle_error": True,
        "automatic_retries": 0,
        "conditional_followup_reads_authorized": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": "EXPLICIT_OWNER_B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_AUTHORIZATION_V01",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_dry(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == DRY_VERSION, "dry version drift")
    _need(payload.get("status") == READY_STATUS, "dry status drift")
    _need(payload.get("code_commit_sha") == expected_code_commit_sha, "dry code SHA drift")
    _need(payload.get("source_preflight_hash") == EXPECTED_PREFLIGHT_HASH, "dry preflight lineage drift")
    _need(payload.get("request_manifest_hash") == EXPECTED_REQUEST_MANIFEST_HASH, "dry request manifest drift")
    _need(payload.get("request_template_hashes") == list(EXPECTED_TEMPLATE_HASHES), "dry template binding drift")
    _need(payload.get("provider_dispatch_attempts_max") == MAX_DISPATCH_ATTEMPTS, "dry ceiling drift")
    _need(payload.get("provider_reads_authorized") is False, "dry cannot authorize reads")
    _need(payload.get("model_calls_authorized") is False, "dry cannot authorize model calls")
    return observed


def build_authorization(*, dry: Mapping[str, Any], owner_approval_id: str, owner_approval_at_utc: str, code_commit_sha: str) -> dict[str, Any]:
    dry_hash = verify_dry(dry, expected_code_commit_sha=code_commit_sha)
    _need(owner_approval_id == "OWNER-B3-RESEARCH-REOPEN-RESIDUAL-EXTERNAL-READ-V01", "owner approval id drift")
    _parse_utc(owner_approval_at_utc)
    artifact = {
        "artifact_version": AUTH_VERSION,
        "status": "AUTHORIZED_EXACTLY_ONE_BOUNDED_PROVIDER_READ_PASS",
        "code_commit_sha": code_commit_sha,
        "source_runner_dry_hash": dry_hash,
        "source_preflight_hash": EXPECTED_PREFLIGHT_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "request_template_hashes": list(EXPECTED_TEMPLATE_HASHES),
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "owner_approval_id": owner_approval_id,
        "owner_approval_at_utc": owner_approval_at_utc,
        "authority_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "single_authorized_read_pass_only": True,
        "automatic_retries": 0,
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
class DurableJournal:
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


@dataclass
class AuditedNewsTransport:
    delegate: ReopenAlpacaCliNewsTransport
    journal: DurableJournal
    bundle_id: str
    dispatch_index: int = 0

    def get(self, *, endpoint: str, query: Mapping[str, str], api_key_id: str, api_secret_key: str) -> tuple[int, bytes]:
        self.dispatch_index += 1
        request_hash = canonical_sha256({"endpoint": endpoint, "query": dict(query), "profile": PROFILE})
        self.journal.dispatch_attempt(bundle_id=self.bundle_id, request_hash=request_hash, dispatch_index_within_bundle=self.dispatch_index)
        status, raw = self.delegate.get(endpoint=endpoint, query=query, api_key_id=api_key_id, api_secret_key=api_secret_key)
        self.journal.response_receipt(bundle_id=self.bundle_id, dispatch_index_within_bundle=self.dispatch_index, response_bytes=raw)
        return status, raw


def _run_cli_json(*, bundle_id: str, command: Sequence[str], journal: DurableJournal, timeout_seconds: int = 45) -> tuple[Any, str, str]:
    executable = shutil.which("alpaca")
    _need(executable is not None, "Alpaca CLI executable unavailable")
    full = [executable, *command, "--profile", PROFILE, "--quiet"]
    request_hash = canonical_sha256({"command": full[1:], "profile": PROFILE})
    journal.dispatch_attempt(bundle_id=bundle_id, request_hash=request_hash, dispatch_index_within_bundle=1)
    env = dict(os.environ)
    env.pop("ALPACA_LIVE_TRADE", None)
    env["ALPACA_QUIET"] = "1"
    try:
        completed = subprocess.run(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_seconds, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ResidualExternalReadRuntimeError(f"{bundle_id} provider process failed") from exc
    _need(completed.returncode == 0, f"{bundle_id} provider command failed")
    _need(bool(completed.stdout), f"{bundle_id} returned empty stdout")
    journal.response_receipt(bundle_id=bundle_id, dispatch_index_within_bundle=1, response_bytes=bytes(completed.stdout))
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResidualExternalReadRuntimeError(f"{bundle_id} returned invalid JSON") from exc
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


def execute_once(*, preflight: Mapping[str, Any], authorization: Mapping[str, Any], journal_path: Path) -> dict[str, Any]:
    preflight_hash = verify_preflight(preflight)
    auth_hash = _self_hash(authorization)
    _need(authorization.get("source_preflight_hash") == preflight_hash, "authorization preflight lineage drift")
    _need(authorization.get("provider_dispatch_attempts_max") == MAX_DISPATCH_ATTEMPTS, "authorization ceiling drift")
    _need(authorization.get("model_calls_authorized") is False, "model authority forbidden")
    journal = DurableJournal(journal_path, authorization_hash=auth_hash)
    rows = preflight["request_preflights"]
    outputs: list[dict[str, Any]] = []

    for row in rows[:3]:
        bundle_id = str(row["bundle_id"])
        contract = row["resolved_request_contract"]
        symbol = str(row["symbol_scope"][0])
        transport = AuditedNewsTransport(ReopenAlpacaCliNewsTransport(profile=PROFILE), journal, bundle_id)
        try:
            read = read_alpaca_news_window_for_reopen(
                symbol=symbol,
                window_start=_parse_utc(contract["window_start_utc"]),
                window_end=_parse_utc(contract["window_end_utc"]),
                research_cutoff=_parse_utc(EXPECTED_REOPEN_CUTOFF_UTC),
                page_size=int(contract["page_size"]),
                max_pages=int(contract["max_pages"]),
                api_key_id=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
                api_secret_key=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
                transport=transport,
            )
        except AlpacaNewsReadError as exc:
            raise ResidualExternalReadRuntimeError(f"{bundle_id} news read failed") from exc
        payload = read.model_dump(mode="json")
        outputs.append({"bundle_id": bundle_id, "status": "PASS" if read.pagination_complete else "PARTIAL_STOP", "provider_dispatch_attempts": read.page_count, "response_artifact": payload, "response_artifact_hash": canonical_sha256(payload)})
        if not read.pagination_complete:
            return _blocked_result(authorization_hash=auth_hash, preflight_hash=preflight_hash, journal=journal, outputs=outputs, failed_bundle_id=bundle_id, reason="NEWS_PAGINATION_NOT_TERMINAL_WITHIN_BOUND")

    positions, positions_sha, positions_request_hash = _run_cli_json(bundle_id="ER4_CURRENT_PAPER_POSITIONS", command=["position", "list"], journal=journal)
    position_symbols = _position_symbols(positions)
    outputs.append({"bundle_id": "ER4_CURRENT_PAPER_POSITIONS", "status": "PASS", "provider_dispatch_attempts": 1, "response_sha256": positions_sha, "request_hash": positions_request_hash, "response_payload": positions, "equity_position_symbols": position_symbols})

    er5 = rows[4]["resolved_request_contract"]
    portfolio, portfolio_sha, portfolio_request_hash = _run_cli_json(
        bundle_id="ER5_CURRENT_PORTFOLIO_EQUITY",
        command=["account", "portfolio", "--start", str(er5["start_utc"]), "--end", str(er5["end_utc"]), "--timeframe", str(er5["timeframe"]), "--intraday-reporting", str(er5["intraday_reporting"])],
        journal=journal,
    )
    outputs.append({"bundle_id": "ER5_CURRENT_PORTFOLIO_EQUITY", "status": "PASS", "provider_dispatch_attempts": 1, "response_sha256": portfolio_sha, "request_hash": portfolio_request_hash, "response_payload": portfolio})

    er6 = rows[5]["resolved_request_contract"]
    symbols = _final_market_symbols(position_symbols)
    final_er6_request = {
        "symbols": symbols,
        "start_utc": er6["start_utc"],
        "end_utc": er6["end_utc"],
        "timeframe": er6["timeframe"],
        "feed": er6["feed"],
        "sort": er6["sort"],
        "limit": er6["limit"],
    }
    final_er6_request_hash = canonical_sha256(final_er6_request)
    market, market_sha, market_request_hash = _run_cli_json(
        bundle_id="ER6_DYNAMIC_MARKET_CONTEXT",
        command=["data", "multi-bars", "--symbols", ",".join(symbols), "--start", str(er6["start_utc"]), "--end", str(er6["end_utc"]), "--timeframe", str(er6["timeframe"]), "--feed", str(er6["feed"]), "--sort", str(er6["sort"]), "--limit", str(er6["limit"])],
        journal=journal,
    )
    _need(isinstance(market, Mapping), "ER6 response must be object")
    _need(market.get("next_page_token") is None, "ER6 pagination is not terminal within one-page bound")
    outputs.append({"bundle_id": "ER6_DYNAMIC_MARKET_CONTEXT", "status": "PASS", "provider_dispatch_attempts": 1, "response_sha256": market_sha, "request_hash": market_request_hash, "final_er6_request_hash": final_er6_request_hash, "final_symbols": symbols, "response_payload": market})

    result = {
        "artifact_version": RESULT_VERSION,
        "status": SUCCESS_STATUS,
        "source_preflight_hash": preflight_hash,
        "authorization_artifact_hash": auth_hash,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "provider_dispatch_attempts": journal.attempt_count,
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "bundle_results": outputs,
        "completed_bundle_count": len(outputs),
        "automatic_retries": 0,
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


def _blocked_result(*, authorization_hash: str, preflight_hash: str, journal: DurableJournal, outputs: Sequence[Mapping[str, Any]], failed_bundle_id: str, reason: str) -> dict[str, Any]:
    result = {
        "artifact_version": RESULT_VERSION,
        "status": BLOCKED_STATUS,
        "source_preflight_hash": preflight_hash,
        "authorization_artifact_hash": authorization_hash,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "provider_dispatch_attempts": journal.attempt_count,
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "bundle_results": list(outputs),
        "failed_bundle_id": failed_bundle_id,
        "failure_reason": reason,
        "automatic_retries": 0,
        "model_calls": 0,
        "model_synthesis_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": "ZERO_CALL_DURABLE_PROVIDER_READ_FAILURE_RECONCILIATION",
    }
    result["artifact_hash"] = canonical_sha256(result)
    return result
