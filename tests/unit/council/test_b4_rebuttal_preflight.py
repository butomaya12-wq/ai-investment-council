from __future__ import annotations

from datetime import UTC, datetime

from aic.council.initial_runtime import (
    INITIAL_COUNCIL_FREEZE_ARTIFACT_VERSION,
    INITIAL_COUNCIL_FROZEN_STATUS,
)
from aic.council.model_input import (
    B4ComputedValueView,
    InitialCouncilModelInput,
    MODEL_INPUT_VERSION,
)
from aic.council.model_policy import MODEL_POLICY_VERSION
from aic.council.models import (
    CouncilClaimKind,
    CouncilClaimType,
    CouncilInputBundle,
    CouncilInputFreezeArtifact,
    CouncilLane,
    CouncilMateriality,
    CouncilSupportStatus,
    ProposedCouncilClaim,
)
from aic.council.policy import COUNCIL_POLICY_VERSION
from aic.council.promotion import promote_initial_council_opinion
from aic.council.proposal import InitialCouncilOpinionProposal, RoleBoundaryStatus
from aic.council.rebuttal_preflight import (
    EXPECTED_REBUTTAL_EVAL_CASE_IDS,
    REBUTTAL_SOURCE_PREFLIGHT_STATUS,
    build_rebuttal_frozen_contexts,
    build_rebuttal_source_request_preflight,
)
from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import MATERIAL_CLAIM_V1


CANDIDATES = ("NVDA", "MSFT", "META")


def _bundle(candidate: str, index: int) -> CouncilInputBundle:
    return CouncilInputBundle.from_unhashed(
        bundle_id=f"B4_INPUT_{candidate}",
        candidate_id=candidate,
        candidate_packet_id=f"B3_PACKET_{candidate}",
        candidate_packet_hash=str(index + 1) * 64,
        research_snapshot_id=f"B3_RESEARCH_{candidate}",
        research_snapshot_hash=str(index + 4) * 64,
        b2_snapshot_id="B2_SNAPSHOT",
        deep_comparison_id="B2_DEEP",
        mandate_version="ALPACA_COMPETITION_V1_2026_08_29",
        council_policy_version=COUNCIL_POLICY_VERSION,
        judge_policy_version="JUDGE_POLICY_vB4_0_1",
        model_policy_version=MODEL_POLICY_VERSION,
        allowed_material_claim_ids=(f"SRC_{candidate}",),
        allowed_computed_value_ids=(f"CV_{candidate}",),
        allowed_conflict_ids=(),
        shared_portfolio_context_refs=(),
        created_at=datetime(2026, 8, 29, 16, index, tzinfo=UTC),
    )


