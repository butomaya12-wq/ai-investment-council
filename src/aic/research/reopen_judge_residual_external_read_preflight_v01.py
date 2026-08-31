from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_residual_external_read_plan_v01 as plan_v01


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_PREFLIGHT_v0_1"
PASS_STATUS = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_PREFLIGHT_ZERO_CALL_PASS"
NEXT_GATE = "EXPLICIT_OWNER_B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_AUTHORIZATION_V01"

EXPECTED_PLAN_HASH = "a37196c7998c87e2e3723f58dbfb88a58e985493497e1bab3587194b70398aa3"
EXPECTED_PLAN_CODE_SHA = "241eabaf35d938f29c555883d4a67d24dafd9881"
HISTORICAL_RESEARCH_CUTOFF_UTC = plan_v01.HISTORICAL_RESEARCH_CUTOFF_UTC

EXPECTED_BUNDLE_IDS = plan_v01.BUNDLE_IDS
EXPECTED_TARGET_IDS = plan_v01.TARGET_IDS
EXPECTED_LOGICAL_BUNDLE_COUNT = plan_v01.LOGICAL_PROVIDER_READ_BUNDLE_COUNT
EXPECTED_PROVIDER_DISPATCH_MAX = plan_v01.PROVIDER_DISPATCH_ATTEMPTS_MAX
EXPECTED_NEWS_DISPATCH_MAX = plan_v01.NEWS_DISPATCH_ATTEMPTS_MAX
EXPECTED_NON_NEWS_DISPATCH_MAX = plan_v01.NON_NEWS_DISPATCH_ATTEMPTS_MAX
EXPECTED_MAX_DYNAMIC_MARKET_SYMBOLS = plan_v01.MAX_DYNAMIC_MARKET_SYMBOLS
EXPECTED_MAX_POSITION_SYMBOLS_FOR_MARKET_EXPANSION = (
    plan_v01.MAX_POSITION_SYMBOLS_FOR_MARKET_EXPANSION
)


