from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_residual_external_read_preflight_v01 as preflight
from aic.research import reopen_judge_residual_external_read_plan_v01 as plan_v01


HEAD = "b" * 40
CUTOFF = "2026-08-31T08:55:00Z"


def _plan(monkeypatch) -> dict:
    monkeypatch.setattr(
        plan_v01,
        "verify_local_replay",
        lambda payload: plan_v01.EXPECTED_LOCAL_REPLAY_HASH,
    )
    raw = plan_v01.build_plan(
        local_replay={},
        code_commit_sha=preflight.EXPECTED_PLAN_CODE_SHA,
    )
    raw["output_path"] = ".aic-runtime/b3_research_reopen_residual_external_read_plan_zero_call_v0_1.json"
    raw["artifact_hash"] = canonical_sha256(raw, exclude_fields=("artifact_hash",))
    monkeypatch.setattr(preflight, "EXPECTED_PLAN_HASH", raw["artifact_hash"])
    return raw


def _build(monkeypatch) -> dict:
    return preflight.build_preflight(
        plan=_plan(monkeypatch),
        code_commit_sha=HEAD,
        reopen_cutoff_utc=CUTOFF,
    )


def test_preflight_freezes_cutoff_and_six_request_templates(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    assert artifact["status"] == preflight.PASS_STATUS
    assert artifact["reopen_cutoff_utc"] == CUTOFF
    assert artifact["logical_provider_read_bundle_count"] == 6
    assert artifact["logical_provider_read_bundle_ids"] == list(preflight.EXPECTED_BUNDLE_IDS)
    assert artifact["provider_dispatch_attempts_max"] == 9
    assert artifact["news_dispatch_attempts_max"] == 6
    assert artifact["non_news_dispatch_attempts_max"] == 3
    assert len(artifact["request_preflights"]) == 6
    assert artifact["artifact_hash"] == canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    assert preflight.verify_preflight(artifact, expected_code_commit_sha=HEAD) == artifact["artifact_hash"]


def test_preflight_resolves_cutoff_windows(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    rows = {row["bundle_id"]: row for row in artifact["request_preflights"]}

    for bundle_id in ("ER1_NVDA_NEWS_REFRESH", "ER2_MSFT_NEWS_REFRESH", "ER3_META_NEWS_REFRESH"):
        contract = rows[bundle_id]["resolved_request_contract"]
        assert contract["window_start_utc"] == preflight.HISTORICAL_RESEARCH_CUTOFF_UTC
        assert contract["window_end_utc"] == CUTOFF
        assert "window_end_rule" not in contract

    equity = rows["ER5_CURRENT_PORTFOLIO_EQUITY"]["resolved_request_contract"]
    assert equity["start_utc"] == "2026-08-24T08:55:00Z"
    assert equity["end_utc"] == CUTOFF
    assert "start_rule" not in equity
    assert "end_rule" not in equity

    market = rows["ER6_DYNAMIC_MARKET_CONTEXT"]["resolved_request_contract"]
    assert market["start_utc"] == "2026-07-17T08:55:00Z"
    assert market["end_utc"] == CUTOFF


def test_dynamic_market_request_is_template_bound_not_falsely_exact(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    rows = {row["bundle_id"]: row for row in artifact["request_preflights"]}
    market = rows["ER6_DYNAMIC_MARKET_CONTEXT"]["resolved_request_contract"]
    binding = market["runtime_symbol_binding"]

    assert binding["source_bundle_id"] == "ER4_CURRENT_PAPER_POSITIONS"
    assert binding["required_symbols"] == ["MSFT", "META"]
    assert binding["max_additional_position_symbols"] == 18
    assert binding["max_total_symbols"] == 20
    assert binding["overflow_rule"] == "FAIL_CLOSED_BEFORE_ER6_PROVIDER_DISPATCH"
    assert binding["final_request_hash_rule"] == "COMPUTE_AND_DURABLY_RECORD_AFTER_ER4_RESPONSE_BEFORE_ER6_DISPATCH"

    top = artifact["dynamic_request_binding_rule"]
    assert top["owner_approval_binds_template_and_runtime_binding_algorithm"] is True
    assert top["final_er6_request_hash_must_be_recorded_before_dispatch"] is True


def test_every_request_template_is_self_bound_and_has_no_authority(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    for row in artifact["request_preflights"]:
        assert row["request_template_hash"] == canonical_sha256(
            row,
            exclude_fields=("request_template_hash",),
        )
        assert row["provider_read_authorized"] is False
        assert row["model_call_authorized"] is False
        assert row["automatic_retry_authorized"] is False

    expected_manifest = canonical_sha256(
        {
            "reopen_cutoff_utc": CUTOFF,
            "request_template_hashes": [row["request_template_hash"] for row in artifact["request_preflights"]],
        }
    )
    assert artifact["request_manifest_hash"] == expected_manifest


def test_preflight_is_not_execution_authority(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    assert artifact["owner_approval_required_before_provider_read"] is True
    assert artifact["owner_provider_read_approval_present"] is False
    assert artifact["provider_reads_authorized"] is False
    assert artifact["model_calls_authorized"] is False
    assert artifact["model_synthesis_authorized"] is False
    assert artifact["execution_authority"] is False
    assert artifact["single_authorized_read_pass_only"] is True
    assert artifact["authorization_consumption_rule"] == "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT"
    assert artifact["automatic_retries"] == 0
    assert artifact["conditional_followup_reads_authorized"] is False
    assert artifact["pagination_beyond_bundle_bounds_authorized"] is False
    assert artifact["stop_on_bundle_error"] is True
    assert artifact["model_calls"] == 0
    assert artifact["provider_reads"] == 0
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert artifact["cost_usd"] == "0"
    assert artifact["live_money"] == "PROHIBITED"
    assert artifact["next_gate"] == preflight.NEXT_GATE


def test_preflight_rejects_cutoff_at_or_before_historical_cutoff(monkeypatch) -> None:
    raw_plan = _plan(monkeypatch)
    with pytest.raises(preflight.ResidualExternalReadPreflightError, match="after historical"):
        preflight.build_preflight(
            plan=raw_plan,
            code_commit_sha=HEAD,
            reopen_cutoff_utc=preflight.HISTORICAL_RESEARCH_CUTOFF_UTC,
        )


def test_preflight_verifier_rejects_dispatch_ceiling_tamper(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    tampered = deepcopy(artifact)
    tampered["provider_dispatch_attempts_max"] = 10
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(preflight.ResidualExternalReadPreflightError, match="provider_dispatch_attempts_max"):
        preflight.verify_preflight(tampered, expected_code_commit_sha=HEAD)


def test_preflight_verifier_rejects_request_template_tamper(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    tampered = deepcopy(artifact)
    tampered["request_preflights"][0]["resolved_request_contract"]["max_pages"] = 3
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(preflight.ResidualExternalReadPreflightError, match="request template hash mismatch"):
        preflight.verify_preflight(tampered, expected_code_commit_sha=HEAD)


def test_preflight_verifier_rejects_provider_authority_tamper(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    tampered = deepcopy(artifact)
    tampered["provider_reads_authorized"] = True
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(preflight.ResidualExternalReadPreflightError, match="provider_reads_authorized"):
        preflight.verify_preflight(tampered, expected_code_commit_sha=HEAD)


def test_runner_has_no_provider_or_model_execution_surface() -> None:
    source = Path("scripts/b3_research_reopen_residual_external_read_preflight_zero_call_v01.py").read_text(encoding="utf-8")
    forbidden = (
        "urlopen",
        "requests.",
        "httpx",
        "StdlibAlpacaNewsTransport",
        "ReopenAlpacaCliNewsTransport",
        "position list",
        "account portfolio",
        "data multi-bars",
        "subprocess.run([\"alpaca\"",
        "OPENAI_API_KEY",
        "provider.post",
        "submit_order",
        "execute-paid",
    )
    for token in forbidden:
        assert token not in source
