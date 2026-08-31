from __future__ import annotations

import re
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_local_replay_v01 as local_replay_v01


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_PLAN_v0_1"
PASS_STATUS = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_PLAN_ZERO_CALL_PASS"
NEXT_GATE = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_PREFLIGHT_ZERO_CALL"

EXPECTED_LOCAL_REPLAY_HASH = "74a539f68fda0102918039f96e4a3ec28bfc5468f17fcb24400e9eecaf875c29"
EXPECTED_LOCAL_REPLAY_CODE_SHA = "f44e23acb0d1d2552d608535331a895a5e075863"
EXPECTED_INVENTORY_HASH = local_replay_v01.EXPECTED_INVENTORY_HASH
EXPECTED_JUDGE_HASH = local_replay_v01.EXPECTED_JUDGE_HASH
HISTORICAL_RESEARCH_CUTOFF_UTC = "2026-08-28T17:34:00Z"

TARGET_IDS = local_replay_v01.ALL_TARGET_IDS
BUNDLE_IDS = (
    "ER1_NVDA_NEWS_REFRESH",
    "ER2_MSFT_NEWS_REFRESH",
    "ER3_META_NEWS_REFRESH",
    "ER4_CURRENT_PAPER_POSITIONS",
    "ER5_CURRENT_PORTFOLIO_EQUITY",
    "ER6_DYNAMIC_MARKET_CONTEXT",
)

LOGICAL_PROVIDER_READ_BUNDLE_COUNT = 6
NEWS_MAX_PAGES_PER_SYMBOL = 2
NEWS_PAGE_SIZE = 5
NEWS_MAX_ARTICLES_PER_SYMBOL = NEWS_MAX_PAGES_PER_SYMBOL * NEWS_PAGE_SIZE
NEWS_DISPATCH_ATTEMPTS_MAX = 3 * NEWS_MAX_PAGES_PER_SYMBOL
NON_NEWS_DISPATCH_ATTEMPTS_MAX = 3
PROVIDER_DISPATCH_ATTEMPTS_MAX = NEWS_DISPATCH_ATTEMPTS_MAX + NON_NEWS_DISPATCH_ATTEMPTS_MAX
MAX_DYNAMIC_MARKET_SYMBOLS = 20
MAX_POSITION_SYMBOLS_FOR_MARKET_EXPANSION = MAX_DYNAMIC_MARKET_SYMBOLS - 2


