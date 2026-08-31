from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.data.providers.alpaca_news_reopen import AlpacaNewsReopenRead
from aic.domain.canonical import canonical_sha256


VERSION = "B3_RESEARCH_REOPEN_WIRE_REPAIR_V02_FAILURE_RECONCILIATION_v0_1"
PASS_STATUS = "B3_RESEARCH_REOPEN_WIRE_REPAIR_V02_FAILURE_RECONCILIATION_ZERO_CALL_PASS"
NEXT_GATE = "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_PREFLIGHT_ZERO_CALL"

EXPECTED_AUTH_HASH = "3151ce5e74e30ca29be831f15cb410c33bd2a63bfc716a97c6792079945c860a"
EXPECTED_RESULT_HASH = "ee6f58136022e49278750e4d2c82a109adabdc7e4bd6183964bf99e2c545e565"
EXPECTED_RUNTIME_CODE_SHA = "233edb3a72f8b29adb86537f5eab3c7a43280f97"
EXPECTED_FAILURE_RECONCILIATION_HASH = "1b4fcc0ce1ed27dcbf422095fdede67153c63861b18211962958d3ecf6d199b4"
EXPECTED_CONTINUATION_PREFLIGHT_HASH = "d50605627567787317c90ac56fb16e4fea1f4b5a3326439383296a4ec6e96fe4"
EXPECTED_CONTINUATION_MANIFEST_HASH = "7be13f17d4ab17c86adae8e170fcf1578a09cc3239c26228f822a5f3008525aa"
EXPECTED_ORIGINAL_PREFLIGHT_HASH = "610f12652f856166a0661ff92f135ea9e5ea60d263eb663720c479ee3fe5ff45"
EXPECTED_ORIGINAL_RESULT_HASH = "45980cba660dff7df1e013808c760a7eae95456e830e734ecd1641021d0cdfc1"
EXPECTED_DRY_HASH = "d75a3f439ee7dc782174e5b12eb097167b408b4a4093b464f05ed1d378cc5ca8"
EXPECTED_REOPEN_CUTOFF_UTC = "2026-08-31T08:58:17Z"
EXPECTED_OWNER_APPROVAL_ID = "OWNER-B3-RESEARCH-REOPEN-CONTINUATION-WIRE-REPAIR-V02"
EXPECTED_OWNER_APPROVAL_AT_UTC = "2026-08-31T12:12:50Z"
EXPECTED_FIRST_DISPATCH_HASH = "4ab28daf55f748f07eeb4a973392c632699fc4ebb2fbc44a8c9faef00961f03f"
EXPECTED_LAST_DISPATCH_HASH = "2f367b5d4a4cec9b148113420192d70e021e267b96440a77c829e75bc2e67c64"
EXPECTED_FAILURE_REASON = "CR4_CURRENT_PORTFOLIO_EQUITY provider command failed"

EXPECTED_MSFT_RESPONSE_ARTIFACT_HASH = "94aeef972aed14129af0805c1d5118c4432980a51ed50046048793fe30f22a3b"
EXPECTED_MSFT_AGGREGATE_HASH = "a25676e1d106b6b903d528ce45087bc866cda95401cede1471d7328de3dc8eba"
EXPECTED_MSFT_PAGE_HASHES = (
    "3816bd3d37821d31eb95fff44c49d75c325d86bdd7ece667f1e890004376d18c",
    "13475cdfe1cb043e7555b5087ef21b2f7906c2c28f56da42bd1a3ee20ab8de31",
)
EXPECTED_META_RESPONSE_ARTIFACT_HASH = "1841e0622ad24c7d2a5c689a5c9447f1fa0692eb28309d77ea96eca1154eebd2"
EXPECTED_META_AGGREGATE_HASH = "8dd58da86991885ed5448c491c3a069d60b6b590364f64d820e0811321c62954"
EXPECTED_META_PAGE_HASHES = (
    "49e7f8ba1157fa6b019420e125f746ed31f57823e338fbd7067b64d715561b92",
    "4aa45f9d4e63e6d5cce004b6f24e97fe54947990402c3dc970018c69fd43c281",
)
EXPECTED_POSITIONS_RESPONSE_SHA256 = "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570"

