from copy import deepcopy
from datetime import UTC, datetime

import pytest

from aic.council.input_bundle import CouncilInputFreezeError, build_council_input_freeze
from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import CANDIDATE_PACKET_V1, MATERIAL_CLAIM_V1
from aic.research.event_policy import build_event_research_policy
from aic.research.mandate import COMPETITION_MANDATE_VERSION
from aic.research.policy_refs import build_model_policy_reference, build_research_policy_reference


# Synthetic B4 fixtures intentionally instantiate the already-frozen B1 canonical
# models whose list fields are deep-frozen to tuples. Pydantic emits the same known
# serializer warning that is already tracked in B3. Keep the suppression local to
# this fixture file so new warnings elsewhere remain visible to exact CI.
pytestmark = pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")

TOP3 = ("NVDA", "MSFT", "META")
HANDOFF_HASH = "1" * 64


def _claim(candidate: str):
    return MATERIAL_CLAIM_V1.from_unhashed(
        claim_id=f"B3_CLAIM_{candidate}",
        candidate_id=candidate,
        category="business_model",
        claim_text=f"{candidate} business model is supported by frozen evidence.",
        claim_kind="FACT",
        materiality="MATERIAL",
        evidence_ids=[f"EVID_{candidate}"],
        computed_value_ids=[f"CV_{candidate}"],
        conflict_ids=[],
        assumptions=[],
        support_status="SUPPORTED",
        uncertainty_note=None,
    )


def _packet(candidate: str, claim_id: str):
    research_policy_ref = build_research_policy_reference(build_event_research_policy())
    model_policy_ref = build_model_policy_reference()
    return CANDIDATE_PACKET_V1.from_unhashed(
        candidate_packet_id=f"B3_PACKET_{candidate}",
        candidate_id=candidate,
        symbol=candidate,
        issuer_id=f"ISSUER_{candidate}",
        b2_snapshot_id="B2_SNAPSHOT",
        research_snapshot_id=f"B3_RESEARCH_{candidate}",
        mandate_version=COMPETITION_MANDATE_VERSION,
        deep_comparison_id="B2_DEEP_COMPARISON",
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
        material_unknowns=["Recent-news pagination remains incomplete."],
        material_conflicts=[],
        source_gaps=["ALPACA_NEWS_PAGINATION_INCOMPLETE"],
        computed_value_ids=[f"CV_{candidate}"],
        evidence_ids=[f"EVID_{candidate}"],
        research_questions_resolved=["Q1"],
        research_questions_unresolved=["Q2"],
        research_status="INCOMPLETE",
    )


def _reconciliation():
    candidates = []
    for index, candidate in enumerate(TOP3, start=2):
        claim = _claim(candidate)
        packet = _packet(candidate, claim.claim_id)
        candidates.append(
            {
                "candidate": candidate,
                "status": "CANONICAL_RECONCILED",
                "bundle_hash": str(index) * 64,
                "material_claims": [
                    claim.model_dump(mode="json", exclude_none=False, warnings=False)
                ],
                "candidate_packet": packet.model_dump(
                    mode="json", exclude_none=False, warnings=False
                ),
                "reconstructibility_status": "PASS",
            }
        )
    artifact = {
        "artifact_version": "B3_SELECTED_MODEL_RECONCILIATION_ARTIFACT_v0_1",
        "run_class": "B3_SELECTED_MODEL_REAL_CANDIDATE_RECONCILIATION",
        "handoff_hash": HANDOFF_HASH,
        "mandate_version": COMPETITION_MANDATE_VERSION,
        "candidates": candidates,
        "reconstructibility_status": "PASS",
        "canonical_reconciliation": "ALL_THREE_SELECTED_MODEL_CANDIDATE_PACKETS_RECONCILED",
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def _rehash(artifact):
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    return artifact


def test_real_shape_freeze_requires_exact_three_and_preserves_incomplete_packet_authority() -> None:
    frozen = build_council_input_freeze(
        _reconciliation(),
        expected_handoff_hash=HANDOFF_HASH,
        mandate_version=COMPETITION_MANDATE_VERSION,
        created_at=datetime(2026, 8, 29, 15, 30, tzinfo=UTC),
    )
    assert frozen.candidate_order == TOP3
    assert [bundle.candidate_id for bundle in frozen.bundles] == list(TOP3)
    assert len({bundle.bundle_hash for bundle in frozen.bundles}) == 3
    assert all(bundle.allowed_material_claim_ids == (f"B3_CLAIM_{bundle.candidate_id}",) for bundle in frozen.bundles)
    assert all(bundle.allowed_computed_value_ids == (f"CV_{bundle.candidate_id}",) for bundle in frozen.bundles)
    assert frozen.model_calls == 0
    assert frozen.provider_reads == 0
    assert frozen.broker_writes == 0
    assert frozen.alpaca_orders == 0
    assert frozen.live_money == "PROHIBITED"


def test_candidate_packet_hash_mismatch_fails_before_b4_run() -> None:
    artifact = deepcopy(_reconciliation())
    artifact["candidates"][0]["candidate_packet"]["packet_hash"] = "0" * 64
    _rehash(artifact)
    with pytest.raises(CouncilInputFreezeError, match="canonical B3 object validation failed"):
        build_council_input_freeze(
            artifact,
            expected_handoff_hash=HANDOFF_HASH,
            mandate_version=COMPETITION_MANDATE_VERSION,
            created_at=datetime(2026, 8, 29, 15, 30, tzinfo=UTC),
        )


def test_missing_candidate_fails_closed() -> None:
    artifact = deepcopy(_reconciliation())
    artifact["candidates"].pop()
    _rehash(artifact)
    with pytest.raises(CouncilInputFreezeError, match="exact frozen top-3"):
        build_council_input_freeze(
            artifact,
            expected_handoff_hash=HANDOFF_HASH,
            mandate_version=COMPETITION_MANDATE_VERSION,
            created_at=datetime(2026, 8, 29, 15, 30, tzinfo=UTC),
        )


def test_reconciliation_handoff_mismatch_fails_closed() -> None:
    with pytest.raises(CouncilInputFreezeError, match="frozen B2 handoff"):
        build_council_input_freeze(
            _reconciliation(),
            expected_handoff_hash="9" * 64,
            mandate_version=COMPETITION_MANDATE_VERSION,
            created_at=datetime(2026, 8, 29, 15, 30, tzinfo=UTC),
        )
