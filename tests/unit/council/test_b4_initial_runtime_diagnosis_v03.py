from __future__ import annotations

from aic.council.initial_runtime_diagnosis_v03 import (
    EXPECTED_RECEIPT_VERSION,
    missing_inference_provenance,
)
from aic.council.proposal import InitialCouncilOpinionProposal


def _proposal(*, source_refs: tuple[str, ...]) -> InitialCouncilOpinionProposal:
    return InitialCouncilOpinionProposal.model_validate(
        {
            "opinion_id": "OP1",
            "candidate_id": "NVDA",
            "lane": "BULL",
            "council_input_bundle_hash": "bundle-hash",
            "candidate_packet_hash": "packet-hash",
            "mandate_version": "M1",
            "council_policy_version": "C1",
            "model_policy_version": "MP1",
            "model_run_ref": "RUN1",
            "proposed_claims": [
                {
                    "claim_local_ref": "C1",
                    "candidate_id": "NVDA",
                    "lane": "BULL",
                    "claim_type": "ARGUMENT",
                    "claim_text": "Bounded inference claim",
                    "source_material_claim_ids": list(source_refs),
                    "computed_value_ids": [],
                    "conflict_ids": [],
                    "claim_kind": "INFERENCE",
                    "support_status": "SUPPORTED",
                    "materiality": "MATERIAL",
                }
            ],
            "primary_claim_ids": ["C1"],
            "critical_assumption_claim_ids": [],
            "falsifier_claim_ids": [],
            "material_unknown_refs": [],
            "material_conflict_refs": [],
            "research_reopen_required": False,
            "research_reopen_reason_codes": [],
            "role_boundary_status": "VALID",
        }
    )


def test_v03_receipt_version_is_replay_specific() -> None:
    assert EXPECTED_RECEIPT_VERSION == "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_3"


def test_missing_inference_provenance_detected() -> None:
    gaps = missing_inference_provenance(_proposal(source_refs=()))
    assert len(gaps) == 1
    assert gaps[0].claim_local_ref == "C1"
    assert gaps[0].claim_kind == "INFERENCE"
    assert gaps[0].source_material_claim_ids == ()
    assert gaps[0].computed_value_ids == ()
    assert gaps[0].conflict_ids == ()


def test_inference_with_frozen_source_ref_is_not_gap() -> None:
    assert missing_inference_provenance(
        _proposal(source_refs=("NVDA_B3_CLAIM_001",))
    ) == ()