EXPECTED_ATTEMPT_BUNDLES = (
    "CR1_MSFT_NEWS_REFRESH",
    "CR1_MSFT_NEWS_REFRESH",
    "CR2_META_NEWS_REFRESH",
    "CR2_META_NEWS_REFRESH",
    "CR3_CURRENT_PAPER_POSITIONS",
    "CR4_CURRENT_PORTFOLIO_EQUITY",
)
EXPECTED_SNAPSHOT_KEYS = (
    ("CR1_MSFT_NEWS_REFRESH", 1, EXPECTED_MSFT_PAGE_HASHES[0]),
    ("CR1_MSFT_NEWS_REFRESH", 2, EXPECTED_MSFT_PAGE_HASHES[1]),
    ("CR2_META_NEWS_REFRESH", 1, EXPECTED_META_PAGE_HASHES[0]),
    ("CR2_META_NEWS_REFRESH", 2, EXPECTED_META_PAGE_HASHES[1]),
    ("CR3_CURRENT_PAPER_POSITIONS", 1, EXPECTED_POSITIONS_RESPONSE_SHA256),
)
EXPECTED_EVENT_TYPES = (
    "PROVIDER_DISPATCH_ATTEMPT", "PROVIDER_RAW_RESPONSE_SNAPSHOT", "PROVIDER_RESPONSE_RECEIPT",
    "PROVIDER_DISPATCH_ATTEMPT", "PROVIDER_RAW_RESPONSE_SNAPSHOT", "PROVIDER_RESPONSE_RECEIPT",
    "PROVIDER_DISPATCH_ATTEMPT", "PROVIDER_RAW_RESPONSE_SNAPSHOT", "PROVIDER_RESPONSE_RECEIPT",
    "PROVIDER_DISPATCH_ATTEMPT", "PROVIDER_RAW_RESPONSE_SNAPSHOT", "PROVIDER_RESPONSE_RECEIPT",
    "PROVIDER_DISPATCH_ATTEMPT", "PROVIDER_RAW_RESPONSE_SNAPSHOT", "PROVIDER_RESPONSE_RECEIPT",
    "PROVIDER_DISPATCH_ATTEMPT", "BUNDLE_FAILURE",
)

FROZEN_PORTFOLIO_TIMEFRAME_INVALID = "1Day"
PORTFOLIO_TIMEFRAME_REPAIR_CANDIDATE = "1D"
FUTURE_BUNDLE_IDS = (
    "RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR",
    "RR2_DYNAMIC_MARKET_CONTEXT",
    "RR3_NVDA_NEWS_CONTINUATION",
)
FUTURE_PROVIDER_DISPATCH_ATTEMPTS_MAX = 6