def _freeze() -> CouncilInputFreezeArtifact:
    bundles = tuple(_bundle(candidate, index) for index, candidate in enumerate(CANDIDATES))
    values = {
        "artifact_version": "B4_COUNCIL_INPUT_FREEZE_ARTIFACT_v0_1",
        "run_class": "B4_LOCAL_ZERO_CALL_INPUT_FREEZE",
        "b3_reconciliation_artifact_hash": "a" * 64,
        "b2_handoff_hash": "b" * 64,
        "mandate_version": "ALPACA_COMPETITION_V1_2026_08_29",
        "candidate_order": CANDIDATES,
        "bundles": bundles,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    provisional = CouncilInputFreezeArtifact.model_construct(**values, artifact_hash="0" * 64)
    return CouncilInputFreezeArtifact(
        **values,
        artifact_hash=canonical_sha256(provisional, exclude_fields=("artifact_hash",)),
    )


def _source_claim(candidate: str):
    return MATERIAL_CLAIM_V1.from_unhashed(
        claim_id=f"SRC_{candidate}",
        candidate_id=candidate,
        category="financial_quality",
        claim_text="Frozen qualitative fact with no numeric literal.",
        claim_kind="FACT",
        materiality="MATERIAL",
        evidence_ids=[f"EVID_{candidate}"],
        computed_value_ids=[],
        conflict_ids=[],
        assumptions=[],
        support_status="SUPPORTED",
        uncertainty_note=None,
    )


def _initial_input(bundle: CouncilInputBundle) -> InitialCouncilModelInput:
    candidate = bundle.candidate_id
    source = _source_claim(candidate)
    return InitialCouncilModelInput.from_unhashed(
        model_input_version=MODEL_INPUT_VERSION,
        candidate_id=candidate,
        council_input_bundle=bundle.model_dump(mode="json", exclude_none=False),
        candidate_packet={"candidate_id": candidate},
        material_claims=(source.model_dump(mode="json", exclude_none=False, warnings=False),),
        computed_values=(
            B4ComputedValueView(
                computed_value_id=f"CV_{candidate}",
                metric_id=f"M_{candidate}",
                value="42",
                unit="count",
            ),
        ),
        data_gap_refs=("GAP:TEST",),
    )


def _proposal(bundle: CouncilInputBundle, lane: CouncilLane) -> InitialCouncilOpinionProposal:
    candidate = bundle.candidate_id
    local_ref = f"{candidate}_{lane.value}_C1"
    claim = ProposedCouncilClaim(
        claim_local_ref=local_ref,
        candidate_id=candidate,
        lane=lane,
        claim_type=CouncilClaimType.ARGUMENT,
        claim_text="Frozen evidence supports this bounded Initial inference.",
        source_material_claim_ids=(f"SRC_{candidate}",),
        computed_value_ids=(),
        conflict_ids=(),
        claim_kind=CouncilClaimKind.INFERENCE,
        support_status=CouncilSupportStatus.SUPPORTED,
        materiality=CouncilMateriality.MATERIAL,
    )
    return InitialCouncilOpinionProposal(
        opinion_id=f"OP_{candidate}_{lane.value}",
        candidate_id=candidate,
        lane=lane,
        council_input_bundle_hash=bundle.bundle_hash,
        candidate_packet_hash=bundle.candidate_packet_hash,
        mandate_version=bundle.mandate_version,
        council_policy_version=bundle.council_policy_version,
        model_policy_version=bundle.model_policy_version,
        model_run_ref=f"MODEL_RUN_{candidate}_{lane.value}",
        proposed_claims=(claim,),
        primary_claim_ids=(local_ref,),
        critical_assumption_claim_ids=(),
        falsifier_claim_ids=(),
        material_unknown_refs=("GAP:TEST",),
        material_conflict_refs=(),
        research_reopen_required=True,
        research_reopen_reason_codes=("TEST_GAP",),
        role_boundary_status=RoleBoundaryStatus.VALID,
    )


def _record(bundle: CouncilInputBundle, lane: CouncilLane) -> dict:
    proposal = _proposal(bundle, lane)
    result = promote_initial_council_opinion(
        proposal,
        bundle=bundle,
        expected_lane=lane,
        source_claims={f"SRC_{bundle.candidate_id}": _source_claim(bundle.candidate_id)},
        computed_value_values={f"CV_{bundle.candidate_id}": "42"},
        allowed_data_gap_refs=("GAP:TEST",),
        required_data_gap_refs=("GAP:TEST",),
        frozen_at=datetime(2026, 8, 29, 17, 0, tzinfo=UTC),
    )
    structured = proposal.model_dump(mode="json", exclude_none=False)
    opinion = result.council_opinion.model_dump(mode="json", exclude_none=False, warnings=False)
    record = {
        "candidate_id": bundle.candidate_id,
        "lane": lane.value,
        "structured_output": structured,
        "structured_output_hash": canonical_sha256(structured),
        "material_claims": [
            claim.model_dump(mode="json", exclude_none=False, warnings=False)
            for claim in result.material_claims
        ],
        "claim_metadata": [
            item.model_dump(mode="json", exclude_none=False)
            for item in result.claim_metadata
        ],
        "council_opinion": opinion,
        "council_opinion_hash": canonical_sha256(result.council_opinion),
    }
    record["record_hash"] = canonical_sha256(record)
    return record


def _initial_freeze(freeze: CouncilInputFreezeArtifact) -> dict:
    records = []
    for bundle in freeze.bundles:
        for lane in (CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM):
            records.append(_record(bundle, lane))
    artifact = {
        "artifact_version": INITIAL_COUNCIL_FREEZE_ARTIFACT_VERSION,
        "status": INITIAL_COUNCIL_FROZEN_STATUS,
        "b4_input_freeze_artifact_hash": freeze.artifact_hash,
        "candidate_order": list(freeze.candidate_order),
        "initial_opinion_count": 9,
        "processed_records": records,
        "dispatch_attempts": 9,
        "model_calls": 9,
        "automatic_repair_calls": 0,
        "initial_freeze_barrier": True,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def _eval_plan() -> dict:
    plan = {
        "plan_version": "B4_STAGE_EVAL_PLAN_v0_1",
        "full_eval_paid_call_count_max": 69,
        "stages": {
            "REBUTTAL": {
                "candidate_keys": ["R1", "R2", "R3"],
                "case_ids": list(EXPECTED_REBUTTAL_EVAL_CASE_IDS),
                "paid_call_count_max": 12,
            }
        },
    }
    plan["plan_hash"] = canonical_sha256(plan)
    return plan


def test_rebuttal_source_preflight_builds_three_frozen_contexts_and_nine_ladder_variants() -> None:
    freeze = _freeze()
    initial_inputs = tuple(_initial_input(bundle) for bundle in freeze.bundles)
    initial_freeze = _initial_freeze(freeze)
    contexts = build_rebuttal_frozen_contexts(
        initial_freeze=initial_freeze,
        freeze=freeze,
        initial_model_inputs=initial_inputs,
        expected_initial_freeze_hash=initial_freeze["artifact_hash"],
    )
    assert tuple(context["candidate_id"] for context in contexts) == CANDIDATES
    for context in contexts:
        assert context["required_unknown_refs"] == ["GAP:TEST"]
        assert context["allowed_uncertainty_refs"] == ["GAP:TEST"]
        assert set(context["opposing_claim_ids_by_lane"]) == {"BULL", "BEAR", "RED_TEAM"}
        assert all(len(ids) == 2 for ids in context["opposing_claim_ids_by_lane"].values())
        assert context["context_hash"] == canonical_sha256(context, exclude_fields=("context_hash",))

    preflight = build_rebuttal_source_request_preflight(
        contexts=contexts,
        freeze=freeze,
        code_commit_sha="c" * 40,
        eval_plan=_eval_plan(),
    )
    assert preflight["status"] == REBUTTAL_SOURCE_PREFLIGHT_STATUS
    assert preflight["request_variant_count"] == 9
    assert preflight["production_rebuttal_calls_after_selection"] == 3
    assert preflight["model_selection_required"] is True
    assert preflight["selected_candidate"] is None
    assert preflight["eval_candidate_keys"] == ["R1", "R2", "R3"]
    assert preflight["eval_case_ids"] == ["E4", "E8", "E13", "E16"]
    assert preflight["eval_paid_call_count_max"] == 12
    assert {row["candidate_key"] for row in preflight["request_variants"]} == {"R1", "R2", "R3"}
    assert all(row["max_output_tokens"] == 6144 for row in preflight["request_variants"])
    assert preflight["model_calls"] == 0
    assert preflight["provider_reads"] == 0
    assert preflight["broker_writes"] == 0
    assert preflight["alpaca_orders"] == 0
    assert preflight["live_money"] == "PROHIBITED"
    assert preflight["paid_eval_authorized"] is False
    assert preflight["production_rebuttal_authorized"] is False
    assert preflight["judge_authorized"] is False
    assert preflight["artifact_hash"] == canonical_sha256(preflight, exclude_fields=("artifact_hash",))


def test_rebuttal_source_preflight_script_is_zero_call_surface() -> None:
    from pathlib import Path

    text = Path("scripts/b4_rebuttal_source_preflight_v01.py").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in text
    assert "StdlibResponsesTransport" not in text
    assert "--execute" not in text
    assert "EXPECTED_INITIAL_FREEZE_HASH" in text
    assert "paid_eval_authorized" in text
    assert "production_rebuttal_authorized" in text
