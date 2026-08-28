from __future__ import annotations

from aic.domain.canonical import canonical_sha256
from aic.research.run import CandidateSynthesisRuntimeResult
from aic.research.runtime import RUNTIME_VERSION, ResponsesCallResult, ResponsesUsage
from aic.research.synthesize import CandidatePacketDraft, CandidateSynthesisDraft, MaterialClaimDraft
from scripts.b3_real_synthesis import _public_summary, _validated_candidate_record


EVIDENCE_ID = "B3_SEC_NVDA_N1_1"
SOURCE_GAP = "ALPACA_NEWS_PAGINATION_INCOMPLETE"


def _draft(status: str) -> CandidateSynthesisDraft:
    claim = MaterialClaimDraft(
        claim_id="CLM_NVDA_1",
        candidate_id="NVDA",
        category="business_model",
        claim_text="Bounded evidence supports this material claim.",
        claim_kind="FACT",
        materiality="MATERIAL",
        evidence_ids=(EVIDENCE_ID,),
        computed_value_ids=(),
        conflict_ids=(),
        assumptions=(),
        support_status="SUPPORTED",
        uncertainty_note=None,
    )
    return CandidateSynthesisDraft(
        candidate_id="NVDA",
        claims=(claim,),
        packet=CandidatePacketDraft(
            business_model_claim_ids=(claim.claim_id,),
            growth_quality_claim_ids=(),
            financial_quality_claim_ids=(),
            competitive_position_claim_ids=(),
            valuation_context_claim_ids=(),
            market_context_claim_ids=(),
            capital_allocation_claim_ids=(),
            catalyst_claim_ids=(),
            risk_claim_ids=(),
            portfolio_interaction_claim_ids=(),
            material_unknowns=("News pagination remains incomplete.",),
            material_conflicts=(),
            source_gaps=(SOURCE_GAP,),
            computed_value_ids=(),
            evidence_ids=(EVIDENCE_ID,),
            research_questions_resolved=("Q1",),
            research_questions_unresolved=("Q2",),
            research_status=status,
        ),
    )


def _call(response_id: str, output_text: str) -> ResponsesCallResult:
    return ResponsesCallResult(
        runtime_version=RUNTIME_VERSION,
        response_id=response_id,
        requested_model="gpt-5.6-terra",
        effective_model="gpt-5.6-terra",
        output_text=output_text,
        output_hash=canonical_sha256({"output_text": output_text}),
        usage=ResponsesUsage(
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=0,
            cached_tokens=0,
        ),
        latency_ms=123,
    )


def test_reconstructible_candidate_record_preserves_both_drafts_without_raw_call_text() -> None:
    initial = _draft("COMPLETE")
    final = _draft("DEGRADED")
    runtime = CandidateSynthesisRuntimeResult(
        initial_call=_call("resp_initial", "initial raw output"),
        repair_call=_call("resp_repair", "repair raw output"),
        initial_draft=initial,
        draft=final,
        repair_attempts=1,
        repair_request_hash="b" * 64,
        validator_results=({"check_id": "B3-P10", "status": "PASS"},),
        initial_validator_error="PARTIAL evidence cannot yield COMPLETE",
    )

    record = _validated_candidate_record(
        candidate="NVDA",
        plan_hash="c" * 64,
        bundle_hash="d" * 64,
        evidence_status="PARTIAL",
        source_gaps=(SOURCE_GAP,),
        synthesis_input_hash="e" * 64,
        synthesis_request_hash="f" * 64,
        runtime=runtime,
    )

    assert record["initial_draft"]["packet"]["research_status"] == "COMPLETE"
    assert record["validated_draft"]["packet"]["research_status"] == "DEGRADED"
    assert record["initial_draft_hash"] == canonical_sha256(initial)
    assert record["draft_hash"] == canonical_sha256(final)
    assert record["repair_request_hash"] == "b" * 64
    assert record["initial_call"]["response_id"] == "resp_initial"
    assert record["repair_call"]["response_id"] == "resp_repair"
    assert "output_text" not in record["initial_call"]
    assert "output_text" not in record["repair_call"]
    assert record["reconstructibility_status"] == "PASS"

    unhashed = dict(record)
    record_hash = unhashed.pop("record_hash")
    assert record_hash == canonical_sha256(unhashed)


def test_public_summary_excludes_research_draft_payloads_and_call_payloads(tmp_path) -> None:
    initial = _draft("COMPLETE")
    final = _draft("DEGRADED")
    runtime = CandidateSynthesisRuntimeResult(
        initial_call=_call("resp_initial", "initial raw output"),
        repair_call=_call("resp_repair", "repair raw output"),
        initial_draft=initial,
        draft=final,
        repair_attempts=1,
        repair_request_hash="b" * 64,
        validator_results=({"check_id": "B3-P10", "status": "PASS"},),
        initial_validator_error="PARTIAL evidence cannot yield COMPLETE",
    )
    record = _validated_candidate_record(
        candidate="NVDA",
        plan_hash="c" * 64,
        bundle_hash="d" * 64,
        evidence_status="PARTIAL",
        source_gaps=(SOURCE_GAP,),
        synthesis_input_hash="e" * 64,
        synthesis_request_hash="f" * 64,
        runtime=runtime,
    )
    artifact = {
        "artifact_version": "B3_SYNTHESIS_BATCH_ARTIFACT_v0_2",
        "run_class": "B3_REAL_CANDIDATE_SYNTHESIS_RUNTIME",
        "handoff_hash": "1" * 64,
        "planner_artifact_hash": "2" * 64,
        "retrieval_artifact_hash": "3" * 64,
        "research_policy_version": "RESEARCH_POLICY_vB3_0_1",
        "model_candidate": "M1",
        "candidates": [record],
        "reconstructibility_status": "PASS",
        "canonical_persistence": "BLOCKED_MANDATE_LINEAGE",
        "persistence_blocker": "MANDATE_VERSION_UNBOUND",
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)

    summary = _public_summary(artifact, output_path=tmp_path / "batch.json")
    candidate = summary["candidates"][0]
    assert "initial_draft" not in candidate
    assert "validated_draft" not in candidate
    assert "initial_call" not in candidate
    assert "repair_call" not in candidate
    assert candidate["record_hash"] == record["record_hash"]
    assert summary["reconstructibility_status"] == "PASS"
