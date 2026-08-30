from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B3_REOPEN_MINIMAL_EXTERNAL_READ_PREFLIGHT_v0_1"
PASS_STATUS = "B3_REOPEN_MINIMAL_EXTERNAL_READ_PREFLIGHT_ZERO_CALL_PASS"
EXPECTED_PRIMITIVES_STATUS = "B3_REOPEN_LOCAL_VALUATION_AND_PORTFOLIO_PRIMITIVES_ZERO_CALL_PASS"
EXPECTED_PRIMITIVES_HASH = "64c76249a36d650c79e95c80720061f3cbe48be900c6d1cdab2fda44240a5ee7"
EXPECTED_EVIDENCE_PLAN_HASH = "13c6e5da3e5d2b9b2369a8998abb9285d20e91a7c86452539a623301805e4b61"
EXPECTED_SCOPE_HASH = "948d3dbd28200d94726e97e39abd7955a0aa428ece22ee7b1ad6bbec6d20ba4a"
TARGET_CANDIDATES = ("MSFT", "META")
B2_CUTOFF = datetime(2026, 8, 27, 20, 0, tzinfo=UTC)
RESEARCH_CUTOFF = datetime(2026, 8, 28, 17, 34, tzinfo=UTC)
B2_LAST_COMPLETED_BAR_TS = datetime(2026, 8, 27, 19, 59, tzinfo=UTC)
RESEARCH_LAST_COMPLETED_BAR_TS = datetime(2026, 8, 28, 17, 33, tzinfo=UTC)
MARKET_WINDOW_START = datetime(2026, 8, 27, 19, 55, tzinfo=UTC)
MARKET_WINDOW_END = RESEARCH_CUTOFF
MARKET_BAR_LIMIT = 1000
ACTIVITY_PAGE_SIZE = 100
PLANNED_PROVIDER_READS_MAX = 4

_MSFT_GAAP_EPS_RE = re.compile(
    r"Diluted earnings per share\s+\$?\s*17\.95\s+\$?\s*13\.64(?:\s+\$?\s*11\.80)?",
    re.IGNORECASE,
)
_META_GAAP_EPS_RE = re.compile(
    r"diluted earnings per share\s*\(EPS\)\s*of\s*\$23\.49\s+for the year ended December 31, 2025",
    re.IGNORECASE,
)

CLI_HELP_SPECS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "market_multi_bars",
        ("data", "multi-bars", "--help"),
        ("--symbols", "--start", "--end", "--timeframe", "--limit", "--feed", "--sort"),
    ),
    ("current_positions", ("position", "list", "--help"), ()),
    (
        "account_activities",
        ("account", "activity", "list", "--help"),
        ("--after", "--until", "--direction", "--page-size", "--page-token"),
    ),
    (
        "portfolio_history",
        ("account", "portfolio", "--help"),
        ("--start", "--end", "--timeframe", "--intraday-reporting"),
    ),
)


