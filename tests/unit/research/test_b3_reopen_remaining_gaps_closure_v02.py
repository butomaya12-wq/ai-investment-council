from __future__ import annotations

import json
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_remaining_gaps_closure_v02 as closure


def _hashed(payload: dict) -> dict:
    value = dict(payload)
    value["artifact_hash"] = canonical_sha256(value)
    return value


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _fixtures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    recovery = _hashed(
        {
            "status": "B3_REOPEN_MINIMAL_EXTERNAL_READ_RECOVERY_ZERO_CALL_PASS",
            "next_gate": "B3_REOPEN_REMAINING_GAPS_CLOSURE_ZERO_CALL",
            "valuation_specific_evidence_ready": True,
            "portfolio_interaction_evidence_ready": True,
            "new_provider_dispatch_attempts": 0,
            "new_provider_reads": 0,
            "model_calls": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "live_money": "PROHIBITED",
            "pagination_recovery": {
                "terminal_page_recovered": True,
                "observed_next_page_token_representation": "EMPTY_STRING",
                "pagination_continuation_required": False,
                "provider_rerun_required": False,
            },
            "valuation_recovery": {
                "MSFT": {
                    "annual_gaap_diluted_eps": "17.95",
                    "eps_period": "FY2026",
                    "price_to_eps": "28.821727019499",
                    "valuation_evidence_complete": True,
                    "price": {
                        "close": "517.35",
                        "bar_timestamp_utc": "2026-08-28T17:33:00Z",
                        "feed": "iex",
                    },
                },
                "META": {
                    "annual_gaap_diluted_eps": "23.49",
                    "eps_period": "FY2025",
                    "price_to_eps": "24.550021285653",
                    "valuation_evidence_complete": True,
                    "price": {
                        "close": "576.68",
                        "bar_timestamp_utc": "2026-08-28T17:33:00Z",
                        "feed": "iex",
                    },
                },
            },
            "portfolio_recovery": {
                "portfolio_interaction_evidence_complete": True,
                "reconstructed_meta_quantity_at_b2_cutoff": "0",
                "reconstructed_meta_market_value_at_b2_cutoff": "0",
                "reconstructed_meta_portfolio_weight": "0.000000000000",
                "b2_cutoff_portfolio_equity": {
                    "selected_equity": "200000",
                    "selected_equity_timestamp_utc": "2026-08-27T20:00:00Z",
                },
                "meta_b2_cutoff_price": {
                    "close": "571.03",
                    "bar_timestamp_utc": "2026-08-27T19:59:00Z",
                },
            },
        }
    )
    monkeypatch.setattr(closure, "EXPECTED_RECOVERY_HASH", recovery["artifact_hash"])

    selected_rows = []
    claim_index = 1
    for candidate, count in (("NVDA", 12), ("MSFT", 12), ("META", 10)):
        claims = []
        for _ in range(count):
            claims.append({"claim_id": f"LEGACY_{claim_index:03d}", "candidate_id": candidate, "category": "risk"})
            claim_index += 1
        selected_rows.append({"candidate": candidate, "material_claims": claims})
    selected = _hashed({"candidates": selected_rows})
    monkeypatch.setattr(closure, "EXPECTED_SELECTED_HASH", selected["artifact_hash"])

    claim_recon = _hashed(
        {
            "status": "B3_REOPEN_BOUNDED_NEWS_CLAIM_RECONCILIATION_ZERO_CALL_PASS",
            "news_gap_closed": True,
            "closure_evidence_ref": closure.NEWS_CLOSURE,
            "remaining_reopen_reason_codes": list(closure.REMAINING_REASONS),
            "source_b3_selected_model_reconciliation_hash": selected["artifact_hash"],
            "source_production_judge_result_hash": "JUDGE_PLACEHOLDER",
        }
    )

    plan = _hashed(
        {
            "status": "B3_REOPEN_REMAINING_GAPS_EVIDENCE_PLAN_ZERO_CALL_PASS",
            "target_candidates": ["MSFT", "META"],
            "non_target_candidate_ids": ["NVDA"],
        }
    )
    monkeypatch.setattr(closure, "EXPECTED_PLAN_HASH", plan["artifact_hash"])

    scope = _hashed(
        {
            "status": "B3_REOPEN_REMAINING_GAPS_SCOPE_ZERO_CALL_PASS",
            "remaining_reopen_reason_codes": list(closure.REMAINING_REASONS),
        }
    )
    monkeypatch.setattr(closure, "EXPECTED_SCOPE_HASH", scope["artifact_hash"])

    primitives = _hashed({"status": "PRIMITIVES_PASS"})
    monkeypatch.setattr(closure, "EXPECTED_PRIMITIVES_HASH", primitives["artifact_hash"])

    reopen = {
        "reopen_request_id": "REOPEN_TEST",
        "reason_codes": [closure.NEWS_GAP, closure.VALUATION_GAP, closure.PORTFOLIO_GAP],
        "source_ref_ids": [closure.NEWS_GAP],
        "requested_at": "2026-08-30T15:00:00Z",
        "new_run_start_state": "S00",
        "parent_run_id": "PARENT",
        "parent_decision_id": None,
        "trigger_bundle_id": None,
    }
    reopen["request_hash"] = canonical_sha256(reopen)
    monkeypatch.setattr(closure, "EXPECTED_REOPEN_ID", reopen["reopen_request_id"])
    monkeypatch.setattr(closure, "EXPECTED_REOPEN_HASH", reopen["request_hash"])

    judge = _hashed(
        {
            "status": "B4_COMPLETE_RESEARCH_REOPEN_REQUESTED",
            "final_decision_created": False,
            "b5_handoff_created": False,
            "rerun_authorized": False,
            "research_reopen_request": reopen,
            "research_reopen_request_hash": reopen["request_hash"],
            "structured_output": {
                "what_would_change_decision": [
                    {"condition_id": "CONDITION_001"},
                    {"condition_id": "CONDITION_002"},
                    {"condition_id": "CONDITION_003"},
                ]
            },
        }
    )
    monkeypatch.setattr(closure, "EXPECTED_JUDGE_HASH", judge["artifact_hash"])

    claim_recon["source_production_judge_result_hash"] = judge["artifact_hash"]
    claim_recon["artifact_hash"] = canonical_sha256(claim_recon, exclude_fields=("artifact_hash",))
    monkeypatch.setattr(closure, "EXPECTED_CLAIM_RECON_HASH", claim_recon["artifact_hash"])

    paths = {}
    for name, payload in {
        "recovery": recovery,
        "claim_reconciliation": claim_recon,
        "evidence_plan": plan,
        "scope": scope,
        "primitives": primitives,
        "selected_reconciliation": selected,
        "judge_result": judge,
    }.items():
        path = tmp_path / f"{name}.json"
        _write(path, payload)
        paths[name] = path
    return paths


