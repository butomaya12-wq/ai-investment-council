from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_existing_evidence_inventory_v01 as inv
from aic.research import reopen_remaining_gaps_closure_v02 as closure_v02


HEAD = "a" * 40


def _requirement(requirement_id: str, source_ref_id: str) -> dict:
    return {
        "requirement_id": requirement_id,
        "source_ref_id": source_ref_id,
    }


def _condition(i: int) -> dict:
    return {
        "condition_id": f"META_CONDITION_{i:03d}",
        "condition_text": f"Condition {i}",
        "source_or_claim_refs": [f"META_REF_{i}"],
    }


def _closure() -> dict:
    return {
        "supplemental_evidence_units": [
            {
                "evidence_id": closure_v02.E_MSFT_VAL,
                "observed": {"price_to_eps": "28.821727019499"},
            },
            {
                "evidence_id": closure_v02.E_META_VAL,
                "observed": {"price_to_eps": "24.550021285653"},
            },
            {
                "evidence_id": closure_v02.E_META_PORT,
                "observed": {"direct_position_exposure": "ZERO"},
            },
        ],
        "supplemental_claims": [
            {"claim_id": closure_v02.C_MSFT_VAL},
            {"claim_id": closure_v02.C_META_VAL},
            {"claim_id": closure_v02.C_META_PORT},
        ],
    }


def _sources(monkeypatch):
    scope = {
        "canonical_reopen_requirements": [
            _requirement("NVDA_CURRENT_DEVELOPMENTS_Q4", "Q4_RECENT_DEVELOPMENTS"),
            _requirement("MSFT_VALUATION_CONTEXT_DEPTH", "MSFT_VAL"),
            _requirement("MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY", "MSFT_DUR"),
        ],
        "judge_change_conditions_for_executable_invest": [_condition(i) for i in range(1, 5)],
    }
    closure = _closure()
    retrieval = {"candidates": []}
    selected = {}
    handoff = {
        "research_cutoff": "2026-08-28T17:33:00Z",
        "b2_decision_cutoff": "2026-08-27T20:00:00Z",
    }
    initial = {}
    judge = {
        "judge_proposal": {
            "what_would_change_decision": [_condition(i) for i in range(1, 5)],
        }
    }

    monkeypatch.setattr(inv, "verify_scope", lambda payload: inv.EXPECTED_SCOPE_HASH)
    monkeypatch.setattr(inv, "verify_closure", lambda payload: "c" * 64)
    monkeypatch.setattr(
        inv,
        "verify_selected_and_retrieval",
        lambda selected, retrieval, handoff: (
            inv.EXPECTED_SELECTED_HASH,
            "d" * 64,
            inv.EXPECTED_HANDOFF_HASH,
            datetime(2026, 8, 28, 17, 33, tzinfo=UTC),
            datetime(2026, 8, 27, 20, 0, tzinfo=UTC),
        ),
    )
    valuation_claim = {
        "claim_id": "MSFT_VAL",
        "evidence_ids": [closure_v02.E_MSFT_VAL],
        "computed_value_ids": [],
    }
    durability_claim = {
        "claim_id": "MSFT_DUR",
        "evidence_ids": ["MSFT_SEC_BUSINESS", "MSFT_SEC_RISK", "MSFT_SEC_MDA"],
        "computed_value_ids": ["MSFT_GROWTH", "MSFT_MARGIN"],
    }
    monkeypatch.setattr(
        inv.scope_v03,
        "verify_recovered_initial_freeze",
        lambda payload: (inv.EXPECTED_INITIAL_HASH, valuation_claim, durability_claim),
    )
    monkeypatch.setattr(inv.scope_v01, "verify_judge_result", lambda payload: inv.EXPECTED_JUDGE_HASH)
    monkeypatch.setattr(
        inv.local,
        "_candidate_map",
        lambda payload: {"NVDA": {}, "MSFT": {}, "META": {}},
    )
    monkeypatch.setattr(
        inv,
        "_news_inventory",
        lambda candidate, row, research_cutoff: {
            "candidate_id": candidate,
            "historical_news_evidence_count": 1,
            "historical_news_evidence_ids": [f"{candidate}_NEWS_OLD"],
            "latest_historical_news_as_of": "2026-08-28T10:00:00Z",
            "all_news_rows_at_or_before_research_cutoff": True,
            "post_judge_refresh_present_in_historical_retrieval": False,
        },
    )
    monkeypatch.setattr(
        inv.local,
        "_valuation_primitives",
        lambda candidate, row, research_cutoff: {
            "candidate_id": candidate,
            "diluted_eps_candidate_fragments": [{"evidence_id": f"{candidate}_EPS"}],
            "diluted_eps_candidate_fragment_count": 1,
            "diluted_eps_denominator_status": "LOCAL_PRIMARY_FILING_CANDIDATES_PRESENT_NEEDS_DETERMINISTIC_SELECTION",
            "disqualified_sec_price_fragments": [],
            "disqualified_sec_price_fragment_count": 0,
            "eligible_local_market_price_candidates": [],
            "eligible_local_market_price_candidate_count": 0,
            "local_market_price_status": "LOCAL_MARKET_PRICE_NOT_FOUND",
            "valuation_metric_computed_at_this_gate": False,
        },
    )
    monkeypatch.setattr(
        inv.local,
        "_portfolio_discoveries",
        lambda roots, b2_cutoff: [
            {
                "path": ".aic-runtime/example.json",
                "json_path": "$.snapshot",
                "at_or_before_b2_cutoff": True,
                "position_count": 2,
                "meta_position_present": False,
            }
        ],
    )
    return scope, closure, retrieval, selected, handoff, initial, judge