class ResidualExternalReadPlanError(ValueError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ResidualExternalReadPlanError(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(
        isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        f"{field} missing",
    )
    expected = canonical_sha256(payload, exclude_fields=(field,))
    _need(observed == expected, f"{field} self-hash mismatch")
    return observed


def verify_local_replay(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_LOCAL_REPLAY_HASH, "local replay hash drift")
    local_replay_v01.verify_local_replay(
        payload,
        expected_code_commit_sha=EXPECTED_LOCAL_REPLAY_CODE_SHA,
    )
    _need(payload.get("source_existing_evidence_inventory_hash") == EXPECTED_INVENTORY_HASH, "local replay inventory lineage drift")
    _need(payload.get("source_judge_result_hash") == EXPECTED_JUDGE_HASH, "local replay Judge lineage drift")
    _need(payload.get("residual_external_read_target_count") == 7, "local replay residual target count drift")
    _need(payload.get("residual_external_read_target_ids") == list(TARGET_IDS), "local replay residual target identity drift")
    _need(payload.get("local_replay_resolved_target_count") == 0, "local replay unexpectedly resolved a target")
    _need(payload.get("provider_reads_authorized") is False, "local replay unexpectedly authorizes provider reads")
    _need(payload.get("model_calls_authorized") is False, "local replay unexpectedly authorizes model calls")
    return observed


def _news_bundle(*, bundle_id: str, symbol: str, target_ids: list[str], order: int) -> dict[str, Any]:
    return {
        "bundle_id": bundle_id,
        "execution_order": order,
        "provider": "ALPACA_MARKET_DATA_NEWS",
        "existing_capability": "AIC_ALPACA_NEWS_REOPEN_PAGINATION_v0_1",
        "auth_mode": "CLI_PROFILE:paper",
        "symbol_scope": [symbol],
        "target_ids": target_ids,
        "max_dispatch_attempts": NEWS_MAX_PAGES_PER_SYMBOL,
        "request_contract": {
            "window_start_utc": HISTORICAL_RESEARCH_CUTOFF_UTC,
            "window_end_rule": "PREFLIGHT_FROZEN_REOPEN_CUTOFF_UTC",
            "sort": "desc",
            "page_size": NEWS_PAGE_SIZE,
            "max_pages": NEWS_MAX_PAGES_PER_SYMBOL,
            "max_articles": NEWS_MAX_ARTICLES_PER_SYMBOL,
            "include_content": True,
            "exclude_contentless": False,
            "single_symbol_only": True,
            "pagination_completion_rule": "MUST_REACH_TERMINAL_PAGE_WITHIN_MAX_PAGES; OTHERWISE PARTIAL_AND_STOP",
            "automatic_pagination_beyond_max_pages": False,
        },
        "freshness_role": "POST_FROZEN_RESEARCH_CUTOFF_CURRENT_DEVELOPMENTS_REFRESH",
        "provider_read_authorized": False,
    }


def _read_bundles() -> list[dict[str, Any]]:
    return [
        _news_bundle(
            bundle_id="ER1_NVDA_NEWS_REFRESH",
            symbol="NVDA",
            target_ids=["NVDA_CURRENT_DEVELOPMENTS_Q4"],
            order=1,
        ),
        _news_bundle(
            bundle_id="ER2_MSFT_NEWS_REFRESH",
            symbol="MSFT",
            target_ids=[
                "MSFT_VALUATION_CONTEXT_DEPTH",
                "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
            ],
            order=2,
        ),
        _news_bundle(
            bundle_id="ER3_META_NEWS_REFRESH",
            symbol="META",
            target_ids=[
                "META_CONDITION_001",
                "META_CONDITION_002",
                "META_CONDITION_003",
            ],
            order=3,
        ),
        {
            "bundle_id": "ER4_CURRENT_PAPER_POSITIONS",
            "execution_order": 4,
            "provider": "ALPACA_TRADING_API",
            "existing_capability": "ALPACA_CLI_POSITION_LIST",
            "auth_mode": "CLI_PROFILE:paper",
            "target_ids": ["META_CONDITION_004"],
            "max_dispatch_attempts": 1,
            "request_contract": {
                "cli_command": ["alpaca", "position", "list"],
                "response_received_at_role": "CURRENT_PAPER_PORTFOLIO_ANCHOR_UTC",
                "max_position_symbols_for_market_expansion": MAX_POSITION_SYMBOLS_FOR_MARKET_EXPANSION,
                "position_scope_overflow_rule": "FAIL_CLOSED_BEFORE_DYNAMIC_MARKET_BUNDLE",
                "live_profile_forbidden": True,
            },
            "provider_read_authorized": False,
        },
        {
            "bundle_id": "ER5_CURRENT_PORTFOLIO_EQUITY",
            "execution_order": 5,
            "provider": "ALPACA_TRADING_API",
            "existing_capability": "ALPACA_CLI_ACCOUNT_PORTFOLIO",
            "auth_mode": "CLI_PROFILE:paper",
            "target_ids": ["META_CONDITION_004"],
            "max_dispatch_attempts": 1,
            "request_contract": {
                "cli_command": ["alpaca", "account", "portfolio"],
                "start_rule": "PREFLIGHT_FROZEN_REOPEN_CUTOFF_UTC_MINUS_7_CALENDAR_DAYS",
                "end_rule": "PREFLIGHT_FROZEN_REOPEN_CUTOFF_UTC",
                "timeframe": "1Day",
                "intraday_reporting": "market_hours",
                "selection_rule": "LATEST_EQUITY_DATAPOINT_TIMESTAMP_AT_OR_BEFORE_REOPEN_CUTOFF",
                "pagination_authorized": False,
                "live_profile_forbidden": True,
            },
            "provider_read_authorized": False,
        },
        {
            "bundle_id": "ER6_DYNAMIC_MARKET_CONTEXT",
            "execution_order": 6,
            "provider": "ALPACA_MARKET_DATA",
            "existing_capability": "ALPACA_CLI_DATA_MULTI_BARS",
            "auth_mode": "CLI_PROFILE:paper",
            "target_ids": ["MSFT_VALUATION_CONTEXT_DEPTH", "META_CONDITION_004"],
            "depends_on_bundle_ids": ["ER4_CURRENT_PAPER_POSITIONS"],
            "max_dispatch_attempts": 1,
            "request_contract": {
                "cli_command": ["alpaca", "data", "multi-bars"],
                "symbol_rule": "DEDUPED_MSFT_META_PLUS_CURRENT_EQUITY_POSITION_SYMBOLS",
                "required_symbols": ["MSFT", "META"],
                "max_symbols": MAX_DYNAMIC_MARKET_SYMBOLS,
                "start_rule": "PREFLIGHT_FROZEN_REOPEN_CUTOFF_UTC_MINUS_45_CALENDAR_DAYS",
                "end_rule": "PREFLIGHT_FROZEN_REOPEN_CUTOFF_UTC",
                "timeframe": "1Hour",
                "feed": "iex",
                "sort": "asc",
                "limit": 1000,
                "max_pages": 1,
                "pagination_completion_rule": "NEXT_PAGE_TOKEN_MUST_BE_NULL; OTHERWISE PARTIAL_AND_STOP",
                "automatic_pagination_continuation": False,
                "valuation_price_rule": "LATEST_COMPLETED_MSFT_AND_META_BAR_AT_OR_BEFORE_REOPEN_CUTOFF",
                "portfolio_interaction_rule": "DETERMINISTIC_CONCENTRATION_AND_RETURN_CORRELATION_CONTEXT_ONLY_WHEN_OVERLAP_IS_SUFFICIENT",
                "minimum_pairwise_return_overlap": 40,
            },
            "provider_read_authorized": False,
        },
    ]


def _target_bundle_map() -> list[dict[str, Any]]:
    return [
        {"target_id": "NVDA_CURRENT_DEVELOPMENTS_Q4", "bundle_ids": ["ER1_NVDA_NEWS_REFRESH"]},
        {
            "target_id": "MSFT_VALUATION_CONTEXT_DEPTH",
            "bundle_ids": ["ER2_MSFT_NEWS_REFRESH", "ER6_DYNAMIC_MARKET_CONTEXT"],
        },
        {
            "target_id": "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
            "bundle_ids": ["ER2_MSFT_NEWS_REFRESH"],
        },
        {"target_id": "META_CONDITION_001", "bundle_ids": ["ER3_META_NEWS_REFRESH"]},
        {"target_id": "META_CONDITION_002", "bundle_ids": ["ER3_META_NEWS_REFRESH"]},
        {"target_id": "META_CONDITION_003", "bundle_ids": ["ER3_META_NEWS_REFRESH"]},
        {
            "target_id": "META_CONDITION_004",
            "bundle_ids": [
                "ER4_CURRENT_PAPER_POSITIONS",
                "ER5_CURRENT_PORTFOLIO_EQUITY",
                "ER6_DYNAMIC_MARKET_CONTEXT",
            ],
        },
    ]


def build_plan(*, local_replay: Mapping[str, Any], code_commit_sha: str) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None, "plan code SHA invalid")
    local_replay_hash = verify_local_replay(local_replay)
    bundles = _read_bundles()
    mapping = _target_bundle_map()

    _need(tuple(row["bundle_id"] for row in bundles) == BUNDLE_IDS, "provider bundle identity/order drift")
    _need(len(bundles) == LOGICAL_PROVIDER_READ_BUNDLE_COUNT, "logical provider bundle count drift")
    _need(sum(int(row["max_dispatch_attempts"]) for row in bundles) == PROVIDER_DISPATCH_ATTEMPTS_MAX, "provider dispatch ceiling arithmetic drift")
    _need(tuple(row["target_id"] for row in mapping) == TARGET_IDS, "target-to-bundle coverage drift")
    _need(all(row["provider_read_authorized"] is False for row in bundles), "plan cannot authorize provider reads")

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_local_replay_hash": local_replay_hash,
        "source_existing_evidence_inventory_hash": EXPECTED_INVENTORY_HASH,
        "source_judge_result_hash": EXPECTED_JUDGE_HASH,
        "residual_external_read_target_count": 7,
        "residual_external_read_target_ids": list(TARGET_IDS),
        "logical_provider_read_bundle_count": LOGICAL_PROVIDER_READ_BUNDLE_COUNT,
        "logical_provider_read_bundle_ids": list(BUNDLE_IDS),
        "provider_dispatch_attempts_max": PROVIDER_DISPATCH_ATTEMPTS_MAX,
        "provider_response_reads_max": PROVIDER_DISPATCH_ATTEMPTS_MAX,
        "news_dispatch_attempts_max": NEWS_DISPATCH_ATTEMPTS_MAX,
        "non_news_dispatch_attempts_max": NON_NEWS_DISPATCH_ATTEMPTS_MAX,
        "provider_read_bundles": bundles,
        "target_to_bundle_map": mapping,
        "frozen_evidence_reuse_rule": "NEW_READS_ARE_ADDITIVE_TO_FROZEN_B2_B3_B4_EVIDENCE; HISTORICAL ARTIFACTS_AND_CLAIMS_MUST_NOT_BE_MUTATED",
        "valuation_resolution_boundary": "FRESH_MARKET_CONTEXT_AND_CURRENT_NEWS_MAY ADD DEPTH BUT DO_NOT AUTOMATICALLY ESTABLISH FAIR_VALUE_OR VALUATION_ATTRACTIVENESS",
        "forward_durability_resolution_boundary": "CURRENT_NEWS_PLUS_FROZEN_PRIMARY_EVIDENCE_MUST BE RECONCILED; CURRENT_STRENGTH_ALONE CANNOT RESOLVE FORWARD_DURABILITY",
        "portfolio_resolution_boundary": "CURRENT_POSITIONS_EQUITY_AND_MARKET_CONTEXT MAY SUPPORT CONCENTRATION_AND_CORRELATION ANALYSIS; UNSUPPORTED SECTOR_OR_FACTOR CLAIMS ARE FORBIDDEN",
        "reopen_cutoff_policy": {
            "historical_research_cutoff_utc": HISTORICAL_RESEARCH_CUTOFF_UTC,
            "preflight_must_freeze_one_reopen_cutoff_utc": True,
            "reopen_cutoff_must_be_after_historical_cutoff": True,
            "reopen_cutoff_becomes_immutable_on_owner_approval": True,
            "all_provider_reads_must_be_bounded_to_reopen_cutoff": True,
        },
        "owner_approval_required_before_provider_read": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_retries": 0,
        "conditional_followup_reads_authorized": False,
        "pagination_beyond_bundle_bounds_authorized": False,
        "broad_b3_rerun_authorized": False,
        "research_run_started": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "judge_rerun_authorized": False,
        "rebuttal_rerun_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "execution_authority": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "post_read_rule": "AFTER_THE_SINGLE_AUTHORIZED_READ_PASS, FREEZE_RECEIPTS_AND_RUN_ZERO_CALL_EVIDENCE_RECONCILIATION; ANY REMAINING GAP STOPS WITHOUT AUTOMATIC_SECOND_ROUND",
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_plan(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == ARTIFACT_VERSION, "plan version drift")
    _need(payload.get("status") == PASS_STATUS, "plan status drift")
    _need(payload.get("code_commit_sha") == expected_code_commit_sha, "plan code SHA drift")
    _need(payload.get("source_local_replay_hash") == EXPECTED_LOCAL_REPLAY_HASH, "plan local replay lineage drift")
    _need(payload.get("source_existing_evidence_inventory_hash") == EXPECTED_INVENTORY_HASH, "plan inventory lineage drift")
    _need(payload.get("source_judge_result_hash") == EXPECTED_JUDGE_HASH, "plan Judge lineage drift")
    _need(payload.get("residual_external_read_target_count") == 7, "plan target count drift")
    _need(payload.get("residual_external_read_target_ids") == list(TARGET_IDS), "plan target identity drift")
    _need(payload.get("logical_provider_read_bundle_count") == LOGICAL_PROVIDER_READ_BUNDLE_COUNT, "plan logical bundle count drift")
    _need(payload.get("logical_provider_read_bundle_ids") == list(BUNDLE_IDS), "plan logical bundle identity drift")
    _need(payload.get("provider_dispatch_attempts_max") == PROVIDER_DISPATCH_ATTEMPTS_MAX, "plan dispatch ceiling drift")
    _need(payload.get("provider_response_reads_max") == PROVIDER_DISPATCH_ATTEMPTS_MAX, "plan response-read ceiling drift")
    _need(payload.get("news_dispatch_attempts_max") == NEWS_DISPATCH_ATTEMPTS_MAX, "plan news dispatch ceiling drift")
    bundles = payload.get("provider_read_bundles")
    _need(isinstance(bundles, list) and len(bundles) == LOGICAL_PROVIDER_READ_BUNDLE_COUNT, "plan bundles missing")
    _need(tuple(row.get("bundle_id") for row in bundles if isinstance(row, Mapping)) == BUNDLE_IDS, "plan bundle order drift")
    _need(sum(int(row.get("max_dispatch_attempts") or 0) for row in bundles if isinstance(row, Mapping)) == PROVIDER_DISPATCH_ATTEMPTS_MAX, "plan bundle dispatch arithmetic drift")
    _need(all(isinstance(row, Mapping) and row.get("provider_read_authorized") is False for row in bundles), "plan bundle unexpectedly authorizes provider read")
    mapping = payload.get("target_to_bundle_map")
    _need(isinstance(mapping, list), "plan target mapping missing")
    _need(tuple(row.get("target_id") for row in mapping if isinstance(row, Mapping)) == TARGET_IDS, "plan target mapping drift")
    _need(payload.get("owner_approval_required_before_provider_read") is True, "plan lost owner approval requirement")
    _need(payload.get("authorization_consumption_rule") == "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT", "plan authority consumption drift")
    _need(payload.get("automatic_retries") == 0, "plan retry boundary drift")
    _need(payload.get("conditional_followup_reads_authorized") is False, "plan cannot authorize follow-up reads")
    _need(payload.get("pagination_beyond_bundle_bounds_authorized") is False, "plan cannot authorize extra pagination")
    _need(payload.get("provider_reads_authorized") is False and payload.get("model_calls_authorized") is False, "plan cannot authorize provider/model calls")
    _need(payload.get("model_synthesis_authorized") is False, "plan cannot authorize model synthesis")
    _need(payload.get("broad_b3_rerun_authorized") is False and payload.get("research_run_started") is False, "plan cannot start broad research")
    _need(payload.get("judge_rerun_authorized") is False and payload.get("rebuttal_rerun_authorized") is False, "plan cannot authorize B4 reruns")
    _need(payload.get("final_decision_created") is False and payload.get("b5_handoff_created") is False, "plan cannot create FinalDecision/B5")
    _need(payload.get("execution_authority") is False, "plan cannot grant execution authority")
    _need(payload.get("model_calls") == 0 and payload.get("provider_reads") == 0, "plan zero-call counters drift")
    _need(payload.get("broker_writes") == 0 and payload.get("alpaca_orders") == 0, "plan broker/order boundary drift")
    _need(payload.get("live_money") == "PROHIBITED", "plan live-money boundary drift")
    _need(payload.get("next_gate") == NEXT_GATE, "plan next gate drift")
    return observed
