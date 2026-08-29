from __future__ import annotations

from typing import Any, Mapping

from .independent_review import IndependentReviewDraft


class IndependentReviewReferenceError(ValueError):
    pass


_ALLOWED_REVIEW_REF_FIELDS = frozenset({"review_ref", "bundle_review_ref"})


def collect_review_refs(value: Any) -> frozenset[str]:
    refs: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for field in _ALLOWED_REVIEW_REF_FIELDS:
                review_ref = node.get(field)
                if isinstance(review_ref, str) and review_ref:
                    refs.add(review_ref)
            for child in node.values():
                visit(child)
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                visit(child)

    visit(value)
    return frozenset(refs)


def validate_review_evidence_refs(
    review: IndependentReviewDraft,
    *,
    review_input: Mapping[str, Any],
) -> None:
    allowed = collect_review_refs(review_input)
    if not allowed:
        raise IndependentReviewReferenceError("review input contains no stable review_ref values")

    used: set[str] = set()
    for attack in review.attack_results:
        for ref in attack.evidence_refs:
            if ref not in allowed:
                raise IndependentReviewReferenceError(
                    f"independent reviewer invented or escaped evidence_ref: {ref}"
                )
            used.add(ref)

    if not used:
        raise IndependentReviewReferenceError("independent reviewer used no review evidence refs")
