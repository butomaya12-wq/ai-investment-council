from __future__ import annotations

from datetime import datetime
from typing import Iterable

from pydantic import model_validator

from aic.b2.models import ComputedValue, EvidenceItem

from .models import B3Model, ResearchEvidenceBundle, ResearchEvidenceStatus, ResearchGapPlan
from .retrieve import RetrievalExecutionResult, RetrievalExecutionStatus, RetrievalProvider


class ResearchEvidenceFreezeError(ValueError):
    pass


class ResearchEvidenceFreezeResult(B3Model):
    bundle: ResearchEvidenceBundle
    evidence_items: tuple[EvidenceItem, ...]
    computed_values: tuple[ComputedValue, ...]
    excluded_evidence_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _bind_visible_payload(self):
        visible_ids = tuple(item.evidence_id for item in self.evidence_items)
        expected_ids = self.bundle.base_b2_evidence_ids + self.bundle.added_b3_evidence_ids
        if not set(visible_ids).issubset(set(expected_ids)):
            raise ValueError("freeze result contains evidence outside bundle identity")
        computed_ids = tuple(item.computed_value_id for item in self.computed_values)
        if not set(computed_ids).issubset(set(self.bundle.computed_value_ids)):
            raise ValueError("freeze result contains computed values outside bundle identity")
        return self


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _is_cutoff_eligible(item: EvidenceItem, *, cutoff: datetime) -> bool:
    if not item.knowable_at_cutoff:
        return False
    for value in (item.published_at, item.observed_at, item.as_of):
        if value is not None and value > cutoff:
            return False
    return True


def freeze_research_evidence_bundle(
    plan: ResearchGapPlan,
    retrieval_results: tuple[RetrievalExecutionResult, ...],
    *,
    bundle_id: str,
    base_b2_evidence_ids: tuple[str, ...] = (),
    base_computed_value_ids: tuple[str, ...] = (),
    base_conflict_ids: tuple[str, ...] = (),
) -> ResearchEvidenceFreezeResult:
    expected_need_ids = tuple(need.need_id for need in plan.requested_needs)
    actual_need_ids = tuple(result.request.need_id for result in retrieval_results)
    if actual_need_ids != expected_need_ids:
        raise ResearchEvidenceFreezeError(
            "retrieval results must correspond exactly to ResearchGapPlan requested_needs order"
        )

    base_evidence = list(base_b2_evidence_ids)
    added_evidence: list[str] = []
    computed_ids = list(base_computed_value_ids)
    conflict_ids = list(base_conflict_ids)
    receipt_ids: list[str] = []
    raw_hashes: list[str] = []
    visible_evidence: list[EvidenceItem] = []
    visible_computed: list[ComputedValue] = []
    excluded_evidence: list[str] = []

    any_failed = False
    any_partial = False
    any_cutoff_exclusion = False

    for result in retrieval_results:
        request = result.request
        if request.research_plan_id != plan.research_plan_id:
            raise ResearchEvidenceFreezeError("retrieval result research_plan_id drift")
        if request.candidate_id != plan.candidate_id:
            raise ResearchEvidenceFreezeError("retrieval result candidate_id drift")

        receipt_ids.append(result.receipt.provider_read_receipt_id)
        conflict_ids.extend(result.conflict_ids)
        visible_computed.extend(result.computed_values)
        computed_ids.extend(value.computed_value_id for value in result.computed_values)

        if result.status is RetrievalExecutionStatus.FAILED or result.receipt.error is not None:
            any_failed = True
        if result.status is RetrievalExecutionStatus.PARTIAL or not result.receipt.pagination_complete:
            any_partial = True

        for evidence in result.evidence_items:
            if not _is_cutoff_eligible(evidence, cutoff=plan.research_cutoff):
                excluded_evidence.append(evidence.evidence_id)
                any_cutoff_exclusion = True
                continue
            visible_evidence.append(evidence)
            raw_hashes.append(evidence.raw_content_hash)
            if request.provider is RetrievalProvider.B2_STORE:
                base_evidence.append(evidence.evidence_id)
            else:
                added_evidence.append(evidence.evidence_id)

    base_ids = _unique(base_evidence)
    added_ids = _unique(added_evidence)
    if set(base_ids) & set(added_ids):
        raise ResearchEvidenceFreezeError("B3 evidence must not overwrite B2 evidence identity")

    final_conflicts = _unique(conflict_ids)
    if any_failed:
        status = ResearchEvidenceStatus.FAILED
    elif any_cutoff_exclusion:
        status = ResearchEvidenceStatus.STALE
    elif final_conflicts:
        status = ResearchEvidenceStatus.CONFLICTED
    elif any_partial:
        status = ResearchEvidenceStatus.PARTIAL
    else:
        status = ResearchEvidenceStatus.COMPLETE

    bundle = ResearchEvidenceBundle.build(
        bundle_id=bundle_id,
        candidate_id=plan.candidate_id,
        b2_snapshot_id=plan.b2_snapshot_id,
        deep_comparison_id=plan.deep_comparison_id,
        research_cutoff=plan.research_cutoff,
        research_policy_version=plan.research_policy_version,
        model_policy_version=plan.model_policy_version,
        provider_read_receipt_ids=_unique(receipt_ids),
        base_b2_evidence_ids=base_ids,
        added_b3_evidence_ids=added_ids,
        computed_value_ids=_unique(computed_ids),
        conflict_ids=final_conflicts,
        raw_content_hashes=_unique(raw_hashes),
        status=status,
    )
    return ResearchEvidenceFreezeResult(
        bundle=bundle,
        evidence_items=tuple(visible_evidence),
        computed_values=tuple(visible_computed),
        excluded_evidence_ids=_unique(excluded_evidence),
    )
