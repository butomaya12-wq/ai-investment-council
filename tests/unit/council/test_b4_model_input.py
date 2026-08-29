from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from aic.council.input_bundle import build_council_input_freeze
from aic.council.model_input import CouncilModelInputError, build_initial_model_inputs
from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import CANDIDATE_PACKET_V1, MATERIAL_CLAIM_V1
from aic.research.event_policy import build_event_research_policy
from aic.research.handoff import B2RealEventHandoff, EXPECTED_METRICS, EXPECTED_TOP3
from aic.research.policy_refs import build_model_policy_reference, build_research_policy_reference


MANDATE = "TEST_MANDATE_V1"


def _handoff() -> B2RealEventHandoff:
    candidates = []
    for index, candidate in enumerate(EXPECTED_TOP3, start=1):
        metrics = [
            {
                "computed_value_id": f"B2_{candidate}_{metric}",
                "metric_id": metric,
                "value": str(index),
                "unit": "ratio" if metric != "adv_20s" else "USD",
            }
            for metric in EXPECTED_METRICS
        ]
        candidates.append(
            {
                "symbol": candidate,
                "sec_accession": f"000000000{index}-26-00000{index}",
                "sec_source_uri": f"https://www.sec.gov/Archives/edgar/data/{index}/filing.htm",
                "sec_evidence_id": f"B2_SEC_{candidate}",
                "metrics": metrics,
            }
        )
    payload = {
        "handoff_version": "B2_REAL_EVENT_HANDOFF_v0_1",
        "source_run_id": "TEST_B2_RUN",
        "source_evidence_index": "TEST_INDEX",
        "b2_decision_cutoff": "2026-08-27T20:00:00Z",
        "research_cutoff": "2026-08-28T17:34:00Z",
        "b2_snapshot_ref": "B2_TEST_SNAPSHOT",
        "deep_comparison_ref": "B2_TEST_DEEP_COMPARISON",
        "top3": list(EXPECTED_TOP3),
        "candidates": candidates,
    }
    payload["handoff_hash"] = canonical_sha256(payload)
    return B2RealEventHandoff.model_validate(payload)


def _claim(candidate: str):
    return MATERIAL_CLAIM_V1.from_unhashed(
        claim_id=f"B3_CLAIM_{candidate}",
        candidate_id=candidate,
        category="business_model",
        claim_text=f"{candidate} business model is supported.",
        claim_kind="FACT",
        materiality="MATERIAL",
        evidence_ids=[f"B3_EVID_{candidate}"],
        computed_value_ids=[],
        conflict_ids=[],
        assumptions=[],
        support_status="SUPPORTED",
        uncertainty_note=None,
    )


def _packet(candidate: str, claim_id: str, handoff: B2RealEventHandoff):
    research_policy_ref = build_research_policy_reference(build_event_research_policy())
    model_policy_ref = build_model_policy_reference()
    computed_ids = [item.computed_value_id for item in handoff.candidate(candidate).metrics]
    return CANDIDATE_PACKET_V1.from_unhashed(
        candidate_packet_id=f"B3_PACKET_{candidate}",
        candidate_id=candidate,
        symbol=candidate,
        issuer_id=f"ISSUER_{candidate}",
        b2_snapshot_id=handoff.b2_snapshot_ref,
        research_snapshot_id=f"B3_RESEARCH_{candidate}",
        mandate_version=MANDATE,
        deep_comparison_id=handoff.deep_comparison_ref,
        research_policy_ref=research_policy_ref.model_dump(mode="json", exclude_none=False),
        research_model_policy_ref=model_policy_ref.model_dump(mode="json", exclude_none=False),
        model_run_ref=f"B3_MODEL_RUN_{candidate}",
        business_model_claim_ids=[claim_id],
        growth_quality_claim_ids=[],
        financial_quality_claim_ids=[],
        competitive_position_claim_ids=[],
        valuation_context_claim_ids=[],
        market_context_claim_ids=[],
        capital_allocation_claim_ids=[],
        catalyst_claim_ids=[],
        risk_claim_ids=[],
        portfolio_interaction_claim_ids=[],
        material_unknowns=["Recent news pagination is incomplete."],
        material_conflicts=[],
        source_gaps=["ALPACA_NEWS_PAGINATION_INCOMPLETE"],
        computed_value_ids=computed_ids,
        evidence_ids=[f"B3_EVID_{candidate}"],
        research_questions_resolved=["Q1"],
        research_questions_unresolved=["Q2"],
        research_status="INCOMPLETE",
    )


