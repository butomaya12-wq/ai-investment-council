from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_residual_external_read_plan_v01 as plan


HEAD = "a" * 40


def _build(monkeypatch) -> dict:
    monkeypatch.setattr(plan, "verify_local_replay", lambda payload: plan.EXPECTED_LOCAL_REPLAY_HASH)
    local_replay = {
        "residual_external_read_target_count": 7,
        "residual_external_read_target_ids": list(plan.TARGET_IDS),
    }
    return plan.build_plan(local_replay=local_replay, code_commit_sha=HEAD)


def test_plan_compresses_seven_targets_into_six_bounded_bundles(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    assert artifact["status"] == plan.PASS_STATUS
    assert artifact["residual_external_read_target_count"] == 7
    assert artifact["residual_external_read_target_ids"] == list(plan.TARGET_IDS)
    assert artifact["logical_provider_read_bundle_count"] == 6
    assert artifact["logical_provider_read_bundle_ids"] == list(plan.BUNDLE_IDS)
    assert artifact["provider_dispatch_attempts_max"] == 9
    assert artifact["news_dispatch_attempts_max"] == 6
    assert artifact["non_news_dispatch_attempts_max"] == 3
    assert artifact["artifact_hash"] == canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    assert plan.verify_plan(artifact, expected_code_commit_sha=HEAD) == artifact["artifact_hash"]


def test_news_scope_is_single_symbol_two_pages_max(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    bundles = artifact["provider_read_bundles"]
    news = bundles[:3]
    assert [row["symbol_scope"] for row in news] == [["NVDA"], ["MSFT"], ["META"]]
    for row in news:
        contract = row["request_contract"]
        assert row["max_dispatch_attempts"] == 2
        assert contract["page_size"] == 5
        assert contract["max_pages"] == 2
        assert contract["max_articles"] == 10
        assert contract["window_start_utc"] == plan.HISTORICAL_RESEARCH_CUTOFF_UTC
        assert contract["window_end_rule"] == "PREFLIGHT_FROZEN_REOPEN_CUTOFF_UTC"
        assert contract["single_symbol_only"] is True
        assert contract["automatic_pagination_beyond_max_pages"] is False


def test_meta_portfolio_context_reuses_existing_position_portfolio_and_multi_bars_surfaces(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    bundles = {row["bundle_id"]: row for row in artifact["provider_read_bundles"]}

    positions = bundles["ER4_CURRENT_PAPER_POSITIONS"]
    assert positions["existing_capability"] == "ALPACA_CLI_POSITION_LIST"
    assert positions["request_contract"]["max_position_symbols_for_market_expansion"] == 18
    assert positions["request_contract"]["live_profile_forbidden"] is True

    equity = bundles["ER5_CURRENT_PORTFOLIO_EQUITY"]
    assert equity["existing_capability"] == "ALPACA_CLI_ACCOUNT_PORTFOLIO"
    assert equity["request_contract"]["timeframe"] == "1Day"
    assert equity["request_contract"]["pagination_authorized"] is False

    market = bundles["ER6_DYNAMIC_MARKET_CONTEXT"]
    assert market["existing_capability"] == "ALPACA_CLI_DATA_MULTI_BARS"
    assert market["depends_on_bundle_ids"] == ["ER4_CURRENT_PAPER_POSITIONS"]
    assert market["request_contract"]["required_symbols"] == ["MSFT", "META"]
    assert market["request_contract"]["max_symbols"] == 20
    assert market["request_contract"]["timeframe"] == "1Hour"
    assert market["request_contract"]["max_pages"] == 1
    assert market["request_contract"]["automatic_pagination_continuation"] is False
    assert market["request_contract"]["minimum_pairwise_return_overlap"] == 40


def test_target_bundle_map_has_complete_coverage_without_one_read_per_target(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    mapping = {row["target_id"]: row["bundle_ids"] for row in artifact["target_to_bundle_map"]}
    assert tuple(mapping) == plan.TARGET_IDS
    assert mapping["NVDA_CURRENT_DEVELOPMENTS_Q4"] == ["ER1_NVDA_NEWS_REFRESH"]
    assert mapping["MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY"] == ["ER2_MSFT_NEWS_REFRESH"]
    assert mapping["META_CONDITION_001"] == ["ER3_META_NEWS_REFRESH"]
    assert mapping["META_CONDITION_002"] == ["ER3_META_NEWS_REFRESH"]
    assert mapping["META_CONDITION_003"] == ["ER3_META_NEWS_REFRESH"]
    assert mapping["META_CONDITION_004"] == [
        "ER4_CURRENT_PAPER_POSITIONS",
        "ER5_CURRENT_PORTFOLIO_EQUITY",
        "ER6_DYNAMIC_MARKET_CONTEXT",
    ]
    assert artifact["logical_provider_read_bundle_count"] < artifact["residual_external_read_target_count"]


def test_plan_is_not_provider_or_model_authority(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    assert artifact["owner_approval_required_before_provider_read"] is True
    assert artifact["authorization_consumption_rule"] == "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT"
    assert artifact["automatic_retries"] == 0
    assert artifact["conditional_followup_reads_authorized"] is False
    assert artifact["pagination_beyond_bundle_bounds_authorized"] is False
    assert artifact["provider_reads_authorized"] is False
    assert artifact["model_calls_authorized"] is False
    assert artifact["model_synthesis_authorized"] is False
    assert artifact["broad_b3_rerun_authorized"] is False
    assert artifact["judge_rerun_authorized"] is False
    assert artifact["rebuttal_rerun_authorized"] is False
    assert artifact["final_decision_created"] is False
    assert artifact["b5_handoff_created"] is False
    assert artifact["execution_authority"] is False
    assert artifact["model_calls"] == 0
    assert artifact["provider_reads"] == 0
    assert artifact["live_money"] == "PROHIBITED"
    assert all(row["provider_read_authorized"] is False for row in artifact["provider_read_bundles"])


def test_plan_verifier_rejects_dispatch_ceiling_tamper(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    tampered = deepcopy(artifact)
    tampered["provider_dispatch_attempts_max"] = 10
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(plan.ResidualExternalReadPlanError, match="dispatch ceiling drift"):
        plan.verify_plan(tampered, expected_code_commit_sha=HEAD)


def test_plan_verifier_rejects_call_authority_tamper(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    tampered = deepcopy(artifact)
    tampered["provider_reads_authorized"] = True
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(plan.ResidualExternalReadPlanError, match="cannot authorize provider/model calls"):
        plan.verify_plan(tampered, expected_code_commit_sha=HEAD)


def test_runner_has_no_external_execution_surface() -> None:
    source = Path("scripts/b3_research_reopen_residual_external_read_plan_zero_call_v01.py").read_text(encoding="utf-8")
    forbidden = (
        "urlopen",
        "requests.",
        "httpx",
        "StdlibAlpacaNewsTransport",
        "ReopenAlpacaCliNewsTransport",
        "provider.post",
        "position list",
        "account portfolio",
        "data multi-bars",
        "submit_order",
        "execute-paid",
    )
    for token in forbidden:
        assert token not in source
