from __future__ import annotations

import json
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research.reopen_claim_reconciliation import (
    CLOSURE_EVIDENCE_REF,
    EXPECTED_CANDIDATES,
    PASS_STATUS,
    PORTFOLIO_GAP,
    SUPERSEDED_GAP_REF,
    VALUATION_GAP,
    ReopenClaimReconciliationError,
    build_claim_reconciliation,
)


def _self_hashed(payload: dict) -> dict:
    result = dict(payload)
    result["artifact_hash"] = canonical_sha256(result)
    return result


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _fixtures(tmp_path: Path) -> dict[str, object]:
    bounded = _self_hashed(
        {
            "artifact_version": "B3_REOPEN_BOUNDED_NEWS_REVIEW_v0_1",
            "status": "B3_REOPEN_BOUNDED_NEWS_ZERO_CALL_PASS",
            "superseded_source_ref_id": SUPERSEDED_GAP_REF,
            "replacement_source_ref_id": CLOSURE_EVIDENCE_REF,
            "gap_closed": True,
            "provider_dataset_exhaustion_required": False,
            "candidate_reviews": [
                {"candidate_id": candidate, "bounded_request_satisfied": True}
                for candidate in EXPECTED_CANDIDATES
            ],
            "new_provider_reads": 0,
            "model_calls": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "live_money": "PROHIBITED",
        }
    )
    candidates = []
    bundles = []
    for index, candidate in enumerate(EXPECTED_CANDIDATES, start=1):
        claim_ids = [f"CLAIM_{candidate}_{index}"]
        candidates.append(
            {
                "candidate": candidate,
                "status": "CANONICAL_RECONCILED",
                "reconstructibility_status": "PASS",
                "source_gaps": [SUPERSEDED_GAP_REF],
                "candidate_packet": {"source_gaps": [SUPERSEDED_GAP_REF]},
                "material_claims": [{"claim_id": claim_ids[0], "claim_text": candidate}],
            }
        )
        bundles.append(
            {
                "candidate_id": candidate,
                "allowed_material_claim_ids": claim_ids,
            }
        )
    reconciliation = _self_hashed(
        {
            "artifact_version": "B3_SELECTED_MODEL_RECONCILIATION_ARTIFACT_v0_1",
            "canonical_reconciliation": "ALL_THREE_SELECTED_MODEL_CANDIDATE_PACKETS_RECONCILED",
            "reconstructibility_status": "PASS",
            "candidates": candidates,
        }
    )
    input_freeze = _self_hashed(
        {
            "artifact_version": "B4_COUNCIL_INPUT_FREEZE_ARTIFACT_v0_1",
            "b3_reconciliation_artifact_hash": reconciliation["artifact_hash"],
            "candidate_order": list(EXPECTED_CANDIDATES),
            "bundles": bundles,
        }
    )
    initial = _self_hashed(
        {
            "status": "INITIAL_COUNCIL_FROZEN",
            "processed_records": [
                {"candidate_id": candidate, "council_opinion": {"data_gap_refs": [SUPERSEDED_GAP_REF]}}
                for candidate in EXPECTED_CANDIDATES
            ],
        }
    )
    rebuttal = _self_hashed(
        {
            "status": "REBUTTAL_COUNCIL_FROZEN",
            "processed_records": [
                {
                    "candidate_id": candidate,
                    "required_unknown_refs": [SUPERSEDED_GAP_REF],
                    "frozen_rebuttal_bundle": {
                        "draft": {"items": [{"remaining_uncertainty_refs": [SUPERSEDED_GAP_REF]}]}
                    },
                }
                for candidate in EXPECTED_CANDIDATES
            ],
        }
    )
    judge = _self_hashed(
        {
            "status": "B4_COMPLETE_RESEARCH_REOPEN_REQUESTED",
            "research_reopen_request": {
                "reason_codes": [SUPERSEDED_GAP_REF, VALUATION_GAP, PORTFOLIO_GAP]
            },
            "material_unknown_ref": SUPERSEDED_GAP_REF,
        }
    )
    paths = {
        "bounded_review_path": _write(tmp_path / "bounded.json", bounded),
        "b3_reconciliation_path": _write(tmp_path / "reconciliation.json", reconciliation),
        "b4_input_freeze_path": _write(tmp_path / "input-freeze.json", input_freeze),
        "initial_freeze_path": _write(tmp_path / "initial.json", initial),
        "rebuttal_freeze_path": _write(tmp_path / "rebuttal.json", rebuttal),
        "judge_result_path": _write(tmp_path / "judge.json", judge),
    }
    return {
        **paths,
        "bounded": bounded,
        "reconciliation": reconciliation,
        "input_freeze": input_freeze,
        "initial": initial,
        "rebuttal": rebuttal,
        "judge": judge,
    }


