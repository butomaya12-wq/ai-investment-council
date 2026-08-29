import pytest

from aic.research.independent_review import ATTACK_CLASSES, IndependentReviewDraft
from aic.research.review_refs import (
    IndependentReviewReferenceError,
    collect_review_refs,
    validate_review_evidence_refs,
)


def _review(ref: str) -> IndependentReviewDraft:
    return IndependentReviewDraft.model_validate(
        {
            "review_status": "PASS",
            "attack_results": [
                {
                    "attack_class": attack_class,
                    "status": "PASS",
                    "finding": "Frozen evidence shows no material acceptance gap.",
                    "evidence_refs": [ref],
                    "materiality_rationale": "The referenced frozen evidence supports PASS.",
                }
                for attack_class in ATTACK_CLASSES
            ],
            "material_gap_summary": [],
            "inconclusive_summary": [],
        }
    )


def test_collect_review_refs_is_recursive_and_ignores_plain_strings() -> None:
    review_input = {
        "outer": {"review_ref": "STATIC:one", "value": "EVIDENCE:not-a-ref"},
        "items": [
            {"review_ref": "EVAL:M2:E10:abc"},
            {"nested": {"review_ref": "PACKET:NVDA:def"}},
        ],
    }
    assert collect_review_refs(review_input) == frozenset(
        {"STATIC:one", "EVAL:M2:E10:abc", "PACKET:NVDA:def"}
    )


def test_review_evidence_refs_must_exist_in_frozen_review_input() -> None:
    review_input = {"evidence": [{"review_ref": "STATIC:allowed", "value": True}]}
    validate_review_evidence_refs(_review("STATIC:allowed"), review_input=review_input)

    with pytest.raises(IndependentReviewReferenceError, match="invented or escaped"):
        validate_review_evidence_refs(_review("STATIC:invented"), review_input=review_input)


def test_review_evidence_ref_validation_fails_closed_when_input_has_no_refs() -> None:
    with pytest.raises(IndependentReviewReferenceError, match="no stable review_ref"):
        validate_review_evidence_refs(_review("STATIC:any"), review_input={"safe": True})