class MinimalExternalReadPreflightError(ValueError):
    pass


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _read_json(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MinimalExternalReadPreflightError(f"unable to read {label}") from exc
    if not isinstance(payload, dict):
        raise MinimalExternalReadPreflightError(f"{label} root must be an object")
    return payload


def _verify_hash(payload: Mapping[str, Any], *, label: str) -> str:
    observed = payload.get("artifact_hash")
    if not isinstance(observed, str) or len(observed) != 64:
        raise MinimalExternalReadPreflightError(f"{label} artifact_hash missing")
    if observed != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise MinimalExternalReadPreflightError(f"{label} self-hash mismatch")
    return observed


def _review_map(primitives: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = primitives.get("valuation_primitive_reviews")
    if not isinstance(rows, list):
        raise MinimalExternalReadPreflightError("valuation primitive reviews missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("candidate_id"), str):
            raise MinimalExternalReadPreflightError("valuation primitive review malformed")
        candidate = str(row["candidate_id"])
        if candidate in result:
            raise MinimalExternalReadPreflightError("duplicate valuation primitive candidate")
        result[candidate] = row
    if tuple(result) != TARGET_CANDIDATES:
        raise MinimalExternalReadPreflightError("valuation primitive candidate scope drift")
    return result


def _select_eps(candidate: str, review: Mapping[str, Any]) -> dict[str, Any]:
    rows = review.get("diluted_eps_candidate_fragments")
    if not isinstance(rows, list) or not rows:
        raise MinimalExternalReadPreflightError(f"{candidate} diluted-EPS fragments missing")
    pattern = _MSFT_GAAP_EPS_RE if candidate == "MSFT" else _META_GAAP_EPS_RE
    selected: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise MinimalExternalReadPreflightError(f"{candidate} diluted-EPS fragment malformed")
        fragment = row.get("fragment")
        if isinstance(fragment, str) and pattern.search(fragment):
            selected.append(row)
    if not selected:
        raise MinimalExternalReadPreflightError(f"{candidate} deterministic GAAP annual EPS selection failed")
    evidence_ids = {row.get("evidence_id") for row in selected}
    if len(evidence_ids) != 1 or None in evidence_ids:
        raise MinimalExternalReadPreflightError(f"{candidate} GAAP EPS provenance is ambiguous")
    if candidate == "MSFT":
        value = Decimal("17.95")
        fiscal_period = "FY2026"
        period_end = "2026-06-30"
    else:
        value = Decimal("23.49")
        fiscal_period = "FY2025"
        period_end = "2025-12-31"
    return {
        "candidate_id": candidate,
        "metric": "LATEST_REPORTED_ANNUAL_GAAP_DILUTED_EPS",
        "value": format(value, "f"),
        "unit": "USD_PER_DILUTED_SHARE",
        "fiscal_period": fiscal_period,
        "period_end": period_end,
        "source_evidence_id": next(iter(evidence_ids)),
        "selection_rule": "GAAP_ANNUAL_DILUTED_EPS_ONLY; REJECT_ADJUSTED_NON_GAAP_AND_IMPACT_ONLY_VALUES",
    }


def inspect_alpaca_cli_help(
    *,
    executable: str = "alpaca",
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, Any]:
    executable_path = which(executable)
    if executable_path is None:
        raise MinimalExternalReadPreflightError("Alpaca CLI executable is unavailable")
    outputs: dict[str, Any] = {}
    for name, suffix, required_flags in CLI_HELP_SPECS:
        try:
            completed = runner(
                [executable_path, *suffix],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MinimalExternalReadPreflightError(f"unable to inspect Alpaca CLI {name} help") from exc
        if completed.returncode != 0:
            raise MinimalExternalReadPreflightError(f"Alpaca CLI {name} help returned non-zero status")
        raw = bytes(completed.stdout or b"") + b"\n" + bytes(completed.stderr or b"")
        if not raw.strip():
            raise MinimalExternalReadPreflightError(f"Alpaca CLI {name} help returned empty output")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MinimalExternalReadPreflightError(f"Alpaca CLI {name} help is not UTF-8") from exc
        missing = [flag for flag in required_flags if flag not in text]
        if missing:
            raise MinimalExternalReadPreflightError(
                f"Alpaca CLI {name} help missing required flags: " + ", ".join(missing)
            )
        outputs[name] = {
            "command": ["alpaca", *suffix[:-1]],
            "help_sha256": hashlib.sha256(raw).hexdigest(),
            "required_flags": list(required_flags),
        }
    return {"alpaca_cli_path": executable_path, "cli_help_checks": outputs}


def build_minimal_external_read_preflight(
    *,
    code_commit_sha: str,
    primitives_path: str | Path,
    executable: str = "alpaca",
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise MinimalExternalReadPreflightError("code_commit_sha must be lowercase 40-char SHA")
    primitives = _read_json(primitives_path, label="local primitives artifact")
    primitives_hash = _verify_hash(primitives, label="local primitives artifact")
    if primitives_hash != EXPECTED_PRIMITIVES_HASH:
        raise MinimalExternalReadPreflightError("local primitives artifact hash drift")
    if primitives.get("status") != EXPECTED_PRIMITIVES_STATUS:
        raise MinimalExternalReadPreflightError("local primitives artifact is not PASS")
    if primitives.get("source_evidence_plan_hash") != EXPECTED_EVIDENCE_PLAN_HASH:
        raise MinimalExternalReadPreflightError("evidence-plan lineage drift")
    if primitives.get("source_remaining_gaps_scope_hash") != EXPECTED_SCOPE_HASH:
        raise MinimalExternalReadPreflightError("remaining-gap scope lineage drift")
    if primitives.get("target_candidates") != list(TARGET_CANDIDATES):
        raise MinimalExternalReadPreflightError("target candidate scope drift")
    if primitives.get("provider_reads_authorized") is not False or primitives.get("model_calls_authorized") is not False:
        raise MinimalExternalReadPreflightError("local primitives unexpectedly authorize external calls")
    if primitives.get("historical_portfolio_candidate_count") != 0:
        raise MinimalExternalReadPreflightError("historical portfolio snapshot unexpectedly became locally available")
    summary = primitives.get("external_need_summary")
    if not isinstance(summary, Mapping):
        raise MinimalExternalReadPreflightError("local primitives external need summary missing")
    if summary.get("point_in_time_market_price_read_candidates") != list(TARGET_CANDIDATES):
        raise MinimalExternalReadPreflightError("market-price read candidate scope drift")
    if summary.get("primary_filing_denominator_read_candidates") != []:
        raise MinimalExternalReadPreflightError("unexpected primary-filing read remains necessary")
    if summary.get("historical_portfolio_reconstruction_needed") is not True:
        raise MinimalExternalReadPreflightError("historical portfolio reconstruction scope drift")

    reviews = _review_map(primitives)
    denominators = [_select_eps(candidate, reviews[candidate]) for candidate in TARGET_CANDIDATES]
    cli = inspect_alpaca_cli_help(executable=executable, which=which, runner=runner)

    provider_read_plan = [
        {
            "read_id": "R1_CURRENT_POSITIONS_ANCHOR",
            "provider": "ALPACA_TRADING_API",
            "cli_command": ["alpaca", "position", "list"],
            "max_dispatch_attempts": 1,
            "purpose": "Capture current paper-account positions; response_received_at becomes reconstruction_anchor_utc.",
        },
        {
            "read_id": "R2_POST_CUTOFF_ACCOUNT_ACTIVITIES_FIRST_PAGE",
            "provider": "ALPACA_TRADING_API",
            "cli_command": ["alpaca", "account", "activity", "list"],
            "after_exclusive": _utc(B2_CUTOFF),
            "until_rule": "CURRENT_POSITIONS_RESPONSE_RECEIVED_AT_UTC",
            "direction": "asc",
            "page_size": ACTIVITY_PAGE_SIZE,
            "max_pages": 1,
            "max_dispatch_attempts": 1,
            "pagination_continuation_authorized": False,
            "completion_rule": "COUNT_LT_100; COUNT_EQ_100_IS_PARTIAL_AND_STOPS",
            "position_reconstruction_rule": "Reverse FILL quantities after cutoff; any non-FILL activity carrying symbol/qty is fail-closed for this version.",
        },
        {
            "read_id": "R3_B2_CUTOFF_PORTFOLIO_EQUITY",
            "provider": "ALPACA_TRADING_API",
            "cli_command": ["alpaca", "account", "portfolio"],
            "start": _utc(B2_CUTOFF.replace(minute=55) if False else datetime(2026, 8, 27, 19, 55, tzinfo=UTC)),
            "end": _utc(B2_CUTOFF),
            "timeframe": "1Min",
            "intraday_reporting": "market_hours",
            "max_dispatch_attempts": 1,
            "selection_rule": "Latest equity datapoint timestamp <= B2 decision cutoff.",
        },
        {
            "read_id": "R4_MSFT_META_POINT_IN_TIME_BARS",
            "provider": "ALPACA_MARKET_DATA",
            "cli_command": ["alpaca", "data", "multi-bars"],
            "symbols": list(TARGET_CANDIDATES),
            "start": _utc(MARKET_WINDOW_START),
            "end": _utc(MARKET_WINDOW_END),
            "timeframe": "1Min",
            "feed": "iex",
            "sort": "asc",
            "limit": MARKET_BAR_LIMIT,
            "max_pages": 1,
            "max_dispatch_attempts": 1,
            "pagination_continuation_authorized": False,
            "completion_rule": "next_page_token MUST be null; otherwise PARTIAL and stop.",
            "price_selection_rules": {
                "META_B2_PORTFOLIO_PRICE": "latest completed META 1Min bar timestamp <= " + _utc(B2_LAST_COMPLETED_BAR_TS),
                "MSFT_RESEARCH_VALUATION_PRICE": "latest completed MSFT 1Min bar timestamp <= " + _utc(RESEARCH_LAST_COMPLETED_BAR_TS),
                "META_RESEARCH_VALUATION_PRICE": "latest completed META 1Min bar timestamp <= " + _utc(RESEARCH_LAST_COMPLETED_BAR_TS),
            },
        },
    ]

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_local_primitives_hash": primitives_hash,
        "source_evidence_plan_hash": EXPECTED_EVIDENCE_PLAN_HASH,
        "source_remaining_gaps_scope_hash": EXPECTED_SCOPE_HASH,
        "target_candidates": list(TARGET_CANDIDATES),
        "non_target_candidate_ids": ["NVDA"],
        "valuation_metric_contract": {
            "metric": "PRICE_TO_LATEST_REPORTED_ANNUAL_DILUTED_EPS",
            "denominators": denominators,
            "price_feed": "iex",
            "price_is_point_in_time": True,
            "binary_float_forbidden": True,
        },
        "portfolio_reconstruction_contract": {
            "candidate_id": "META",
            "b2_decision_cutoff": _utc(B2_CUTOFF),
            "current_positions_are_only_a_reconstruction_anchor": True,
            "current_positions_are_not_a_cutoff_substitute": True,
            "activity_window_is_post_cutoff_only": True,
            "activity_page_size": ACTIVITY_PAGE_SIZE,
            "activity_max_pages": 1,
            "activity_pagination_continuation_authorized": False,
            "position_weight_rule": "META reconstructed quantity * META B2 cutoff price / B2 cutoff portfolio equity when all three are valid.",
            "fail_closed_on_security_affecting_non_fill_activity": True,
        },
        "provider_read_plan": provider_read_plan,
        "planned_provider_reads_max": PLANNED_PROVIDER_READS_MAX,
        "provider_reads_authorized": False,
        "owner_approval_required": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_retries": 0,
        "rerun_authorized": False,
        "next_gate": "B3_REOPEN_MINIMAL_EXTERNAL_READ_OWNER_APPROVAL",
        **cli,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