def _reconciliation(handoff: B2RealEventHandoff) -> dict:
    candidates = []
    for index, candidate in enumerate(EXPECTED_TOP3, start=2):
        claim = _claim(candidate)
        packet = _packet(candidate, claim.claim_id, handoff)
        candidates.append(
            {
                "candidate": candidate,
                "status": "CANONICAL_RECONCILED",
                "bundle_hash": str(index) * 64,
                "source_gaps": ["ALPACA_NEWS_PAGINATION_INCOMPLETE"],
                "material_claims": [claim.model_dump(mode="json", exclude_none=False, warnings=False)],
                "candidate_packet": packet.model_dump(mode="json", exclude_none=False, warnings=False),
                "reconstructibility_status": "PASS",
            }
        )
    payload = {
        "artifact_version": "B3_SELECTED_MODEL_RECONCILIATION_ARTIFACT_v0_1",
        "run_class": "B3_SELECTED_MODEL_REAL_CANDIDATE_RECONCILIATION",
        "handoff_hash": handoff.handoff_hash,
        "mandate_version": MANDATE,
        "candidates": candidates,
        "reconstructibility_status": "PASS",
        "canonical_reconciliation": "ALL_THREE_SELECTED_MODEL_CANDIDATE_PACKETS_RECONCILED",
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def _freeze(reconciliation: dict, handoff: B2RealEventHandoff):
    return build_council_input_freeze(
        reconciliation,
        expected_handoff_hash=handoff.handoff_hash,
        mandate_version=MANDATE,
        created_at=datetime(2026, 8, 29, 17, 0, tzinfo=UTC),
    )


def test_real_shape_model_inputs_bind_packet_claims_metrics_and_gap_state() -> None:
    handoff = _handoff()
    reconciliation = _reconciliation(handoff)
    freeze = _freeze(reconciliation, handoff)
    inputs = build_initial_model_inputs(freeze, reconciliation, handoff)
    assert tuple(item.candidate_id for item in inputs) == EXPECTED_TOP3
    assert all(len(item.material_claims) == 1 for item in inputs)
    assert all(len(item.computed_values) == 5 for item in inputs)
    assert all(item.data_gap_refs == ("ALPACA_NEWS_PAGINATION_INCOMPLETE",) for item in inputs)
    assert len({item.model_input_hash for item in inputs}) == 3


def test_changed_reconciliation_cannot_reuse_existing_b4_freeze() -> None:
    handoff = _handoff()
    reconciliation = _reconciliation(handoff)
    freeze = _freeze(reconciliation, handoff)
    changed = deepcopy(reconciliation)
    changed["candidates"][0]["source_gaps"] = []
    changed["artifact_hash"] = canonical_sha256(changed, exclude_fields=("artifact_hash",))
    with pytest.raises(CouncilModelInputError, match="does not bind supplied B3 reconciliation"):
        build_initial_model_inputs(freeze, changed, handoff)


def test_hidden_gap_fails_when_freeze_is_built_from_same_changed_reconciliation() -> None:
    handoff = _handoff()
    changed = _reconciliation(handoff)
    changed["candidates"][0]["source_gaps"] = []
    changed["artifact_hash"] = canonical_sha256(changed, exclude_fields=("artifact_hash",))
    freeze = _freeze(changed, handoff)
    with pytest.raises(CouncilModelInputError, match="source-gap lineage mismatch"):
        build_initial_model_inputs(freeze, changed, handoff)
