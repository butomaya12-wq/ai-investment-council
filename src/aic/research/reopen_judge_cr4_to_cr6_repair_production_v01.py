from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.data.providers.alpaca_cli_news import CLI_PROFILE_CREDENTIAL_PLACEHOLDER
from aic.data.providers.alpaca_news import ALPACA_NEWS_ENDPOINT, AlpacaNewsReadError
from aic.data.providers.alpaca_news_reopen import AlpacaNewsReopenRead
from aic.data.providers.alpaca_news_reopen_continuation import (
    read_alpaca_news_continuation_from_saved_token,
)
from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_cr4_to_cr6_repair_preflight_runtime_fix_v03 as preflight_v03
from aic.research import reopen_judge_cr4_to_cr6_repair_runner_dry_v01 as dry_v01


AUTH_VERSION = "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_AUTHORIZATION_v0_1"
RESULT_VERSION = "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_RESULT_v0_1"
JOURNAL_EVENT_VERSION = "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_JOURNAL_EVENT_v0_1"
AUTHORIZED_STATUS = "AUTHORIZED_EXACTLY_ONE_BOUNDED_CR4_TO_CR6_REPAIR_PROVIDER_READ_PASS"
SUCCESS_STATUS = "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_PASS_FROZEN"
BLOCKED_STATUS = "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_BLOCKED_WITH_DURABLE_PARTIAL"
NEXT_GATE = "B3_RESEARCH_REOPEN_CR4_TO_CR6_POST_READ_EVIDENCE_RECONCILIATION_ZERO_CALL"
OWNER_APPROVAL_ID = "OWNER-B3-RESEARCH-REOPEN-CR4-TO-CR6-REPAIR-V01"
PROFILE = "paper"

EXPECTED_DRY_CODE_SHA = "505cfc76070b9379fb89466c57e43a2ab382ccab"
EXPECTED_DRY_HASH = "d7ed62685390a9e7dc604d4fea7ae9af03da397d18b0fa5d43c90a26c03734d8"
EXPECTED_PREFLIGHT_HASH = dry_v01.EXPECTED_PREFLIGHT_HASH
EXPECTED_REQUEST_MANIFEST_HASH = dry_v01.EXPECTED_REQUEST_MANIFEST_HASH
EXPECTED_CAPABILITY_PROBE_HASH = dry_v01.EXPECTED_CAPABILITY_PROBE_HASH
EXPECTED_ALPACA_BINARY_SHA256 = dry_v01.EXPECTED_ALPACA_BINARY_SHA256
EXPECTED_REOPEN_CUTOFF_UTC = dry_v01.EXPECTED_REOPEN_CUTOFF_UTC
EXPECTED_SOURCE_ORIGINAL_RESULT_HASH = dry_v01.EXPECTED_SOURCE_ORIGINAL_RESULT_HASH
EXPECTED_SOURCE_RECONCILIATION_HASH = dry_v01.EXPECTED_SOURCE_RECONCILIATION_HASH
EXPECTED_TEMPLATE_HASHES = dry_v01.EXPECTED_TEMPLATE_HASHES
BUNDLE_IDS = dry_v01.BUNDLE_IDS
DISPATCH_CEILING_BY_BUNDLE = dry_v01.DISPATCH_CEILING_BY_BUNDLE
MAX_DISPATCH_ATTEMPTS = dry_v01.MAX_DISPATCH_ATTEMPTS

CREDENTIAL_ENV_KEYS = (
    "OPENAI_API_KEY",
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "ALPACA_LIVE_TRADE",
)


class CR4ToCR6RepairProductionError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise CR4ToCR6RepairProductionError(message)


def _self_hash(payload: Mapping[str, Any], *, field_name: str = "artifact_hash") -> str:
    observed = payload.get(field_name)
    _need(
        isinstance(observed, str)
        and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        f"{field_name} missing",
    )
    _need(
        observed == canonical_sha256(payload, exclude_fields=(field_name,)),
        f"{field_name} self-hash mismatch",
    )
    return observed