class ResidualExternalReadPreflightError(ValueError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ResidualExternalReadPreflightError(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(
        isinstance(observed, str)
        and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        f"{field} missing",
    )
    expected = canonical_sha256(payload, exclude_fields=(field,))
    _need(observed == expected, f"{field} self-hash mismatch")
    return observed


def _parse_utc(value: str, *, field: str) -> datetime:
    _need(
        isinstance(value, str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is not None,
        f"{field} must be second-precision UTC Z timestamp",
    )
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ResidualExternalReadPreflightError(f"{field} invalid") from exc
    return parsed


def _utc_text(value: datetime) -> str:
    _need(value.tzinfo is not None, "UTC datetime must be timezone-aware")
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def verify_plan(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_PLAN_HASH, "residual external-read plan hash drift")
    plan_v01.verify_plan(payload, expected_code_commit_sha=EXPECTED_PLAN_CODE_SHA)
    _need(
        payload.get("logical_provider_read_bundle_ids") == list(EXPECTED_BUNDLE_IDS),
        "plan bundle identity/order drift",
    )
    _need(
        payload.get("provider_dispatch_attempts_max") == EXPECTED_PROVIDER_DISPATCH_MAX,
        "plan provider dispatch ceiling drift",
    )
    _need(payload.get("provider_reads_authorized") is False, "plan already authorizes provider reads")
    _need(payload.get("model_calls_authorized") is False, "plan unexpectedly authorizes model calls")
    _need(payload.get("execution_authority") is False, "plan unexpectedly grants execution authority")
    return observed


def _resolve_contract(
    *,
    bundle: Mapping[str, Any],
    reopen_cutoff: datetime,
) -> dict[str, Any]:
    contract = deepcopy(bundle["request_contract"])
    bundle_id = str(bundle["bundle_id"])

    if bundle_id in {"ER1_NVDA_NEWS_REFRESH", "ER2_MSFT_NEWS_REFRESH", "ER3_META_NEWS_REFRESH"}:
        _need(
            contract.get("window_end_rule") == "PREFLIGHT_FROZEN_REOPEN_CUTOFF_UTC",
            f"{bundle_id} news end rule drift",
        )
        contract.pop("window_end_rule")
        contract["window_end_utc"] = _utc_text(reopen_cutoff)
    elif bundle_id == "ER4_CURRENT_PAPER_POSITIONS":
        _need(
            contract.get("max_position_symbols_for_market_expansion")
            == EXPECTED_MAX_POSITION_SYMBOLS_FOR_MARKET_EXPANSION,
            "position expansion ceiling drift",
        )
    elif bundle_id == "ER5_CURRENT_PORTFOLIO_EQUITY":
        _need(
            contract.get("start_rule")
            == "PREFLIGHT_FROZEN_REOPEN_CUTOFF_UTC_MINUS_7_CALENDAR_DAYS",
            "portfolio start rule drift",
        )
        _need(
            contract.get("end_rule") == "PREFLIGHT_FROZEN_REOPEN_CUTOFF_UTC",
            "portfolio end rule drift",
        )
        contract.pop("start_rule")
        contract.pop("end_rule")
        contract["start_utc"] = _utc_text(reopen_cutoff - timedelta(days=7))
        contract["end_utc"] = _utc_text(reopen_cutoff)
    elif bundle_id == "ER6_DYNAMIC_MARKET_CONTEXT":
        _need(
            contract.get("start_rule")
            == "PREFLIGHT_FROZEN_REOPEN_CUTOFF_UTC_MINUS_45_CALENDAR_DAYS",
            "dynamic market start rule drift",
        )
        _need(
            contract.get("end_rule") == "PREFLIGHT_FROZEN_REOPEN_CUTOFF_UTC",
            "dynamic market end rule drift",
        )
        _need(
            contract.get("symbol_rule")
            == "DEDUPED_MSFT_META_PLUS_CURRENT_EQUITY_POSITION_SYMBOLS",
            "dynamic market symbol rule drift",
        )
        contract.pop("start_rule")
        contract.pop("end_rule")
        contract["start_utc"] = _utc_text(reopen_cutoff - timedelta(days=45))
        contract["end_utc"] = _utc_text(reopen_cutoff)
        contract["runtime_symbol_binding"] = {
            "source_bundle_id": "ER4_CURRENT_PAPER_POSITIONS",
            "required_symbols": ["MSFT", "META"],
            "position_symbol_filter": "EQUITY_POSITIONS_ONLY",
            "dedupe_rule": "PRESERVE_REQUIRED_SYMBOLS_THEN_SORT_ADDITIONAL_POSITION_SYMBOLS_ASC",
            "max_additional_position_symbols": EXPECTED_MAX_POSITION_SYMBOLS_FOR_MARKET_EXPANSION,
            "max_total_symbols": EXPECTED_MAX_DYNAMIC_MARKET_SYMBOLS,
            "overflow_rule": "FAIL_CLOSED_BEFORE_ER6_PROVIDER_DISPATCH",
            "final_request_hash_rule": "COMPUTE_AND_DURABLY_RECORD_AFTER_ER4_RESPONSE_BEFORE_ER6_DISPATCH",
        }
    else:
        raise ResidualExternalReadPreflightError(f"unexpected bundle: {bundle_id}")

    return contract


def _request_preflight(
    *,
    bundle: Mapping[str, Any],
    reopen_cutoff: datetime,
) -> dict[str, Any]:
    bundle_id = str(bundle["bundle_id"])
    row: dict[str, Any] = {
        "bundle_id": bundle_id,
        "execution_order": int(bundle["execution_order"]),
        "provider": bundle["provider"],
        "existing_capability": bundle["existing_capability"],
        "auth_mode": bundle["auth_mode"],
        "target_ids": list(bundle["target_ids"]),
        "max_dispatch_attempts": int(bundle["max_dispatch_attempts"]),
        "resolved_request_contract": _resolve_contract(
            bundle=bundle,
            reopen_cutoff=reopen_cutoff,
        ),
        "provider_read_authorized": False,
        "model_call_authorized": False,
        "automatic_retry_authorized": False,
    }
    if "symbol_scope" in bundle:
        row["symbol_scope"] = list(bundle["symbol_scope"])
    if "depends_on_bundle_ids" in bundle:
        row["depends_on_bundle_ids"] = list(bundle["depends_on_bundle_ids"])
    row["request_template_hash"] = canonical_sha256(row)
    return row


def build_preflight(
    *,
    plan: Mapping[str, Any],
    code_commit_sha: str,
    reopen_cutoff_utc: str,
) -> dict[str, Any]:
    _need(
        isinstance(code_commit_sha, str)
        and re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None,
        "preflight code SHA invalid",
    )
    plan_hash = verify_plan(plan)
    cutoff = _parse_utc(reopen_cutoff_utc, field="reopen_cutoff_utc")
    historical = _parse_utc(HISTORICAL_RESEARCH_CUTOFF_UTC, field="historical_research_cutoff_utc")
    _need(cutoff > historical, "reopen cutoff must be after historical research cutoff")

    bundles = plan.get("provider_read_bundles")
    _need(
        isinstance(bundles, list) and len(bundles) == EXPECTED_LOGICAL_BUNDLE_COUNT,
        "provider bundle count drift",
    )
    _need(
        tuple(row.get("bundle_id") for row in bundles if isinstance(row, Mapping))
        == EXPECTED_BUNDLE_IDS,
        "provider bundle identity/order drift",
    )

    requests = [
        _request_preflight(bundle=row, reopen_cutoff=cutoff)
        for row in bundles
        if isinstance(row, Mapping)
    ]
    _need(len(requests) == EXPECTED_LOGICAL_BUNDLE_COUNT, "request preflight count drift")
    _need(
        sum(int(row["max_dispatch_attempts"]) for row in requests)
        == EXPECTED_PROVIDER_DISPATCH_MAX,
        "request dispatch ceiling arithmetic drift",
    )
    request_manifest_hash = canonical_sha256(
        {
            "reopen_cutoff_utc": reopen_cutoff_utc,
            "request_template_hashes": [row["request_template_hash"] for row in requests],
        }
    )

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_residual_external_read_plan_hash": plan_hash,
        "source_residual_external_read_plan_code_sha": EXPECTED_PLAN_CODE_SHA,
        "source_existing_evidence_inventory_hash": plan_v01.EXPECTED_INVENTORY_HASH,
        "source_judge_result_hash": plan_v01.EXPECTED_JUDGE_HASH,
        "source_local_replay_hash": plan_v01.EXPECTED_LOCAL_REPLAY_HASH,
        "historical_research_cutoff_utc": HISTORICAL_RESEARCH_CUTOFF_UTC,
        "reopen_cutoff_utc": reopen_cutoff_utc,
        "reopen_cutoff_immutable_on_owner_approval": True,
        "residual_external_read_target_count": len(EXPECTED_TARGET_IDS),
        "residual_external_read_target_ids": list(EXPECTED_TARGET_IDS),
        "logical_provider_read_bundle_count": EXPECTED_LOGICAL_BUNDLE_COUNT,
        "logical_provider_read_bundle_ids": list(EXPECTED_BUNDLE_IDS),
        "provider_dispatch_attempts_max": EXPECTED_PROVIDER_DISPATCH_MAX,
        "provider_response_reads_max": EXPECTED_PROVIDER_DISPATCH_MAX,
        "news_dispatch_attempts_max": EXPECTED_NEWS_DISPATCH_MAX,
        "non_news_dispatch_attempts_max": EXPECTED_NON_NEWS_DISPATCH_MAX,
        "request_preflights": requests,
        "request_manifest_hash": request_manifest_hash,
        "dynamic_request_binding_rule": {
            "bundle_id": "ER6_DYNAMIC_MARKET_CONTEXT",
            "reason_exact_final_symbols_not_preflight_known": "CURRENT_PAPER_POSITIONS_ARE_PROVIDER_DATA_NOT_READ_DURING_ZERO_CALL_PREFLIGHT",
            "owner_approval_binds_template_and_runtime_binding_algorithm": True,
            "final_er6_request_hash_must_be_recorded_before_dispatch": True,
            "max_total_symbols": EXPECTED_MAX_DYNAMIC_MARKET_SYMBOLS,
            "max_additional_position_symbols": EXPECTED_MAX_POSITION_SYMBOLS_FOR_MARKET_EXPANSION,
        },
        "execution_order": list(EXPECTED_BUNDLE_IDS),
        "single_authorized_read_pass_only": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_retries": 0,
        "conditional_followup_reads_authorized": False,
        "pagination_beyond_bundle_bounds_authorized": False,
        "stop_on_bundle_error": True,
        "bundle_error_rule": "DURABLY_RECORD_FAILURE_AND_STOP_WITHOUT_RETRY_OR_CONTINUING_TO_LATER_BUNDLES",
        "post_read_rule": "FREEZE_ALL_RECEIPTS_THEN_RUN_ZERO_CALL_EVIDENCE_RECONCILIATION; REMAINING_GAPS_STOP_WITHOUT_AUTOMATIC_SECOND_ROUND",
        "owner_approval_required_before_provider_read": True,
        "owner_provider_read_approval_present": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "broad_b3_rerun_authorized": False,
        "judge_rerun_authorized": False,
        "rebuttal_rerun_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "execution_authority": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd": "0",
        "live_money": "PROHIBITED",
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
        "source_residual_external_read_plan_hash": EXPECTED_PLAN_HASH,
        "source_residual_external_read_plan_code_sha": EXPECTED_PLAN_CODE_SHA,
        "historical_research_cutoff_utc": HISTORICAL_RESEARCH_CUTOFF_UTC,
        "residual_external_read_target_count": len(EXPECTED_TARGET_IDS),
        "residual_external_read_target_ids": list(EXPECTED_TARGET_IDS),
        "logical_provider_read_bundle_count": EXPECTED_LOGICAL_BUNDLE_COUNT,
        "logical_provider_read_bundle_ids": list(EXPECTED_BUNDLE_IDS),
        "provider_dispatch_attempts_max": EXPECTED_PROVIDER_DISPATCH_MAX,
        "provider_response_reads_max": EXPECTED_PROVIDER_DISPATCH_MAX,
        "news_dispatch_attempts_max": EXPECTED_NEWS_DISPATCH_MAX,
        "non_news_dispatch_attempts_max": EXPECTED_NON_NEWS_DISPATCH_MAX,
        "execution_order": list(EXPECTED_BUNDLE_IDS),
        "single_authorized_read_pass_only": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_retries": 0,
        "conditional_followup_reads_authorized": False,
        "pagination_beyond_bundle_bounds_authorized": False,
        "stop_on_bundle_error": True,
        "owner_approval_required_before_provider_read": True,
        "owner_provider_read_approval_present": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "broad_b3_rerun_authorized": False,
        "judge_rerun_authorized": False,
        "rebuttal_rerun_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "execution_authority": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd": "0",
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
    }
    for key, expected in exact.items():
        _need(payload.get(key) == expected, f"preflight drift: {key}")

    cutoff = _parse_utc(str(payload.get("reopen_cutoff_utc")), field="reopen_cutoff_utc")
    historical = _parse_utc(HISTORICAL_RESEARCH_CUTOFF_UTC, field="historical_research_cutoff_utc")
    _need(cutoff > historical, "preflight cutoff is not after historical cutoff")

    rows = payload.get("request_preflights")
    _need(
        isinstance(rows, list) and len(rows) == EXPECTED_LOGICAL_BUNDLE_COUNT,
        "request preflight rows drift",
    )
    _need(
        tuple(row.get("bundle_id") for row in rows if isinstance(row, Mapping))
        == EXPECTED_BUNDLE_IDS,
        "request preflight order drift",
    )
    _need(
        sum(int(row.get("max_dispatch_attempts", -1)) for row in rows if isinstance(row, Mapping))
        == EXPECTED_PROVIDER_DISPATCH_MAX,
        "request preflight dispatch ceiling drift",
    )
    for row in rows:
        _need(isinstance(row, Mapping), "request preflight row malformed")
        _need(row.get("provider_read_authorized") is False, "request preflight row authorizes provider read")
        _need(row.get("model_call_authorized") is False, "request preflight row authorizes model call")
        _need(row.get("automatic_retry_authorized") is False, "request preflight row authorizes retry")
        row_hash = row.get("request_template_hash")
        _need(
            isinstance(row_hash, str)
            and row_hash
            == canonical_sha256(row, exclude_fields=("request_template_hash",)),
            f"request template hash mismatch: {row.get('bundle_id')}",
        )

    manifest_expected = canonical_sha256(
        {
            "reopen_cutoff_utc": payload["reopen_cutoff_utc"],
            "request_template_hashes": [row["request_template_hash"] for row in rows],
        }
    )
    _need(payload.get("request_manifest_hash") == manifest_expected, "request manifest hash drift")

    binding = payload.get("dynamic_request_binding_rule")
    _need(isinstance(binding, Mapping), "dynamic request binding missing")
    _need(binding.get("bundle_id") == "ER6_DYNAMIC_MARKET_CONTEXT", "dynamic binding bundle drift")
    _need(binding.get("max_total_symbols") == EXPECTED_MAX_DYNAMIC_MARKET_SYMBOLS, "dynamic symbol ceiling drift")
    _need(
        binding.get("max_additional_position_symbols")
        == EXPECTED_MAX_POSITION_SYMBOLS_FOR_MARKET_EXPANSION,
        "dynamic position-symbol ceiling drift",
    )
    _need(
        binding.get("owner_approval_binds_template_and_runtime_binding_algorithm") is True,
        "owner approval dynamic binding rule missing",
    )
    return observed