def _build(tmp_path: Path, fixtures: dict[str, object]) -> dict:
    initial = fixtures["initial"]
    rebuttal = fixtures["rebuttal"]
    judge = fixtures["judge"]
    assert isinstance(initial, dict) and isinstance(rebuttal, dict) and isinstance(judge, dict)
    return build_claim_reconciliation(
        code_commit_sha="1" * 40,
        bounded_review_path=fixtures["bounded_review_path"],
        b3_reconciliation_path=fixtures["b3_reconciliation_path"],
        b4_input_freeze_path=fixtures["b4_input_freeze_path"],
        initial_freeze_path=fixtures["initial_freeze_path"],
        rebuttal_freeze_path=fixtures["rebuttal_freeze_path"],
        judge_result_path=fixtures["judge_result_path"],
        expected_initial_freeze_hash=initial["artifact_hash"],
        expected_rebuttal_freeze_hash=rebuttal["artifact_hash"],
        expected_judge_result_hash=judge["artifact_hash"],
    )


def test_claim_reconciliation_closes_only_news_gap_without_rewriting_claims(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)
    artifact = _build(tmp_path, fixtures)
    assert artifact["status"] == PASS_STATUS
    assert artifact["news_gap_closed"] is True
    assert artifact["overall_research_reopen_complete"] is False
    assert artifact["closed_reopen_reason_codes"] == [SUPERSEDED_GAP_REF]
    assert artifact["remaining_reopen_reason_codes"] == [VALUATION_GAP, PORTFOLIO_GAP]
    assert artifact["claim_reconciliation"]["material_claim_rewrite_required"] is False
    assert artifact["claim_reconciliation"]["material_claim_payloads_mutated"] is False
    assert artifact["legacy_frozen_artifacts_mutated"] is False
    assert all(row["effective_open_source_gaps"] == [] for row in artifact["candidate_reconciliations"])
    assert artifact["new_provider_reads"] == 0
    assert artifact["model_calls"] == 0


def test_historical_source_gap_drift_fails_closed(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)
    reconciliation = fixtures["reconciliation"]
    assert isinstance(reconciliation, dict)
    reconciliation = dict(reconciliation)
    candidates = [dict(row) for row in reconciliation["candidates"]]
    candidates[0]["source_gaps"] = []
    reconciliation["candidates"] = candidates
    reconciliation.pop("artifact_hash")
    reconciliation = _self_hashed(reconciliation)
    fixtures["b3_reconciliation_path"] = _write(tmp_path / "reconciliation-drift.json", reconciliation)

    input_freeze = fixtures["input_freeze"]
    assert isinstance(input_freeze, dict)
    input_freeze = dict(input_freeze)
    input_freeze["b3_reconciliation_artifact_hash"] = reconciliation["artifact_hash"]
    input_freeze.pop("artifact_hash")
    fixtures["b4_input_freeze_path"] = _write(tmp_path / "input-freeze-drift.json", _self_hashed(input_freeze))

    with pytest.raises(ReopenClaimReconciliationError, match="historical source-gap surface drift"):
        _build(tmp_path, fixtures)


def test_closure_ref_must_not_be_backfilled_into_immutable_history(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)
    initial = fixtures["initial"]
    assert isinstance(initial, dict)
    initial = dict(initial)
    initial["unexpected_backfill"] = CLOSURE_EVIDENCE_REF
    initial.pop("artifact_hash")
    initial = _self_hashed(initial)
    fixtures["initial"] = initial
    fixtures["initial_freeze_path"] = _write(tmp_path / "initial-backfilled.json", initial)
    with pytest.raises(ReopenClaimReconciliationError, match="closure evidence ref unexpectedly present"):
        _build(tmp_path, fixtures)


def test_remaining_judge_gap_cannot_be_silently_dropped(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path)
    judge = fixtures["judge"]
    assert isinstance(judge, dict)
    judge = dict(judge)
    judge["research_reopen_request"] = {
        "reason_codes": [SUPERSEDED_GAP_REF, VALUATION_GAP]
    }
    judge.pop("artifact_hash")
    judge = _self_hashed(judge)
    fixtures["judge"] = judge
    fixtures["judge_result_path"] = _write(tmp_path / "judge-drift.json", judge)
    with pytest.raises(ReopenClaimReconciliationError, match="reason-code lineage drift"):
        _build(tmp_path, fixtures)


def test_zero_call_runner_has_no_network_provider_or_model_dispatch_surface() -> None:
    source = Path("scripts/b3_reopen_bounded_news_claim_reconciliation_zero_call_v01.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "OPENAI_API_KEY",
        "load_openai_api_key",
        "StdlibResponsesTransport",
        "read_alpaca_news",
        "alpaca data news",
        "urlopen",
        "requests.get",
        "requests.post",
    )
    for marker in forbidden:
        assert marker not in source