def _build(monkeypatch) -> dict:
    scope, closure, retrieval, selected, handoff, initial, judge = _sources(monkeypatch)
    return inv.build_inventory(
        scope=scope,
        historical_closure=closure,
        retrieval=retrieval,
        selected_reconciliation=selected,
        handoff=handoff,
        recovered_initial=initial,
        judge_result=judge,
        runtime_root=".aic-runtime",
        config_root="config/event",
        code_commit_sha=HEAD,
    )


def test_inventory_accounts_for_all_seven_targets_without_silent_resolution(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    assert artifact["status"] == inv.PASS_STATUS
    assert artifact["inventory_target_count"] == 7
    assert [row["target_id"] for row in artifact["inventory_targets"]] == list(inv.TARGET_IDS)
    assert artifact["resolved_target_count"] == 0
    assert artifact["local_replay_target_count"] == 2
    assert artifact["local_replay_target_ids"] == list(inv.LOCAL_REPLAY_TARGETS)
    assert artifact["residual_external_read_target_count"] == 5
    assert artifact["residual_external_read_target_ids"] == list(inv.EXTERNAL_READ_TARGETS)
    assert all(row["inventory_status"] != "RESOLVED" for row in artifact["inventory_targets"])
    assert artifact["provider_reads_authorized"] is False
    assert artifact["model_calls_authorized"] is False
    assert artifact["next_gate"] == inv.NEXT_GATE
    assert artifact["artifact_hash"] == canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    assert inv.verify_inventory(artifact, expected_code_commit_sha=HEAD) == artifact["artifact_hash"]


def test_forward_durability_is_not_inferred_from_current_strength(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    rows = {row["target_id"]: row for row in artifact["inventory_targets"]}
    msft = rows["MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY"]
    assert msft["inventory_status"] == "RESIDUAL_EXTERNAL_READ_REQUIRED"
    assert msft["external_read_required_after_inventory"] is True
    assert msft["local_replay_required"] is False
    assert msft["existing_evidence_refs"] == ["MSFT_SEC_BUSINESS", "MSFT_SEC_RISK", "MSFT_SEC_MDA"]
    assert artifact["forward_durability_resolution_rule"] == "CURRENT_STRENGTH_OR_DISCLOSED_RISK_ALONE_CANNOT_RESOLVE_FORWARD_DURABILITY"


def test_historical_point_in_time_valuation_and_portfolio_are_partial_not_closed(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    rows = {row["target_id"]: row for row in artifact["inventory_targets"]}
    msft_val = rows["MSFT_VALUATION_CONTEXT_DEPTH"]
    meta_context = rows["META_CONDITION_004"]
    assert msft_val["inventory_status"] == "LOCAL_REPLAY_FIRST"
    assert closure_v02.E_MSFT_VAL in msft_val["existing_evidence_refs"]
    assert closure_v02.C_MSFT_VAL in msft_val["existing_claim_refs"]
    assert meta_context["inventory_status"] == "LOCAL_REPLAY_FIRST"
    assert closure_v02.E_META_VAL in meta_context["existing_evidence_refs"]
    assert closure_v02.E_META_PORT in meta_context["existing_evidence_refs"]
    assert meta_context["external_read_required_after_inventory"] is False


def test_inventory_verifier_rejects_call_authority_tamper(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    tampered = deepcopy(artifact)
    tampered["provider_reads_authorized"] = True
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(inv.ExistingEvidenceInventoryError, match="cannot authorize calls"):
        inv.verify_inventory(tampered, expected_code_commit_sha=HEAD)


def test_inventory_verifier_rejects_silent_resolution(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    tampered = deepcopy(artifact)
    tampered["resolved_target_count"] = 1
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(inv.ExistingEvidenceInventoryError, match="cannot silently resolve"):
        inv.verify_inventory(tampered, expected_code_commit_sha=HEAD)


def test_inventory_runner_has_no_external_execution_surface() -> None:
    source = Path("scripts/b3_research_reopen_existing_evidence_inventory_zero_call_v01.py").read_text(encoding="utf-8")
    forbidden = (
        "urlopen",
        "requests.",
        "httpx",
        "StdlibResponsesTransport",
        "execute-paid",
        "provider.post",
        "submit_order",
    )
    for token in forbidden:
        assert token not in source