def _parse_owner_time(value: str) -> str:
    _need(
        isinstance(value, str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)
        is not None,
        "owner approval timestamp must be second-precision UTC Z",
    )
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise CR4ToCR6RepairProductionError("owner approval timestamp invalid") from exc
    return value


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def verify_preflight(payload: Mapping[str, Any]) -> str:
    try:
        observed = dry_v01.verify_preflight(payload)
    except Exception as exc:
        raise CR4ToCR6RepairProductionError(str(exc)) from exc
    _need(observed == EXPECTED_PREFLIGHT_HASH, "preflight hash drift")
    return observed


def verify_source_dry(payload: Mapping[str, Any]) -> str:
    try:
        observed = dry_v01.verify_dry(
            payload,
            expected_code_commit_sha=EXPECTED_DRY_CODE_SHA,
        )
    except Exception as exc:
        raise CR4ToCR6RepairProductionError(str(exc)) from exc
    _need(observed == EXPECTED_DRY_HASH, "runner dry hash drift")
    return observed


def verify_installed_cli(preflight: Mapping[str, Any]) -> dict[str, Any]:
    try:
        observed = dry_v01.verify_installed_alpaca_binary(preflight)
    except Exception as exc:
        raise CR4ToCR6RepairProductionError(str(exc)) from exc
    _need(
        observed.get("alpaca_binary_sha256") == EXPECTED_ALPACA_BINARY_SHA256,
        "Alpaca binary SHA drift at production gate",
    )
    return observed


def verify_original_result(payload: Mapping[str, Any]) -> AlpacaNewsReopenRead:
    try:
        summary = preflight_v03.verify_original_result_v03(payload)
    except Exception as exc:
        raise CR4ToCR6RepairProductionError(str(exc)) from exc
    _need(
        summary.get("result_artifact_hash") == EXPECTED_SOURCE_ORIGINAL_RESULT_HASH,
        "original result hash drift",
    )
    bundle_results = payload.get("bundle_results")
    _need(
        isinstance(bundle_results, list) and len(bundle_results) == 1,
        "original result retained bundle missing",
    )
    bundle = bundle_results[0]
    _need(isinstance(bundle, Mapping), "original retained bundle malformed")
    response = bundle.get("response_artifact")
    _need(isinstance(response, Mapping), "original retained NVDA response missing")
    try:
        typed = AlpacaNewsReopenRead.model_validate(dict(response))
    except Exception as exc:
        raise CR4ToCR6RepairProductionError(
            "original retained NVDA typed response invalid"
        ) from exc
    return typed


def _template(preflight: Mapping[str, Any], bundle_id: str) -> Mapping[str, Any]:
    rows = preflight.get("request_templates")
    _need(isinstance(rows, list), "preflight request templates missing")
    matches = [row for row in rows if isinstance(row, Mapping) and row.get("bundle_id") == bundle_id]
    _need(len(matches) == 1, f"request template missing for {bundle_id}")
    row = matches[0]
    _need(
        row.get("request_template_hash")
        == EXPECTED_TEMPLATE_HASHES[list(BUNDLE_IDS).index(bundle_id)],
        f"request template hash drift for {bundle_id}",
    )
    contract = row.get("request_contract")
    _need(isinstance(contract, Mapping), f"request contract missing for {bundle_id}")
    return contract


