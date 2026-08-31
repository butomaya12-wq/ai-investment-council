from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_durable_provider_read_failure_reconciliation_v01 as original_failure_v01


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_PREFLIGHT_v0_1"
PASS_STATUS = "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_PREFLIGHT_ZERO_CALL_PASS"
NEXT_GATE = "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_RUNNER_DRY_ZERO_CALL"

EXPECTED_RECONCILIATION_HASH = "07d0acc69e9f7806abf828354e7032611f72d3e960a76f6e8639994e5d857e07"
EXPECTED_RECONCILIATION_CODE_SHA = "b027d2941f35a6cdc35418db2bbf096eb051147b"
EXPECTED_RECONCILIATION_STATUS = (
    "B3_RESEARCH_REOPEN_WIRE_REPAIR_V02_FAILURE_RECONCILIATION_ZERO_CALL_PASS"
)
EXPECTED_ORIGINAL_RESULT_HASH = original_failure_v01.EXPECTED_RESULT_HASH
EXPECTED_REOPEN_CUTOFF_UTC = "2026-08-31T08:58:17Z"
EXPECTED_HISTORICAL_RESEARCH_CUTOFF_UTC = "2026-08-28T17:34:00Z"
EXPECTED_NVDA_CONTINUATION_TOKEN = (
    "MTc4ODAwODQzNDAwMDAwMDAwMHw2MTUxMDk3Mw=="
)
EXPECTED_NVDA_RETAINED_ARTICLE_COUNT = 10
EXPECTED_NVDA_RETAINED_PAGE_COUNT = 2
EXPECTED_NVDA_TOTAL_ENGINEERING_PAGE_BOUND = 6
EXPECTED_NVDA_ADDITIONAL_PAGE_BOUND = 4
UPSTREAM_CLI_REFERENCE_COMMIT = "53606273aa230a40c64b783425dcb3f4423ede30"
UPSTREAM_CLI_SOURCE_OF_TRUTH_RULE = "INSTALLED_BINARY_IS_SOURCE_OF_TRUTH"

BUNDLE_IDS = (
    "RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR",
    "RR2_DYNAMIC_MARKET_CONTEXT",
    "RR3_NVDA_NEWS_CONTINUATION",
)
PROVIDER_DISPATCH_CEILING_BY_BUNDLE = {
    "RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR": 1,
    "RR2_DYNAMIC_MARKET_CONTEXT": 1,
    "RR3_NVDA_NEWS_CONTINUATION": 4,
}
PROVIDER_DISPATCH_ATTEMPTS_MAX = sum(PROVIDER_DISPATCH_CEILING_BY_BUNDLE.values())

PORTFOLIO_REQUIRED_FLAGS = (
    "--start",
    "--end",
    "--timeframe",
    "--intraday-reporting",
    "--profile",
    "--quiet",
)
MULTI_BARS_REQUIRED_FLAGS = (
    "--symbols",
    "--start",
    "--end",
    "--timeframe",
    "--feed",
    "--sort",
    "--limit",
    "--profile",
    "--quiet",
)
NEWS_REQUIRED_FLAGS = (
    "--symbols",
    "--start",
    "--end",
    "--sort",
    "--limit",
    "--include-content",
    "--exclude-contentless",
    "--page-token",
    "--profile",
    "--quiet",
)

CREDENTIAL_ENV_KEYS = (
    "OPENAI_API_KEY",
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "ALPACA_LIVE_TRADE",
)


class CR4ToCR6RepairPreflightError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise CR4ToCR6RepairPreflightError(message)


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


