from __future__ import annotations

import json
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_remaining_gaps_evidence_plan as mod


def _self_hashed(payload: dict) -> dict:
    result = dict(payload)
    result["artifact_hash"] = canonical_sha256(result)
    return result


def _handoff() -> dict:
    metrics = [
        ("return_20s", "RATIO"),
        ("max_drawdown_20s", "RATIO"),
        ("adv_20s", "USD"),
        ("annual_revenue_growth", "RATIO"),
        ("annual_operating_margin", "RATIO"),
    ]
    payload = {
        "handoff_version": "B2_REAL_EVENT_HANDOFF_v0_1",
        "source_run_id": "RUN",
        "source_evidence_index": "INDEX",
        "b2_decision_cutoff": "2026-08-27T20:00:00Z",
        "research_cutoff": "2026-08-28T17:34:00Z",
        "b2_snapshot_ref": "B2",
        "deep_comparison_ref": "DEEP",
        "top3": ["NVDA", "MSFT", "META"],
        "candidates": [],
    }
    for candidate in ("NVDA", "MSFT", "META"):
        payload["candidates"].append(
            {
                "symbol": candidate,
                "sec_accession": "0000000000-26-000001",
                "sec_source_uri": "https://www.sec.gov/example",
                "sec_evidence_id": f"SEC_{candidate}",
                "metrics": [
                    {
                        "computed_value_id": f"B2_{candidate}_{metric.upper()}",
                        "metric_id": metric,
                        "value": "1",
                        "unit": unit,
                    }
                    for metric, unit in metrics
                ],
            }
        )
    payload["handoff_hash"] = canonical_sha256(payload)
    return payload


def _retrieval() -> dict:
    candidates = []
    for candidate in ("NVDA", "MSFT", "META"):
        evidence = [
            {
                "evidence_id": f"SEC_{candidate}_MDA",
                "provider": "SEC",
                "source_type": "SEC_FILING_SECTION",
                "field_or_claim": "MD&A",
                "normalized_value": (
                    "Revenue was 100. Diluted earnings per share was 2.50. "
                    "Weighted-average diluted shares were 40."
                ),
                "raw_value_or_record_ref": "ref",
                "authoritative_for": ["financial_quality"],
            }
        ]
        candidates.append({"candidate": candidate, "research_evidence": {"evidence_items": evidence}})
    return _self_hashed({"candidates": candidates})


def _scope(*, retrieval_hash: str, handoff_hash: str, claim_hash: str) -> dict:
    rows = []
    for candidate in ("NVDA", "MSFT", "META"):
        rows.append(
            {
                "candidate_id": candidate,
                "valuation_context": {"claim_count": 0},
                "portfolio_interaction": {"claim_count": 1 if candidate == "NVDA" else 0},
            }
        )
    return _self_hashed(
        {
            "status": mod.EXPECTED_SCOPE_STATUS,
            "remaining_reopen_reason_codes": list(mod.EXPECTED_REASONS),
            "valuation_gap_closed_by_this_inventory": False,
            "portfolio_interaction_gap_closed_by_this_inventory": False,
            "source_claim_reconciliation_hash": claim_hash,
            "source_historical_b3_retrieval_hash": retrieval_hash,
            "source_b2_handoff_hash": handoff_hash,
            "candidate_scopes": rows,
            "inventory_summary": {
                "valuation_context_claim_count": 0,
                "shared_portfolio_context_ref_count": 0,
            },
        }
    )


def _judge() -> dict:
    return _self_hashed(
        {
            "status": mod.EXPECTED_JUDGE_STATUS,
            "structured_output": {
                "what_would_change_decision": [
                    {
                        "condition_id": "CONDITION_001",
                        "condition_text": "News closure is sufficient for NVDA.",
                        "source_or_claim_refs": ["NVDA_REF"],
                    },
                    {
                        "condition_id": "CONDITION_002",
                        "condition_text": "A new lifecycle supplies valuation evidence for MSFT.",
                        "source_or_claim_refs": ["MSFT_REF"],
                    },
                    {
                        "condition_id": "CONDITION_003",
                        "condition_text": "A new lifecycle addresses valuation-specific and portfolio-interaction omissions for META.",
                        "source_or_claim_refs": ["META_REF"],
                    },
                ]
            },
            "research_reopen_request": {
                "reason_codes": [
                    "ALPACA_NEWS_PAGINATION_INCOMPLETE",
                    *mod.EXPECTED_REASONS,
                ]
            },
        }
    )


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_local_valuation_inventory_detects_filing_primitives_without_price() -> None:
    row = _retrieval()["candidates"][1]
    result = mod._local_valuation_signal_inventory("MSFT", row)
    assert result["local_fundamental_text_available"] is True
    assert result["local_fundamental_primitive_signal_refs"]["diluted_eps"] == ["SEC_MSFT_MDA"]
    assert result["local_fundamental_primitive_signal_refs"]["revenue"] == ["SEC_MSFT_MDA"]
    assert result["local_fundamental_primitive_signal_refs"]["share_count"] == ["SEC_MSFT_MDA"]
    assert result["local_point_in_time_price_signal_detected"] is False