def build_authorization(
    *,
    preflight: Mapping[str, Any],
    dry: Mapping[str, Any],
    owner_approval_id: str,
    owner_approval_at_utc: str,
    code_commit_sha: str,
) -> dict[str, Any]:
    _need(
        isinstance(code_commit_sha, str)
        and re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None,
        "exact production code SHA required",
    )
    preflight_hash = verify_preflight(preflight)
    dry_hash = verify_source_dry(dry)
    cli = verify_installed_cli(preflight)
    _need(owner_approval_id == OWNER_APPROVAL_ID, "owner approval id drift")
    owner_time = _parse_owner_time(owner_approval_at_utc)

    artifact = {
        "artifact_version": AUTH_VERSION,
        "status": AUTHORIZED_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_runner_dry_code_commit_sha": EXPECTED_DRY_CODE_SHA,
        "source_runner_dry_hash": dry_hash,
        "source_preflight_artifact_hash": preflight_hash,
        "source_wire_repair_v02_failure_reconciliation_hash": EXPECTED_SOURCE_RECONCILIATION_HASH,
        "source_original_provider_result_hash": EXPECTED_SOURCE_ORIGINAL_RESULT_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "capability_probe_hash": EXPECTED_CAPABILITY_PROBE_HASH,
        "alpaca_binary_sha256": cli["alpaca_binary_sha256"],
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "request_template_hashes": list(EXPECTED_TEMPLATE_HASHES),
        "logical_provider_read_bundle_ids": list(BUNDLE_IDS),
        "provider_dispatch_ceiling_by_bundle": dict(DISPATCH_CEILING_BY_BUNDLE),
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "owner_approval_id": owner_approval_id,
        "owner_approval_at_utc": owner_time,
        "single_authorized_repair_provider_read_pass_only": True,
        "authority_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "completed_prior_bundle_rereads_allowed": False,
        "msft_news_reread_allowed": False,
        "meta_news_reread_allowed": False,
        "positions_reread_allowed": False,
        "remaining_bundles_independent": True,
        "bundle_failure_policy": "DURABLY_RECORD_FAILURE_AND_CONTINUE_ONLY_ALREADY_FROZEN_INDEPENDENT_READ_BUNDLES",
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "conditional_followup_reads_authorized": False,
        "unplanned_provider_reads_authorized": False,
        "pagination_beyond_frozen_bounds_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "broker_writes_authorized": False,
        "alpaca_orders_authorized": False,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


@dataclass
class RepairReadJournal:
    path: Path
    raw_dir: Path
    authorization_hash: str
    attempt_count: int = 0
    raw_snapshot_count: int = 0
    bundle_attempt_counts: dict[str, int] = field(default_factory=dict)

    def _append(self, payload: Mapping[str, Any]) -> str:
        row = dict(payload)
        row["event_hash"] = canonical_sha256(row)
        raw = (
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return str(row["event_hash"])

    def dispatch(self, *, bundle_id: str, request_hash: str) -> int:
        _need(bundle_id in DISPATCH_CEILING_BY_BUNDLE, "unfrozen bundle dispatch forbidden")
        next_bundle_count = self.bundle_attempt_counts.get(bundle_id, 0) + 1
        _need(
            next_bundle_count <= DISPATCH_CEILING_BY_BUNDLE[bundle_id],
            f"bundle dispatch ceiling exceeded for {bundle_id}",
        )
        self.attempt_count += 1
        _need(self.attempt_count <= MAX_DISPATCH_ATTEMPTS, "global provider dispatch ceiling exceeded")
        self.bundle_attempt_counts[bundle_id] = next_bundle_count
        self._append(
            {
                "event_version": JOURNAL_EVENT_VERSION,
                "event_type": "PROVIDER_DISPATCH_ATTEMPT",
                "recorded_at_utc": _utc_now_text(),
                "authorization_artifact_hash": self.authorization_hash,
                "global_dispatch_index": self.attempt_count,
                "bundle_id": bundle_id,
                "dispatch_index_within_bundle": next_bundle_count,
                "request_hash": request_hash,
            }
        )
        return next_bundle_count

    def snapshot(
        self,
        *,
        bundle_id: str,
        dispatch_index: int,
        stream: str,
        payload: bytes,
    ) -> str | None:
        if not payload:
            return None
        _need(stream in {"stdout", "stderr", "provider_response"}, "snapshot stream invalid")
        sha = hashlib.sha256(payload).hexdigest()
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{bundle_id}__{dispatch_index:02d}__{stream}__{sha}.bin"
        path = self.raw_dir / filename
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self.raw_snapshot_count += 1
        self._append(
            {
                "event_version": JOURNAL_EVENT_VERSION,
                "event_type": "PROVIDER_RAW_RESPONSE_SNAPSHOT",
                "recorded_at_utc": _utc_now_text(),
                "authorization_artifact_hash": self.authorization_hash,
                "bundle_id": bundle_id,
                "dispatch_index_within_bundle": dispatch_index,
                "stream": stream,
                "raw_snapshot_file": filename,
                "response_sha256": sha,
                "response_bytes": len(payload),
            }
        )
        return sha

    def receipt(self, *, bundle_id: str, dispatch_index: int, payload: bytes) -> str:
        return self._append(
            {
                "event_version": JOURNAL_EVENT_VERSION,
                "event_type": "PROVIDER_RESPONSE_RECEIPT",
                "recorded_at_utc": _utc_now_text(),
                "authorization_artifact_hash": self.authorization_hash,
                "bundle_id": bundle_id,
                "dispatch_index_within_bundle": dispatch_index,
                "response_sha256": hashlib.sha256(payload).hexdigest(),
                "response_bytes": len(payload),
            }
        )

    def failure(self, *, bundle_id: str, reason: str) -> str:
        return self._append(
            {
                "event_version": JOURNAL_EVENT_VERSION,
                "event_type": "BUNDLE_FAILURE",
                "recorded_at_utc": _utc_now_text(),
                "authorization_artifact_hash": self.authorization_hash,
                "bundle_id": bundle_id,
                "reason": reason,
            }
        )



def _sanitized_provider_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in CREDENTIAL_ENV_KEYS:
        env.pop(key, None)
    env["ALPACA_QUIET"] = "1"
    return env


def _run_cli_json(
    *,
    bundle_id: str,
    command: Sequence[str],
    journal: RepairReadJournal,
    timeout_seconds: int = 45,
) -> tuple[Any, str, str]:
    executable = shutil.which("alpaca")
    _need(executable is not None, "Alpaca CLI executable unavailable")
    full = [executable, *command, "--profile", PROFILE, "--quiet"]
    request_hash = canonical_sha256({"command": full[1:], "profile": PROFILE})
    dispatch_index = journal.dispatch(bundle_id=bundle_id, request_hash=request_hash)
    try:
        completed = subprocess.run(
            full,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
            env=_sanitized_provider_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CR4ToCR6RepairProductionError(f"{bundle_id} provider process failed") from exc

    stdout = bytes(completed.stdout or b"")
    stderr = bytes(completed.stderr or b"")
    if completed.returncode != 0:
        journal.snapshot(
            bundle_id=bundle_id,
            dispatch_index=dispatch_index,
            stream="stdout",
            payload=stdout,
        )
        journal.snapshot(
            bundle_id=bundle_id,
            dispatch_index=dispatch_index,
            stream="stderr",
            payload=stderr,
        )
        raise CR4ToCR6RepairProductionError(
            f"{bundle_id} provider command failed with exit {completed.returncode}"
        )
    _need(bool(stdout), f"{bundle_id} returned empty stdout")
    response_sha = journal.snapshot(
        bundle_id=bundle_id,
        dispatch_index=dispatch_index,
        stream="provider_response",
        payload=stdout,
    )
    _need(isinstance(response_sha, str), "provider response snapshot missing")
    journal.receipt(bundle_id=bundle_id, dispatch_index=dispatch_index, payload=stdout)
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CR4ToCR6RepairProductionError(f"{bundle_id} returned invalid JSON") from exc
    return payload, response_sha, request_hash


@dataclass
class ExactRepairNewsTransport:
    journal: RepairReadJournal
    bundle_id: str
    executable: str = "alpaca"
    timeout_seconds: int = 30

    def get(
        self,
        *,
        endpoint: str,
        query: Mapping[str, str],
        api_key_id: str,
        api_secret_key: str,
    ) -> tuple[int, bytes]:
        _need(endpoint == ALPACA_NEWS_ENDPOINT, "NVDA news endpoint drift")
        _need(
            api_key_id == CLI_PROFILE_CREDENTIAL_PLACEHOLDER
            and api_secret_key == CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
            "NVDA continuation requires CLI profile placeholder binding",
        )
        allowed = {
            "symbols",
            "start",
            "end",
            "sort",
            "limit",
            "include_content",
            "exclude_contentless",
        }
        _need(
            set(query) in (allowed, allowed | {"page_token"}),
            "NVDA continuation query shape drift",
        )
        _need(query.get("symbols") == "NVDA", "NVDA continuation symbol drift")
        _need(query.get("include_content") == "true", "NVDA include_content drift")
        _need(query.get("exclude_contentless") == "false", "NVDA exclude_contentless drift")
        page_token = query.get("page_token")
        _need(
            isinstance(page_token, str) and page_token and page_token == page_token.strip(),
            "NVDA continuation page token required",
        )
        executable = shutil.which(self.executable)
        _need(executable is not None, "Alpaca CLI executable unavailable")
        command = [
            executable,
            "data",
            "news",
            "--symbols",
            "NVDA",
            "--start",
            query["start"],
            "--end",
            query["end"],
            "--sort",
            query["sort"],
            "--limit",
            query["limit"],
            "--include-content=true",
            "--exclude-contentless=false",
            "--page-token",
            page_token,
            "--profile",
            PROFILE,
            "--quiet",
        ]
        request_hash = canonical_sha256({"command": command[1:], "profile": PROFILE})
        dispatch_index = self.journal.dispatch(
            bundle_id=self.bundle_id,
            request_hash=request_hash,
        )
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                env=_sanitized_provider_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AlpacaNewsReadError("NVDA continuation provider process failed") from exc
        stdout = bytes(completed.stdout or b"")
        stderr = bytes(completed.stderr or b"")
        if completed.returncode != 0:
            self.journal.snapshot(
                bundle_id=self.bundle_id,
                dispatch_index=dispatch_index,
                stream="stdout",
                payload=stdout,
            )
            self.journal.snapshot(
                bundle_id=self.bundle_id,
                dispatch_index=dispatch_index,
                stream="stderr",
                payload=stderr,
            )
            raise AlpacaNewsReadError(
                f"NVDA continuation provider command failed with exit {completed.returncode}"
            )
        if not stdout:
            self.journal.snapshot(
                bundle_id=self.bundle_id,
                dispatch_index=dispatch_index,
                stream="stderr",
                payload=stderr,
            )
            raise AlpacaNewsReadError("NVDA continuation returned empty stdout")
        self.journal.snapshot(
            bundle_id=self.bundle_id,
            dispatch_index=dispatch_index,
            stream="provider_response",
            payload=stdout,
        )
        self.journal.receipt(
            bundle_id=self.bundle_id,
            dispatch_index=dispatch_index,
            payload=stdout,
        )
        return 200, stdout


def _execute_rr1(
    *, preflight: Mapping[str, Any], journal: RepairReadJournal
) -> dict[str, Any]:
    bundle_id = "RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR"
    contract = _template(preflight, bundle_id)
    _need(contract.get("timeframe") == "1D", "RR1 timeframe drift")
    payload, response_sha, request_hash = _run_cli_json(
        bundle_id=bundle_id,
        command=[
            "account",
            "portfolio",
            "--start",
            str(contract["start_utc"]),
            "--end",
            str(contract["end_utc"]),
            "--timeframe",
            "1D",
            "--intraday-reporting",
            str(contract["intraday_reporting"]),
        ],
        journal=journal,
    )
    return {
        "bundle_id": bundle_id,
        "status": "PASS",
        "provider_dispatch_attempts": 1,
        "response_sha256": response_sha,
        "request_hash": request_hash,
        "response_payload": payload,
    }


def _execute_rr2(
    *, preflight: Mapping[str, Any], journal: RepairReadJournal
) -> dict[str, Any]:
    bundle_id = "RR2_DYNAMIC_MARKET_CONTEXT"
    contract = _template(preflight, bundle_id)
    _need(contract.get("symbols") == ["MSFT", "META"], "RR2 symbols drift")
    payload, response_sha, request_hash = _run_cli_json(
        bundle_id=bundle_id,
        command=[
            "data",
            "multi-bars",
            "--symbols",
            "MSFT,META",
            "--start",
            str(contract["start_utc"]),
            "--end",
            str(contract["end_utc"]),
            "--timeframe",
            str(contract["timeframe"]),
            "--feed",
            str(contract["feed"]),
            "--sort",
            str(contract["sort"]),
            "--limit",
            str(contract["limit"]),
        ],
        journal=journal,
    )
    _need(isinstance(payload, Mapping), "RR2 response must be object")
    normalized = dict(payload)
    if normalized.get("next_page_token") == "":
        normalized["next_page_token"] = None
    _need(
        normalized.get("next_page_token") is None,
        "RR2 pagination is not terminal within one-page bound",
    )
    return {
        "bundle_id": bundle_id,
        "status": "PASS",
        "provider_dispatch_attempts": 1,
        "response_sha256": response_sha,
        "request_hash": request_hash,
        "symbols": ["MSFT", "META"],
        "pagination_complete": True,
        "response_payload": normalized,
    }


def _execute_rr3(
    *,
    preflight: Mapping[str, Any],
    original_result: Mapping[str, Any],
    journal: RepairReadJournal,
) -> dict[str, Any]:
    bundle_id = "RR3_NVDA_NEWS_CONTINUATION"
    contract = _template(preflight, bundle_id)
    retained = verify_original_result(original_result)
    _need(
        contract.get("starting_page_token") == retained.terminal_next_page_token,
        "RR3 saved start token drift",
    )
    transport = ExactRepairNewsTransport(journal=journal, bundle_id=bundle_id)
    continuation = read_alpaca_news_continuation_from_saved_token(
        symbol="NVDA",
        window_start=retained.window_start,
        window_end=retained.window_end,
        research_cutoff=datetime.strptime(
            EXPECTED_REOPEN_CUTOFF_UTC, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc),
        start_page_token=str(contract["starting_page_token"]),
        retained_articles=retained.articles,
        page_size=int(contract["page_size"]),
        max_additional_pages=int(contract["max_additional_pages"]),
        api_key_id=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
        api_secret_key=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
        transport=transport,
    )
    response = continuation.model_dump(mode="json")
    return {
        "bundle_id": bundle_id,
        "status": "PASS" if continuation.pagination_complete else "PARTIAL_PAGINATION_BOUND",
        "provider_dispatch_attempts": journal.bundle_attempt_counts.get(bundle_id, 0),
        "response_artifact_hash": canonical_sha256(response),
        "pagination_complete": continuation.pagination_complete,
        "terminal_next_page_token": continuation.terminal_next_page_token,
        "retained_article_count": continuation.retained_article_count,
        "new_article_count": len(continuation.new_articles),
        "total_article_count": continuation.total_article_count,
        "response_artifact": response,
    }


def execute_once(
    *,
    preflight: Mapping[str, Any],
    dry: Mapping[str, Any],
    original_result: Mapping[str, Any],
    authorization: Mapping[str, Any],
    journal_path: Path,
    raw_dir: Path,
) -> dict[str, Any]:
    preflight_hash = verify_preflight(preflight)
    dry_hash = verify_source_dry(dry)
    retained = verify_original_result(original_result)
    auth_hash = _self_hash(authorization)
    _need(authorization.get("source_runner_dry_hash") == dry_hash, "authorization dry drift")
    _need(
        authorization.get("source_preflight_artifact_hash") == preflight_hash,
        "authorization preflight drift",
    )
    _need(
        authorization.get("source_original_provider_result_hash")
        == EXPECTED_SOURCE_ORIGINAL_RESULT_HASH,
        "authorization original result drift",
    )
    _need(
        authorization.get("request_manifest_hash") == EXPECTED_REQUEST_MANIFEST_HASH,
        "authorization manifest drift",
    )
    _need(
        authorization.get("provider_dispatch_attempts_max") == MAX_DISPATCH_ATTEMPTS,
        "authorization dispatch ceiling drift",
    )
    _need(authorization.get("model_calls_authorized") is False, "model calls forbidden")
    _need(authorization.get("broker_writes_authorized") is False, "broker writes forbidden")
    _need(authorization.get("alpaca_orders_authorized") is False, "orders forbidden")
    _need(
        retained.terminal_next_page_token
        == _template(preflight, "RR3_NVDA_NEWS_CONTINUATION").get("starting_page_token"),
        "retained NVDA token drift before execution",
    )

    journal = RepairReadJournal(
        path=journal_path,
        raw_dir=raw_dir,
        authorization_hash=auth_hash,
    )
    outputs: list[dict[str, Any]] = []
    failed_bundle_ids: list[str] = []

    steps = (
        ("RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR", lambda: _execute_rr1(preflight=preflight, journal=journal)),
        ("RR2_DYNAMIC_MARKET_CONTEXT", lambda: _execute_rr2(preflight=preflight, journal=journal)),
        (
            "RR3_NVDA_NEWS_CONTINUATION",
            lambda: _execute_rr3(
                preflight=preflight,
                original_result=original_result,
                journal=journal,
            ),
        ),
    )

    for bundle_id, call in steps:
        try:
            outputs.append(call())
        except (CR4ToCR6RepairProductionError, AlpacaNewsReadError, ValueError, OSError) as exc:
            reason = str(exc)
            journal.failure(bundle_id=bundle_id, reason=reason)
            failed_bundle_ids.append(bundle_id)
            outputs.append(
                {
                    "bundle_id": bundle_id,
                    "status": "FAILED",
                    "provider_dispatch_attempts": journal.bundle_attempt_counts.get(bundle_id, 0),
                    "failure_reason": reason,
                }
            )

    status = BLOCKED_STATUS if failed_bundle_ids else SUCCESS_STATUS
    result = {
        "artifact_version": RESULT_VERSION,
        "status": status,
        "source_runner_dry_hash": dry_hash,
        "source_preflight_artifact_hash": preflight_hash,
        "source_wire_repair_v02_failure_reconciliation_hash": EXPECTED_SOURCE_RECONCILIATION_HASH,
        "source_original_provider_result_hash": EXPECTED_SOURCE_ORIGINAL_RESULT_HASH,
        "authorization_artifact_hash": auth_hash,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "provider_dispatch_attempts": journal.attempt_count,
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "provider_dispatch_ceiling_by_bundle": dict(DISPATCH_CEILING_BY_BUNDLE),
        "raw_response_snapshot_count": journal.raw_snapshot_count,
        "bundle_results": outputs,
        "failed_bundle_ids": failed_bundle_ids,
        "failed_bundle_count": len(failed_bundle_ids),
        "completed_or_partial_bundle_count": len(
            [row for row in outputs if row.get("status") in {"PASS", "PARTIAL_PAGINATION_BOUND"}]
        ),
        "partial_bundle_ids": [
            row["bundle_id"]
            for row in outputs
            if row.get("status") == "PARTIAL_PAGINATION_BOUND"
        ],
        "completed_prior_bundle_rereads_performed": False,
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "model_calls": 0,
        "model_synthesis_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    result["artifact_hash"] = canonical_sha256(result)
    return result