def _utc(value: str, *, field: str) -> datetime:
    _need(
        isinstance(value, str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)
        is not None,
        f"{field} must be second-precision UTC Z timestamp",
    )
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise CR4ToCR6RepairPreflightError(f"{field} invalid") from exc


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def verify_failure_reconciliation(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_RECONCILIATION_HASH, "V02 reconciliation hash drift")
    exact = {
        "status": EXPECTED_RECONCILIATION_STATUS,
        "code_commit_sha": EXPECTED_RECONCILIATION_CODE_SHA,
        "authority_consumed": True,
        "authority_reusable": False,
        "production_rerun_allowed": False,
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
        "current_paper_equity_position_symbols": [],
        "failed_bundle_id": "CR4_CURRENT_PORTFOLIO_EQUITY",
        "frozen_portfolio_timeframe_invalid": "1Day",
        "portfolio_timeframe_repair_candidate": "1D",
        "portfolio_timeframe_defect_proven_as_sole_runtime_failure_cause": False,
        "local_cli_capability_probe_required_before_new_owner_gate": True,
        "future_provider_dispatch_attempts_max": 6,
        "provider_reads_this_step": 0,
        "model_calls_this_step": 0,
        "live_money": "PROHIBITED",
        "next_gate": "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_PREFLIGHT_ZERO_CALL",
    }
    for key, expected in exact.items():
        _need(payload.get(key) == expected, f"V02 reconciliation drift: {key}")
    return observed


def verify_original_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    summary = original_failure_v01.verify_result(payload)
    _need(
        summary["result_artifact_hash"] == EXPECTED_ORIGINAL_RESULT_HASH,
        "original result hash drift",
    )
    _need(
        summary["nvda_terminal_next_page_token"] == EXPECTED_NVDA_CONTINUATION_TOKEN,
        "NVDA continuation token drift",
    )
    _need(
        summary["nvda_retained_article_count"] == EXPECTED_NVDA_RETAINED_ARTICLE_COUNT,
        "NVDA retained article count drift",
    )
    return summary


def _sanitized_probe_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in CREDENTIAL_ENV_KEYS:
        env.pop(key, None)
    env["ALPACA_QUIET"] = "1"
    return env