def test_portfolio_discovery_reads_only_local_json(tmp_path: Path) -> None:
    _write(
        tmp_path / "b2_snapshot.json",
        {"snapshot": {"portfolio_snapshot_ref": "PORTFOLIO_SNAPSHOT_20260827"}},
    )
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    found = mod._discover_local_portfolio_refs(tmp_path)
    assert found == [
        {
            "path": str(tmp_path / "b2_snapshot.json"),
            "json_path": "$.snapshot.portfolio_snapshot_ref",
            "portfolio_snapshot_ref": "PORTFOLIO_SNAPSHOT_20260827",
        }
    ]


def test_full_plan_targets_only_msft_and_meta_and_authorizes_zero_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retrieval = _retrieval()
    handoff = _handoff()
    judge = _judge()
    monkeypatch.setattr(mod, "EXPECTED_JUDGE_HASH", judge["artifact_hash"])
    claim = _self_hashed(
        {
            "status": mod.EXPECTED_CLAIM_RECON_STATUS,
            "closure_evidence_ref": mod.EXPECTED_NEWS_CLOSURE,
            "source_production_judge_result_hash": judge["artifact_hash"],
        }
    )
    scope = _scope(
        retrieval_hash=retrieval["artifact_hash"],
        handoff_hash=handoff["handoff_hash"],
        claim_hash=claim["artifact_hash"],
    )

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    _write(runtime / "manifest.json", {"portfolio_snapshot_ref": "PORT_SNAP"})
    paths = {
        "scope": tmp_path / "scope.json",
        "claim": tmp_path / "claim.json",
        "judge": tmp_path / "judge.json",
        "retrieval": tmp_path / "retrieval.json",
        "handoff": tmp_path / "handoff.json",
    }
    _write(paths["scope"], scope)
    _write(paths["claim"], claim)
    _write(paths["judge"], judge)
    _write(paths["retrieval"], retrieval)
    _write(paths["handoff"], handoff)

    artifact = mod.build_remaining_gaps_evidence_plan(
        code_commit_sha="a" * 40,
        scope_path=paths["scope"],
        claim_reconciliation_path=paths["claim"],
        judge_result_path=paths["judge"],
        retrieval_path=paths["retrieval"],
        handoff_path=paths["handoff"],
        runtime_root=runtime,
    )
    assert artifact["status"] == mod.PASS_STATUS
    assert artifact["target_candidates"] == ["MSFT", "META"]
    assert artifact["non_target_candidate_ids"] == ["NVDA"]
    assert artifact["provider_reads_authorized"] is False
    assert artifact["planned_provider_reads_at_this_gate"] == 0
    assert artifact["model_calls_authorized"] is False
    assert artifact["portfolio_interaction_evidence_plan"]["local_portfolio_snapshot_ref_discoveries"][0]["portfolio_snapshot_ref"] == "PORT_SNAP"
    assert artifact["artifact_hash"] == canonical_sha256(artifact, exclude_fields=("artifact_hash",))


def test_current_positions_are_explicitly_forbidden_as_historical_substitute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    retrieval = _retrieval()
    handoff = _handoff()
    judge = _judge()
    monkeypatch.setattr(mod, "EXPECTED_JUDGE_HASH", judge["artifact_hash"])
    claim = _self_hashed(
        {
            "status": mod.EXPECTED_CLAIM_RECON_STATUS,
            "closure_evidence_ref": mod.EXPECTED_NEWS_CLOSURE,
            "source_production_judge_result_hash": judge["artifact_hash"],
        }
    )
    scope = _scope(retrieval_hash=retrieval["artifact_hash"], handoff_hash=handoff["handoff_hash"], claim_hash=claim["artifact_hash"])
    for name, value in (("scope", scope), ("claim", claim), ("judge", judge), ("retrieval", retrieval), ("handoff", handoff)):
        _write(tmp_path / f"{name}.json", value)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    artifact = mod.build_remaining_gaps_evidence_plan(
        code_commit_sha="b" * 40,
        scope_path=tmp_path / "scope.json",
        claim_reconciliation_path=tmp_path / "claim.json",
        judge_result_path=tmp_path / "judge.json",
        retrieval_path=tmp_path / "retrieval.json",
        handoff_path=tmp_path / "handoff.json",
        runtime_root=runtime,
    )
    portfolio = artifact["portfolio_interaction_evidence_plan"]
    assert portfolio["required_output_contract"]["current_2026_08_30_positions_are_not_valid_substitute"] is True
    assert portfolio["external_read_fallback_not_authorized"]["current_positions"] == "PROHIBITED_AS_CUTOFF_SUBSTITUTE"


def test_zero_call_runner_has_no_network_or_execution_surface() -> None:
    source = Path("scripts/b3_reopen_remaining_gaps_evidence_plan_zero_call_v01.py").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "urlopen" not in lowered
    assert "requests." not in lowered
    assert "alpaca data" not in lowered
    assert "alpaca order" not in lowered
    assert "execute-provider-read" not in lowered
    assert "load_openai_api_key" not in lowered
    assert "provider_reads=0" in lowered