class WireRepairV02FailureReconciliationError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise WireRepairV02FailureReconciliationError(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(
        isinstance(observed, str)
        and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        f"{field} missing",
    )
    _need(
        observed == canonical_sha256(payload, exclude_fields=(field,)),
        f"{field} self-hash mismatch",
    )
    return observed


def _event_hash(payload: Mapping[str, Any]) -> str:
    observed = payload.get("event_hash")
    _need(
        isinstance(observed, str)
        and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        "journal event_hash missing",
    )
    _need(
        observed == canonical_sha256(payload, exclude_fields=("event_hash",)),
        "journal event self-hash mismatch",
    )
    return observed


def read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WireRepairV02FailureReconciliationError(f"unable to read {label}") from exc
    _need(isinstance(payload, dict), f"{label} root must be object")
    return payload


def read_journal(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise WireRepairV02FailureReconciliationError("unable to read V02 journal") from exc
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WireRepairV02FailureReconciliationError("V02 journal contains invalid JSON") from exc
        _need(isinstance(row, dict), "V02 journal row must be object")
        _event_hash(row)
        rows.append(row)
    _need(bool(rows), "V02 journal is empty")
    return rows


def _portfolio_contract_from_original_preflight(
    original_preflight: Mapping[str, Any],
) -> Mapping[str, Any]:
    rows = original_preflight.get("request_preflights")
    _need(isinstance(rows, list), "original preflight request_preflights missing")
    matches = [
        row for row in rows
        if isinstance(row, Mapping)
        and row.get("bundle_id") == "ER5_CURRENT_PORTFOLIO_EQUITY"
    ]
    _need(len(matches) == 1, "original ER5 portfolio request row missing or duplicated")
    contract = matches[0].get("resolved_request_contract")
    _need(isinstance(contract, Mapping), "original ER5 resolved request contract missing")
    return contract


def _verify_original_portfolio_contract(original_preflight: Mapping[str, Any]) -> None:
    observed = _self_hash(original_preflight)
    _need(observed == EXPECTED_ORIGINAL_PREFLIGHT_HASH, "original preflight hash drift")
    contract = _portfolio_contract_from_original_preflight(original_preflight)
    _need(contract.get("cli_command") == ["alpaca", "account", "portfolio"], "ER5 CLI command drift")
    _need(contract.get("timeframe") == FROZEN_PORTFOLIO_TIMEFRAME_INVALID, "ER5 frozen timeframe drift")
    _need(contract.get("intraday_reporting") == "market_hours", "ER5 intraday_reporting drift")
    _need(contract.get("start_utc") == "2026-08-24T08:58:17Z", "ER5 start drift")
    _need(contract.get("end_utc") == EXPECTED_REOPEN_CUTOFF_UTC, "ER5 end drift")


def _verify_authorization(authorization: Mapping[str, Any]) -> str:
    auth_hash = _self_hash(authorization)
    _need(auth_hash == EXPECTED_AUTH_HASH, "V02 authorization hash drift")
    exact = {
        "status": "AUTHORIZED_EXACTLY_ONE_BOUNDED_CONTINUATION_WIRE_REPAIR_PROVIDER_READ_PASS",
        "code_commit_sha": EXPECTED_RUNTIME_CODE_SHA,
        "source_runner_dry_hash": EXPECTED_DRY_HASH,
        "source_failure_reconciliation_hash": EXPECTED_FAILURE_RECONCILIATION_HASH,
        "source_continuation_preflight_hash": EXPECTED_CONTINUATION_PREFLIGHT_HASH,
        "continuation_request_manifest_hash": EXPECTED_CONTINUATION_MANIFEST_HASH,
        "source_original_preflight_hash": EXPECTED_ORIGINAL_PREFLIGHT_HASH,
        "source_original_result_hash": EXPECTED_ORIGINAL_RESULT_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "provider_dispatch_attempts_max": 11,
        "owner_approval_id": EXPECTED_OWNER_APPROVAL_ID,
        "owner_approval_at_utc": EXPECTED_OWNER_APPROVAL_AT_UTC,
        "raw_response_snapshot_before_parse": True,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "broker_writes_authorized": False,
        "alpaca_orders_authorized": False,
        "live_money": "PROHIBITED",
    }
    for key, expected in exact.items():
        _need(authorization.get(key) == expected, f"V02 authorization drift: {key}")
    return auth_hash


def _verify_news_bundle(
    row: Mapping[str, Any],
    *,
    bundle_id: str,
    symbol: str,
    response_hash: str,
    aggregate_hash: str,
    page_hashes: Sequence[str],
    article_count: int,
) -> None:
    _need(row.get("bundle_id") == bundle_id, f"{bundle_id} identity drift")
    _need(row.get("status") == "PASS", f"{bundle_id} status drift")
    _need(row.get("provider_dispatch_attempts") == 2, f"{bundle_id} dispatch drift")
    _need(row.get("article_count") == article_count, f"{bundle_id} article count drift")
    _need(row.get("pagination_complete") is True, f"{bundle_id} pagination incomplete")
    _need(row.get("terminal_next_page_token") is None, f"{bundle_id} terminal token drift")
    _need(row.get("response_artifact_hash") == response_hash, f"{bundle_id} artifact hash drift")
    response = row.get("response_artifact")
    _need(isinstance(response, Mapping), f"{bundle_id} response artifact missing")
    _need(canonical_sha256(response) == response_hash, f"{bundle_id} response artifact rehash mismatch")
    try:
        typed = AlpacaNewsReopenRead.model_validate(response)
    except Exception as exc:
        raise WireRepairV02FailureReconciliationError(f"{bundle_id} typed validation failed") from exc
    _need(typed.symbol == symbol, f"{bundle_id} symbol drift")
    _need(typed.aggregate_payload_hash == aggregate_hash, f"{bundle_id} aggregate hash drift")
    _need(typed.page_count == 2 and typed.max_pages == 2 and typed.page_size == 5, f"{bundle_id} page contract drift")
    _need(tuple(typed.page_raw_payload_hashes) == tuple(page_hashes), f"{bundle_id} page hashes drift")
    _need(len(typed.articles) == article_count, f"{bundle_id} typed article count drift")
    _need(typed.pagination_complete is True and typed.terminal_next_page_token is None, f"{bundle_id} typed terminal drift")


def _verify_result(result: Mapping[str, Any], *, authorization_hash: str) -> str:
    result_hash = _self_hash(result)
    _need(result_hash == EXPECTED_RESULT_HASH, "V02 result hash drift")
    exact = {
        "status": "B3_RESEARCH_REOPEN_CONTINUATION_WIRE_REPAIR_BLOCKED",
        "authorization_artifact_hash": authorization_hash,
        "source_failure_reconciliation_hash": EXPECTED_FAILURE_RECONCILIATION_HASH,
        "source_continuation_preflight_hash": EXPECTED_CONTINUATION_PREFLIGHT_HASH,
        "source_original_preflight_hash": EXPECTED_ORIGINAL_PREFLIGHT_HASH,
        "source_original_result_hash": EXPECTED_ORIGINAL_RESULT_HASH,
        "continuation_request_manifest_hash": EXPECTED_CONTINUATION_MANIFEST_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "provider_dispatch_attempts": 6,
        "provider_dispatch_attempts_max": 11,
        "raw_response_snapshot_count": 5,
        "raw_response_snapshot_before_parse": True,
        "failed_bundle_id": "CR4_CURRENT_PORTFOLIO_EQUITY",
        "failure_reason": EXPECTED_FAILURE_REASON,
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "model_calls": 0,
        "model_synthesis_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": "ZERO_CALL_DURABLE_CONTINUATION_WIRE_REPAIR_PROVIDER_READ_FAILURE_RECONCILIATION",
    }
    for key, expected in exact.items():
        _need(result.get(key) == expected, f"V02 result drift: {key}")

    bundles = result.get("bundle_results")
    _need(isinstance(bundles, list) and len(bundles) == 3, "V02 completed bundle count drift")
    _verify_news_bundle(
        bundles[0],
        bundle_id="CR1_MSFT_NEWS_REFRESH",
        symbol="MSFT",
        response_hash=EXPECTED_MSFT_RESPONSE_ARTIFACT_HASH,
        aggregate_hash=EXPECTED_MSFT_AGGREGATE_HASH,
        page_hashes=EXPECTED_MSFT_PAGE_HASHES,
        article_count=8,
    )
    _verify_news_bundle(
        bundles[1],
        bundle_id="CR2_META_NEWS_REFRESH",
        symbol="META",
        response_hash=EXPECTED_META_RESPONSE_ARTIFACT_HASH,
        aggregate_hash=EXPECTED_META_AGGREGATE_HASH,
        page_hashes=EXPECTED_META_PAGE_HASHES,
        article_count=6,
    )
    positions = bundles[2]
    _need(positions.get("bundle_id") == "CR3_CURRENT_PAPER_POSITIONS", "CR3 identity drift")
    _need(positions.get("status") == "PASS", "CR3 status drift")
    _need(positions.get("provider_dispatch_attempts") == 1, "CR3 dispatch drift")
    _need(positions.get("response_sha256") == EXPECTED_POSITIONS_RESPONSE_SHA256, "CR3 response SHA drift")
    _need(positions.get("equity_position_symbols") == [], "CR3 equity positions must be empty")
    _need(positions.get("response_payload") == [], "CR3 response payload must be empty")
    return result_hash


def _verify_journal_and_raw(
    journal_rows: Sequence[Mapping[str, Any]],
    *,
    raw_dir: Path,
    authorization_hash: str,
) -> None:
    _need(len(journal_rows) == len(EXPECTED_EVENT_TYPES), "V02 journal event count drift")
    _need(tuple(row.get("event_type") for row in journal_rows) == EXPECTED_EVENT_TYPES, "V02 journal event order drift")
    _need(all(row.get("authorization_artifact_hash") == authorization_hash for row in journal_rows), "journal authorization lineage drift")

    attempts = [row for row in journal_rows if row.get("event_type") == "PROVIDER_DISPATCH_ATTEMPT"]
    snapshots = [row for row in journal_rows if row.get("event_type") == "PROVIDER_RAW_RESPONSE_SNAPSHOT"]
    receipts = [row for row in journal_rows if row.get("event_type") == "PROVIDER_RESPONSE_RECEIPT"]
    bindings = [row for row in journal_rows if row.get("event_type") == "DYNAMIC_REQUEST_BINDING"]
    failures = [row for row in journal_rows if row.get("event_type") == "BUNDLE_FAILURE"]

    _need(len(attempts) == 6, "expected six V02 provider dispatch attempts")
    _need(len(snapshots) == 5, "expected five V02 raw snapshots")
    _need(len(receipts) == 5, "expected five V02 response receipts")
    _need(len(bindings) == 0, "dynamic binding unexpectedly occurred before CR4 failure")
    _need(len(failures) == 1, "expected one V02 bundle failure")
    _need(tuple(row.get("bundle_id") for row in attempts) == EXPECTED_ATTEMPT_BUNDLES, "V02 dispatch bundle sequence drift")
    _need(attempts[0].get("event_hash") == EXPECTED_FIRST_DISPATCH_HASH, "V02 first dispatch hash drift")
    _need(attempts[-1].get("event_hash") == EXPECTED_LAST_DISPATCH_HASH, "V02 last dispatch hash drift")
    _need(failures[0].get("bundle_id") == "CR4_CURRENT_PORTFOLIO_EQUITY", "V02 failure bundle drift")
    _need(failures[0].get("reason") == EXPECTED_FAILURE_REASON, "V02 failure reason drift")

    observed_snapshot_keys = tuple(
        (row.get("bundle_id"), row.get("dispatch_index_within_bundle"), row.get("response_sha256"))
        for row in snapshots
    )
    _need(observed_snapshot_keys == EXPECTED_SNAPSHOT_KEYS, "V02 raw snapshot sequence/hash drift")
    observed_receipt_keys = tuple(
        (row.get("bundle_id"), row.get("dispatch_index_within_bundle"), row.get("response_sha256"))
        for row in receipts
    )
    _need(observed_receipt_keys == EXPECTED_SNAPSHOT_KEYS, "V02 response receipt sequence/hash drift")

    positions_by_key = {
        (row.get("bundle_id"), row.get("dispatch_index_within_bundle"), row.get("response_sha256")): index
        for index, row in enumerate(journal_rows)
        if row.get("event_type") in {"PROVIDER_RAW_RESPONSE_SNAPSHOT", "PROVIDER_RESPONSE_RECEIPT"}
    }
    for key in EXPECTED_SNAPSHOT_KEYS:
        snapshot_row = next(
            row for row in snapshots
            if (row.get("bundle_id"), row.get("dispatch_index_within_bundle"), row.get("response_sha256")) == key
        )
        filename = snapshot_row.get("raw_snapshot_file")
        _need(isinstance(filename, str) and Path(filename).name == filename, "raw snapshot filename unsafe")
        path = raw_dir / filename
        _need(path.is_file(), f"raw snapshot missing: {filename}")
        raw = path.read_bytes()
        _need(bool(raw), f"raw snapshot empty: {filename}")
        _need(hashlib.sha256(raw).hexdigest() == key[2], f"raw snapshot SHA mismatch: {filename}")
        _need(snapshot_row.get("response_bytes") == len(raw), f"raw snapshot byte count drift: {filename}")
        snapshot_pos = positions_by_key[key]
        receipt_pos = next(
            i for i, row in enumerate(journal_rows)
            if row.get("event_type") == "PROVIDER_RESPONSE_RECEIPT"
            and (row.get("bundle_id"), row.get("dispatch_index_within_bundle"), row.get("response_sha256")) == key
        )
        _need(snapshot_pos < receipt_pos, f"raw snapshot must precede receipt: {filename}")

    raw_files = [path for path in raw_dir.iterdir() if path.is_file()]
    _need(len(raw_files) == 5, "raw response directory file count drift")
    _need(
        {hashlib.sha256(path.read_bytes()).hexdigest() for path in raw_files}
        == {key[2] for key in EXPECTED_SNAPSHOT_KEYS},
        "raw response directory hash set drift",
    )
    _need(
        all(row.get("bundle_id") != "CR4_CURRENT_PORTFOLIO_EQUITY" for row in snapshots + receipts),
        "CR4 unexpectedly has a durable provider response",
    )


def build_reconciliation(
    *,
    authorization: Mapping[str, Any],
    result: Mapping[str, Any],
    original_preflight: Mapping[str, Any],
    journal_rows: Sequence[Mapping[str, Any]],
    raw_dir: Path,
    code_commit_sha: str,
) -> dict[str, Any]:
    _need(
        isinstance(code_commit_sha, str)
        and re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None,
        "exact code SHA required",
    )
    _verify_original_portfolio_contract(original_preflight)
    auth_hash = _verify_authorization(authorization)
    result_hash = _verify_result(result, authorization_hash=auth_hash)
    _verify_journal_and_raw(journal_rows, raw_dir=raw_dir, authorization_hash=auth_hash)

    artifact: dict[str, Any] = {
        "artifact_version": VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_authorization_artifact_hash": auth_hash,
        "source_result_artifact_hash": result_hash,
        "source_runtime_code_commit_sha": EXPECTED_RUNTIME_CODE_SHA,
        "source_failure_reconciliation_hash": EXPECTED_FAILURE_RECONCILIATION_HASH,
        "source_continuation_preflight_hash": EXPECTED_CONTINUATION_PREFLIGHT_HASH,
        "source_continuation_request_manifest_hash": EXPECTED_CONTINUATION_MANIFEST_HASH,
        "source_original_preflight_hash": EXPECTED_ORIGINAL_PREFLIGHT_HASH,
        "source_original_result_hash": EXPECTED_ORIGINAL_RESULT_HASH,
        "source_runner_dry_hash": EXPECTED_DRY_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "authority_consumed": True,
        "authority_reusable": False,
        "production_rerun_allowed": False,
        "provider_dispatch_attempts_observed": 6,
        "provider_response_receipts_observed": 5,
        "raw_response_snapshots_observed": 5,
        "raw_response_snapshot_integrity": "PASS",
        "raw_snapshot_before_response_receipt": True,
        "first_dispatch_event_hash": EXPECTED_FIRST_DISPATCH_HASH,
        "last_dispatch_event_hash": EXPECTED_LAST_DISPATCH_HASH,
        "completed_bundle_count": 3,
        "completed_bundle_ids": [
            "CR1_MSFT_NEWS_REFRESH",
            "CR2_META_NEWS_REFRESH",
            "CR3_CURRENT_PAPER_POSITIONS",
        ],
        "completed_bundle_reread_allowed": False,
        "msft_news_reread_allowed": False,
        "meta_news_reread_allowed": False,
        "positions_reread_allowed": False,
        "msft_news_response_artifact_hash": EXPECTED_MSFT_RESPONSE_ARTIFACT_HASH,
        "msft_news_article_count": 8,
        "msft_news_pagination_complete": True,
        "meta_news_response_artifact_hash": EXPECTED_META_RESPONSE_ARTIFACT_HASH,
        "meta_news_article_count": 6,
        "meta_news_pagination_complete": True,
        "current_paper_equity_position_symbols": [],
        "current_paper_positions_response_sha256": EXPECTED_POSITIONS_RESPONSE_SHA256,
        "failed_bundle_id": "CR4_CURRENT_PORTFOLIO_EQUITY",
        "failure_reason": EXPECTED_FAILURE_REASON,
        "cr4_provider_response_payload_durably_retained": False,
        "cr4_stderr_durably_retained": False,
        "cr4_exact_cli_failure_detail_available": False,
        "deterministic_pre_dispatch_contract_defect_present": True,
        "frozen_portfolio_timeframe_invalid": FROZEN_PORTFOLIO_TIMEFRAME_INVALID,
        "portfolio_timeframe_repair_candidate": PORTFOLIO_TIMEFRAME_REPAIR_CANDIDATE,
        "portfolio_timeframe_contract_rule": "ACCOUNT_PORTFOLIO_HISTORY_USES_1D_NOT_MARKET_DATA_1DAY",
        "portfolio_timeframe_defect_should_have_been_detected_before_provider_dispatch": True,
        "portfolio_timeframe_defect_proven_as_sole_runtime_failure_cause": False,
        "exact_cr4_stderr_unavailable_reason": "V02_NONZERO_CLI_EXIT_RAISED_BEFORE_STDERR_OR_STDOUT_DURABLE_SNAPSHOT",
        "local_cli_capability_probe_required_before_new_owner_gate": True,
        "local_cli_capability_probe_required_command": "alpaca account portfolio --help",
        "local_cli_version_probe_required": True,
        "future_cli_nonzero_stdout_stderr_snapshot_required": True,
        "undispatched_bundle_count": 2,
        "undispatched_bundle_ids": [
            "CR5_DYNAMIC_MARKET_CONTEXT",
            "CR6_NVDA_NEWS_CONTINUATION",
        ],
        "nvda_retained_pages_replay_allowed": False,
        "nvda_continuation_start_token_required": True,
        "future_provider_read_bundle_count": 3,
        "future_provider_read_bundle_ids": list(FUTURE_BUNDLE_IDS),
        "future_provider_dispatch_attempts_max": FUTURE_PROVIDER_DISPATCH_ATTEMPTS_MAX,
        "future_dispatch_ceiling_breakdown": {
            "RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR": 1,
            "RR2_DYNAMIC_MARKET_CONTEXT": 1,
            "RR3_NVDA_NEWS_CONTINUATION": 4,
        },
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