def _run_local_probe(
    *,
    executable: str,
    args: Sequence[str],
    timeout_seconds: int = 10,
) -> bytes:
    try:
        completed = subprocess.run(
            [executable, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
            env=_sanitized_probe_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CR4ToCR6RepairPreflightError(
            f"local Alpaca CLI probe failed to start: {' '.join(args)}"
        ) from exc
    _need(
        completed.returncode == 0,
        f"local Alpaca CLI probe returned nonzero: {' '.join(args)}",
    )
    raw = bytes(completed.stdout) + bytes(completed.stderr)
    _need(bool(raw.strip()), f"local Alpaca CLI probe returned empty output: {' '.join(args)}")
    return raw


def _decode_probe(raw: bytes, *, label: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CR4ToCR6RepairPreflightError(f"{label} is not UTF-8") from exc


def _validate_help(
    *,
    text: str,
    required_flags: Sequence[str],
    label: str,
    required_fragments: Sequence[str] = (),
) -> None:
    for flag in required_flags:
        _need(flag in text, f"{label} missing required flag {flag}")
    for fragment in required_fragments:
        _need(fragment in text, f"{label} missing required fragment {fragment}")


def probe_local_alpaca_cli() -> dict[str, Any]:
    executable = shutil.which("alpaca")
    _need(executable is not None, "Alpaca CLI executable is unavailable")
    resolved = Path(executable).resolve()
    _need(resolved.is_file(), "resolved Alpaca CLI executable is not a file")
    binary = resolved.read_bytes()
    _need(bool(binary), "Alpaca CLI executable is empty")

    version_raw = _run_local_probe(executable=str(resolved), args=("version",))
    portfolio_raw = _run_local_probe(
        executable=str(resolved), args=("account", "portfolio", "--help")
    )
    multi_bars_raw = _run_local_probe(
        executable=str(resolved), args=("data", "multi-bars", "--help")
    )
    news_raw = _run_local_probe(
        executable=str(resolved), args=("data", "news", "--help")
    )

    version_text = _decode_probe(version_raw, label="Alpaca version output")
    portfolio_text = _decode_probe(portfolio_raw, label="portfolio help")
    multi_bars_text = _decode_probe(multi_bars_raw, label="multi-bars help")
    news_text = _decode_probe(news_raw, label="news help")

    _need("alpaca" in version_text.lower(), "Alpaca version output identity missing")
    _validate_help(
        text=portfolio_text,
        required_flags=PORTFOLIO_REQUIRED_FLAGS,
        required_fragments=("1D",),
        label="account portfolio help",
    )
    _validate_help(
        text=multi_bars_text,
        required_flags=MULTI_BARS_REQUIRED_FLAGS,
        required_fragments=("multi-bars",),
        label="data multi-bars help",
    )
    _validate_help(
        text=news_text,
        required_flags=NEWS_REQUIRED_FLAGS,
        required_fragments=("news",),
        label="data news help",
    )

    artifact = {
        "installed_cli_source_of_truth_rule": UPSTREAM_CLI_SOURCE_OF_TRUTH_RULE,
        "upstream_cli_reference_commit": UPSTREAM_CLI_REFERENCE_COMMIT,
        "alpaca_executable_path": str(resolved),
        "alpaca_binary_sha256": hashlib.sha256(binary).hexdigest(),
        "alpaca_binary_bytes": len(binary),
        "version_probe_command": ["alpaca", "version"],
        "version_output_sha256": hashlib.sha256(version_raw).hexdigest(),
        "version_output_bytes": len(version_raw),
        "version_output_first_line": version_text.strip().splitlines()[0][:200],
        "portfolio_help_probe_command": ["alpaca", "account", "portfolio", "--help"],
        "portfolio_help_sha256": hashlib.sha256(portfolio_raw).hexdigest(),
        "portfolio_help_bytes": len(portfolio_raw),
        "portfolio_required_flags": list(PORTFOLIO_REQUIRED_FLAGS),
        "portfolio_timeframe_1d_confirmed": True,
        "multi_bars_help_probe_command": ["alpaca", "data", "multi-bars", "--help"],
        "multi_bars_help_sha256": hashlib.sha256(multi_bars_raw).hexdigest(),
        "multi_bars_help_bytes": len(multi_bars_raw),
        "multi_bars_required_flags": list(MULTI_BARS_REQUIRED_FLAGS),
        "news_help_probe_command": ["alpaca", "data", "news", "--help"],
        "news_help_sha256": hashlib.sha256(news_raw).hexdigest(),
        "news_help_bytes": len(news_raw),
        "news_required_flags": list(NEWS_REQUIRED_FLAGS),
        "credentials_removed_from_probe_environment": list(CREDENTIAL_ENV_KEYS),
        "provider_reads_during_probe": 0,
        "model_calls_during_probe": 0,
    }
    artifact["capability_probe_hash"] = canonical_sha256(artifact)
    return artifact


def _request_templates(*, nvda_start_token: str) -> list[dict[str, Any]]:
    cutoff = _utc(EXPECTED_REOPEN_CUTOFF_UTC, field="reopen_cutoff_utc")
    portfolio_start = _utc_text(cutoff - timedelta(days=7))
    market_start = _utc_text(cutoff - timedelta(days=45))

    templates: list[dict[str, Any]] = [
        {
            "bundle_id": "RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR",
            "execution_order": 1,
            "provider": "ALPACA_TRADING_API",
            "auth_mode": "CLI_PROFILE:paper",
            "max_dispatch_attempts": 1,
            "request_contract": {
                "cli_command": ["alpaca", "account", "portfolio"],
                "start_utc": portfolio_start,
                "end_utc": EXPECTED_REOPEN_CUTOFF_UTC,
                "timeframe": "1D",
                "intraday_reporting": "market_hours",
                "profile": "paper",
                "quiet": True,
                "pagination_authorized": False,
                "selection_rule": "LATEST_EQUITY_DATAPOINT_TIMESTAMP_AT_OR_BEFORE_REOPEN_CUTOFF",
                "nonzero_stdout_stderr_must_be_durably_snapshotted_before_raise": True,
                "live_profile_forbidden": True,
            },
        },
        {
            "bundle_id": "RR2_DYNAMIC_MARKET_CONTEXT",
            "execution_order": 2,
            "provider": "ALPACA_MARKET_DATA",
            "auth_mode": "CLI_PROFILE:paper",
            "max_dispatch_attempts": 1,
            "request_contract": {
                "cli_command": ["alpaca", "data", "multi-bars"],
                "symbols": ["MSFT", "META"],
                "position_symbol_source": "FROZEN_CR3_EMPTY_EQUITY_POSITIONS",
                "start_utc": market_start,
                "end_utc": EXPECTED_REOPEN_CUTOFF_UTC,
                "timeframe": "1Hour",
                "feed": "iex",
                "sort": "asc",
                "limit": 1000,
                "max_pages": 1,
                "next_page_token_must_be_null": True,
                "automatic_pagination_continuation": False,
                "profile": "paper",
                "quiet": True,
                "nonzero_stdout_stderr_must_be_durably_snapshotted_before_raise": True,
            },
        },
        {
            "bundle_id": "RR3_NVDA_NEWS_CONTINUATION",
            "execution_order": 3,
            "provider": "ALPACA_MARKET_DATA_NEWS",
            "auth_mode": "CLI_PROFILE:paper",
            "max_dispatch_attempts": EXPECTED_NVDA_ADDITIONAL_PAGE_BOUND,
            "request_contract": {
                "cli_command": ["alpaca", "data", "news"],
                "symbol": "NVDA",
                "window_start_utc": EXPECTED_HISTORICAL_RESEARCH_CUTOFF_UTC,
                "window_end_utc": EXPECTED_REOPEN_CUTOFF_UTC,
                "sort": "desc",
                "page_size": 5,
                "starting_page_token": nvda_start_token,
                "starting_page_token_sha256": hashlib.sha256(
                    nvda_start_token.encode("utf-8")
                ).hexdigest(),
                "retained_pages_before_continuation": EXPECTED_NVDA_RETAINED_PAGE_COUNT,
                "max_additional_pages": EXPECTED_NVDA_ADDITIONAL_PAGE_BOUND,
                "max_total_pages_after_continuation": EXPECTED_NVDA_TOTAL_ENGINEERING_PAGE_BOUND,
                "retained_pages_replay_allowed": False,
                "include_content": True,
                "exclude_contentless": False,
                "duplicate_article_id_rule": "SAME_CONTENT_HASH_DEDUPE; CHANGED_CONTENT_FAIL_CLOSED",
                "profile": "paper",
                "quiet": True,
                "nonzero_stdout_stderr_must_be_durably_snapshotted_before_raise": True,
            },
        },
    ]
    for template in templates:
        template["request_template_hash"] = canonical_sha256(template)
    return templates


def build_preflight(
    *,
    reconciliation: Mapping[str, Any],
    original_result: Mapping[str, Any],
    code_commit_sha: str,
    capability_probe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _need(
        isinstance(code_commit_sha, str)
        and re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None,
        "exact preflight code SHA required",
    )
    reconciliation_hash = verify_failure_reconciliation(reconciliation)
    original_summary = verify_original_result(original_result)

    probe = dict(capability_probe or probe_local_alpaca_cli())
    observed_probe_hash = probe.get("capability_probe_hash")
    _need(
        isinstance(observed_probe_hash, str)
        and observed_probe_hash == canonical_sha256(probe, exclude_fields=("capability_probe_hash",)),
        "local CLI capability probe hash mismatch",
    )
    _need(probe.get("provider_reads_during_probe") == 0, "CLI probe provider-read drift")
    _need(probe.get("model_calls_during_probe") == 0, "CLI probe model-call drift")
    _need(probe.get("portfolio_timeframe_1d_confirmed") is True, "portfolio 1D capability not confirmed")

    templates = _request_templates(
        nvda_start_token=original_summary["nvda_terminal_next_page_token"]
    )
    _need(tuple(row["bundle_id"] for row in templates) == BUNDLE_IDS, "repair bundle identity/order drift")
    _need(
        sum(int(row["max_dispatch_attempts"]) for row in templates)
        == PROVIDER_DISPATCH_ATTEMPTS_MAX,
        "repair dispatch ceiling arithmetic drift",
    )

    request_manifest_hash = canonical_sha256(
        {
            "source_reconciliation_hash": reconciliation_hash,
            "source_original_result_hash": original_summary["result_artifact_hash"],
            "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
            "capability_probe_hash": observed_probe_hash,
            "alpaca_binary_sha256": probe["alpaca_binary_sha256"],
            "request_template_hashes": [row["request_template_hash"] for row in templates],
        }
    )

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_wire_repair_v02_failure_reconciliation_hash": reconciliation_hash,
        "source_original_provider_result_hash": original_summary["result_artifact_hash"],
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "reopen_cutoff_immutable": True,
        "completed_prior_bundle_rereads_allowed": False,
        "msft_news_reread_allowed": False,
        "meta_news_reread_allowed": False,
        "positions_reread_allowed": False,
        "frozen_current_equity_position_symbols": [],
        "local_cli_capability_probe": probe,
        "local_cli_capability_probe_hash": observed_probe_hash,
        "installed_cli_binary_bound_before_owner_gate": True,
        "remaining_provider_read_bundle_count": len(BUNDLE_IDS),
        "remaining_provider_read_bundle_ids": list(BUNDLE_IDS),
        "provider_dispatch_ceiling_by_bundle": dict(PROVIDER_DISPATCH_CEILING_BY_BUNDLE),
        "provider_dispatch_attempts_max": PROVIDER_DISPATCH_ATTEMPTS_MAX,
        "provider_response_reads_max": PROVIDER_DISPATCH_ATTEMPTS_MAX,
        "request_templates": templates,
        "request_manifest_hash": request_manifest_hash,
        "nvda_retained_page_count": EXPECTED_NVDA_RETAINED_PAGE_COUNT,
        "nvda_retained_article_count": EXPECTED_NVDA_RETAINED_ARTICLE_COUNT,
        "nvda_starting_page_token": original_summary["nvda_terminal_next_page_token"],
        "nvda_retained_pages_replay_allowed": False,
        "nvda_max_additional_pages": EXPECTED_NVDA_ADDITIONAL_PAGE_BOUND,
        "nvda_total_page_engineering_bound": EXPECTED_NVDA_TOTAL_ENGINEERING_PAGE_BOUND,
        "bundle_failure_policy": "DURABLY_RECORD_FAILURE_AND_CONTINUE_ONLY_ALREADY_FROZEN_INDEPENDENT_READ_BUNDLES",
        "all_remaining_bundles_independent_after_frozen_empty_positions": True,
        "any_bundle_failure_blocks_pass_status_and_downstream_reconciliation": True,
        "automatic_retries": 0,
        "conditional_followup_reads_authorized": False,
        "unplanned_provider_reads_authorized": False,
        "pagination_beyond_frozen_bounds_authorized": False,
        "nonzero_cli_stdout_stderr_snapshot_before_raise_required": True,
        "raw_response_snapshot_before_parse_required": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "owner_approval_required_before_provider_read": True,
        "owner_provider_read_approval_present": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "judge_rerun_authorized": False,
        "rebuttal_rerun_authorized": False,
        "broad_b3_rerun_authorized": False,
        "execution_authority": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "provider_reads": 0,
        "model_calls": 0,
        "model_synthesis_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd": "0",
        "live_money": "PROHIBITED",
        "post_read_rule": "FREEZE_ALL_RECEIPTS_AND_RAW_SNAPSHOTS_THEN_RUN_ZERO_CALL_EVIDENCE_RECONCILIATION",
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_preflight(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload)
    exact = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": expected_code_commit_sha,
        "source_wire_repair_v02_failure_reconciliation_hash": EXPECTED_RECONCILIATION_HASH,
        "source_original_provider_result_hash": EXPECTED_ORIGINAL_RESULT_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "completed_prior_bundle_rereads_allowed": False,
        "msft_news_reread_allowed": False,
        "meta_news_reread_allowed": False,
        "positions_reread_allowed": False,
        "frozen_current_equity_position_symbols": [],
        "remaining_provider_read_bundle_count": len(BUNDLE_IDS),
        "remaining_provider_read_bundle_ids": list(BUNDLE_IDS),
        "provider_dispatch_ceiling_by_bundle": dict(PROVIDER_DISPATCH_CEILING_BY_BUNDLE),
        "provider_dispatch_attempts_max": PROVIDER_DISPATCH_ATTEMPTS_MAX,
        "provider_response_reads_max": PROVIDER_DISPATCH_ATTEMPTS_MAX,
        "nvda_retained_pages_replay_allowed": False,
        "nvda_max_additional_pages": EXPECTED_NVDA_ADDITIONAL_PAGE_BOUND,
        "nvda_total_page_engineering_bound": EXPECTED_NVDA_TOTAL_ENGINEERING_PAGE_BOUND,
        "all_remaining_bundles_independent_after_frozen_empty_positions": True,
        "any_bundle_failure_blocks_pass_status_and_downstream_reconciliation": True,
        "automatic_retries": 0,
        "conditional_followup_reads_authorized": False,
        "unplanned_provider_reads_authorized": False,
        "pagination_beyond_frozen_bounds_authorized": False,
        "nonzero_cli_stdout_stderr_snapshot_before_raise_required": True,
        "raw_response_snapshot_before_parse_required": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "owner_approval_required_before_provider_read": True,
        "owner_provider_read_approval_present": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "execution_authority": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "provider_reads": 0,
        "model_calls": 0,
        "model_synthesis_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd": "0",
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
    }
    for key, expected in exact.items():
        _need(payload.get(key) == expected, f"repair preflight drift: {key}")

    probe = payload.get("local_cli_capability_probe")
    _need(isinstance(probe, Mapping), "repair preflight capability probe missing")
    probe_hash = payload.get("local_cli_capability_probe_hash")
    _need(
        probe_hash == probe.get("capability_probe_hash")
        == canonical_sha256(probe, exclude_fields=("capability_probe_hash",)),
        "repair preflight capability probe hash drift",
    )
    _need(probe.get("portfolio_timeframe_1d_confirmed") is True, "portfolio 1D capability drift")
    _need(probe.get("provider_reads_during_probe") == 0, "capability provider read drift")
    _need(probe.get("model_calls_during_probe") == 0, "capability model call drift")

    templates = payload.get("request_templates")
    _need(isinstance(templates, list) and len(templates) == len(BUNDLE_IDS), "repair request template count drift")
    _need(tuple(row.get("bundle_id") for row in templates if isinstance(row, Mapping)) == BUNDLE_IDS, "repair request template identity drift")
    for row in templates:
        _need(isinstance(row, Mapping), "repair request template malformed")
        _need(row.get("request_template_hash") == canonical_sha256(row, exclude_fields=("request_template_hash",)), "repair request template hash drift")

    expected_manifest = canonical_sha256(
        {
            "source_reconciliation_hash": EXPECTED_RECONCILIATION_HASH,
            "source_original_result_hash": EXPECTED_ORIGINAL_RESULT_HASH,
            "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
            "capability_probe_hash": probe_hash,
            "alpaca_binary_sha256": probe["alpaca_binary_sha256"],
            "request_template_hashes": [row["request_template_hash"] for row in templates],
        }
    )
    _need(payload.get("request_manifest_hash") == expected_manifest, "repair request manifest hash drift")
    return observed