def _build(paths: dict[str, Path]) -> dict:
    return closure.build_closure(
        code_commit_sha="a" * 40,
        recovery_path=paths["recovery"],
        claim_reconciliation_path=paths["claim_reconciliation"],
        evidence_plan_path=paths["evidence_plan"],
        scope_path=paths["scope"],
        primitives_path=paths["primitives"],
        selected_reconciliation_path=paths["selected_reconciliation"],
        judge_result_path=paths["judge_result"],
    )


def test_closure_is_additive_and_closes_all_three_judge_conditions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = _fixtures(tmp_path, monkeypatch)
    artifact = _build(paths)
    assert artifact["status"] == closure.PASS_STATUS
    assert artifact["legacy_material_claims"]["claim_count"] == 34
    assert artifact["legacy_material_claims"]["payloads_mutated"] is False
    assert artifact["reopen_overlay_is_additive"] is True
    assert artifact["supplemental_claim_count"] == 3
    assert artifact["all_judge_conditions_satisfied"] is True
    assert [row["condition_id"] for row in artifact["judge_condition_closure"]] == [
        "CONDITION_001",
        "CONDITION_002",
        "CONDITION_003",
    ]
    assert artifact["remaining_reopen_reason_codes"] == []
    assert artifact["research_reopen_request_satisfied"] is True
    assert artifact["overall_research_reopen_complete"] is True
    assert artifact["next_gate"] == "B4_REOPEN_INPUT_OVERLAY_ZERO_CALL"
    assert artifact["new_provider_reads"] == 0
    assert artifact["model_calls"] == 0
    assert artifact["final_decision_created"] is False
    assert artifact["b5_handoff_created"] is False


def test_canonical_reopen_request_uses_request_hash_not_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = _fixtures(tmp_path, monkeypatch)
    judge = json.loads(paths["judge_result"].read_text(encoding="utf-8"))
    judge["research_reopen_request"]["reopen_request_hash"] = judge["research_reopen_request"].pop("request_hash")
    judge["artifact_hash"] = canonical_sha256(judge, exclude_fields=("artifact_hash",))
    monkeypatch.setattr(closure, "EXPECTED_JUDGE_HASH", judge["artifact_hash"])
    paths["judge_result"].write_text(json.dumps(judge), encoding="utf-8")
    claim = json.loads(paths["claim_reconciliation"].read_text(encoding="utf-8"))
    claim["source_production_judge_result_hash"] = judge["artifact_hash"]
    claim["artifact_hash"] = canonical_sha256(claim, exclude_fields=("artifact_hash",))
    monkeypatch.setattr(closure, "EXPECTED_CLAIM_RECON_HASH", claim["artifact_hash"])
    paths["claim_reconciliation"].write_text(json.dumps(claim), encoding="utf-8")
    with pytest.raises(closure.ClosureError, match="canonical reopen request hash drift"):
        _build(paths)


def test_supplemental_claim_ids_cannot_collide_with_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = _fixtures(tmp_path, monkeypatch)
    selected = json.loads(paths["selected_reconciliation"].read_text(encoding="utf-8"))
    selected["candidates"][0]["material_claims"][0]["claim_id"] = closure.C_MSFT_VAL
    selected["artifact_hash"] = canonical_sha256(selected, exclude_fields=("artifact_hash",))
    monkeypatch.setattr(closure, "EXPECTED_SELECTED_HASH", selected["artifact_hash"])
    paths["selected_reconciliation"].write_text(json.dumps(selected), encoding="utf-8")
    claim = json.loads(paths["claim_reconciliation"].read_text(encoding="utf-8"))
    claim["source_b3_selected_model_reconciliation_hash"] = selected["artifact_hash"]
    claim["artifact_hash"] = canonical_sha256(claim, exclude_fields=("artifact_hash",))
    monkeypatch.setattr(closure, "EXPECTED_CLAIM_RECON_HASH", claim["artifact_hash"])
    paths["claim_reconciliation"].write_text(json.dumps(claim), encoding="utf-8")
    with pytest.raises(closure.ClosureError, match="supplemental claim id collision"):
        _build(paths)


def test_runner_has_no_provider_or_model_execution_surface():
    source = Path("scripts/b3_reopen_remaining_gaps_closure_zero_call_v02.py").read_text(encoding="utf-8")
    assert '"alpaca"' not in source
    assert "execute-provider" not in source
    assert "OPENAI_API_KEY" not in source
    assert "APCA_API_KEY" not in source
    assert "submit_order" not in source
    assert "model_calls=1" not in source
