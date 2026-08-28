from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from aic.b2.models import EvidenceItem, ProviderReadReceipt
from aic.domain.canonical import canonical_sha256
from aic.research.evidence_bundle import freeze_research_evidence_bundle
from aic.research.models import (
    CurrentEvidenceStatus,
    ResearchEvidenceStatus,
    ResearchGapPlan,
    ResearchNeed,
    ResearchNeedType,
    ResearchQuestion,
    SecFilingSectionParameters,
)
from aic.research.policy import RESEARCH_POLICY_VERSION, ResearchPolicy
from aic.research.retrieve import (
    RetrievalAction,
    RetrievalExecutionError,
    RetrievalExecutionResult,
    RetrievalExecutionStatus,
    RetrievalProvider,
    RetrievalRequest,
    execute_retrieval_plan,
)


CUTOFF = datetime(2026, 8, 28, 17, 34, tzinfo=UTC)


def _policy() -> ResearchPolicy:
    return ResearchPolicy(
        policy_version=RESEARCH_POLICY_VERSION,
        allowed_need_types=tuple(ResearchNeedType),
        max_needs_per_candidate=6,
        max_items_per_need=5,
        max_total_evidence_items_per_candidate=30,
        allowed_source_tiers=("B2", "SEC", "ALPACA_NEWS"),
        allowed_sec_forms=("10-K", "10-Q", "8-K"),
        allowed_sec_sections=("Business", "Risk Factors", "MD&A", "Material 8-K"),
        company_ir_policy_ref=None,
        news_window_policy_ref="NEWS_WINDOW_v1",
        material_claim_categories=("risk",),
        inference_rule="Bind inference to evidence.",
        unknown_rule="Keep unknowns explicit.",
        conflict_rule="Keep conflicts explicit.",
        numeric_claim_rule="No model arithmetic.",
        research_cutoff_rule="No future evidence.",
        max_model_calls_per_candidate=3,
        repair_attempt_limit=1,
        failure_behavior="Bounded failure.",
    )


def _plan() -> ResearchGapPlan:
    question = ResearchQuestion(
        question_id="q1",
        category="risk",
        question_text="What filing risk evidence remains material?",
        why_material="Needed for evidence-bounded later Council review.",
        current_evidence_status=CurrentEvidenceStatus.MISSING,
    )
    need = ResearchNeed(
        need_id="n1",
        question_id="q1",
        need_type=ResearchNeedType.NEED_SEC_FILING_SECTION,
        parameters=SecFilingSectionParameters(
            filing_accession="0001045810-26-000021",
            sections=("Risk Factors",),
        ),
        max_items=1,
        expected_evidence_role="primary",
    )
    return ResearchGapPlan(
        research_plan_id="plan-nvda-1",
        candidate_id="NVDA",
        b2_snapshot_id="b2-event",
        deep_comparison_id="cmp-event",
        research_policy_version=RESEARCH_POLICY_VERSION,
        model_policy_version="MODEL_POLICY_vB3_0_1",
        research_cutoff=CUTOFF,
        material_questions=(question,),
        requested_needs=(need,),
    )


@dataclass(frozen=True)
class _SecAdapter:
    provider: RetrievalProvider = RetrievalProvider.SEC
    future: bool = False
    pagination_complete: bool = True

    def execute(
        self,
        *,
        request: RetrievalRequest,
        research_cutoff: datetime,
    ) -> RetrievalExecutionResult:
        assert request.action is RetrievalAction.GET_FILING_SECTIONS
        as_of = research_cutoff + timedelta(seconds=1) if self.future else research_cutoff - timedelta(days=1)
        receipt_id = f"receipt-{request.need_id}"
        receipt = ProviderReadReceipt(
            provider_read_receipt_id=receipt_id,
            provider=RetrievalProvider.SEC.value,
            endpoint_class=request.action.value,
            request_start=research_cutoff + timedelta(hours=1),
            response_received_at=research_cutoff + timedelta(hours=1, seconds=1),
            request_parameters_hash=canonical_sha256(request.parameters),
            pagination_complete=self.pagination_complete,
            raw_payload_hash=canonical_sha256({"section": "bounded SEC filing text"}),
            record_count=1,
            http_status=200,
            error=None,
        )
        evidence = EvidenceItem(
            evidence_id=f"evidence-{request.need_id}",
            provider="SEC",
            source_type="SEC_FILING_SECTION",
            source_uri="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm",
            request_parameters_ref=request.request_hash,
            entity_id=request.candidate_id,
            field_or_claim="Risk Factors",
            raw_value_or_record_ref="section:Risk Factors",
            normalized_value="bounded SEC filing text",
            published_at=as_of,
            observed_at=None,
            retrieved_at=research_cutoff + timedelta(hours=1),
            as_of=as_of,
            freshness_rule_id="B3_SEC_CUTOFF_V1",
            knowable_at_cutoff=not self.future,
            authoritative_for=("risk",),
            conflict_group=None,
            provider_read_receipt_id=receipt_id,
            raw_content_hash=canonical_sha256({"content": "bounded SEC filing text"}),
            normalization_version="B3_SEC_SECTION_v0_1",
        )
        return RetrievalExecutionResult.build(
            request=request,
            receipt=receipt,
            evidence_items=(evidence,),
            computed_values=(),
            conflict_ids=(),
            status=(
                RetrievalExecutionStatus.COMPLETE
                if self.pagination_complete
                else RetrievalExecutionStatus.PARTIAL
            ),
        )


def test_execute_and_freeze_complete_bundle_preserves_b2_lineage() -> None:
    plan = _plan()
    results = execute_retrieval_plan(
        plan,
        policy=_policy(),
        adapters={RetrievalProvider.SEC: _SecAdapter()},
    )
    frozen = freeze_research_evidence_bundle(
        plan,
        results,
        bundle_id="bundle-nvda-1",
        base_b2_evidence_ids=("B2_SEC_IDENTITY_NVDA_20260827",),
        base_computed_value_ids=("B2_NVDA_RETURN_20S_20260827",),
    )

    assert frozen.bundle.status is ResearchEvidenceStatus.COMPLETE
    assert frozen.bundle.base_b2_evidence_ids == ("B2_SEC_IDENTITY_NVDA_20260827",)
    assert frozen.bundle.added_b3_evidence_ids == ("evidence-n1",)
    assert frozen.bundle.computed_value_ids == ("B2_NVDA_RETURN_20S_20260827",)
    assert frozen.bundle.provider_read_receipt_ids == ("receipt-n1",)
    assert frozen.excluded_evidence_ids == ()
    assert len(frozen.bundle.bundle_hash) == 64


def test_future_evidence_is_excluded_and_bundle_is_not_complete() -> None:
    plan = _plan()
    results = execute_retrieval_plan(
        plan,
        policy=_policy(),
        adapters={RetrievalProvider.SEC: _SecAdapter(future=True)},
    )
    frozen = freeze_research_evidence_bundle(plan, results, bundle_id="bundle-nvda-future")

    assert frozen.bundle.status is ResearchEvidenceStatus.STALE
    assert frozen.bundle.added_b3_evidence_ids == ()
    assert frozen.evidence_items == ()
    assert frozen.excluded_evidence_ids == ("evidence-n1",)


def test_incomplete_pagination_makes_bundle_partial() -> None:
    plan = _plan()
    results = execute_retrieval_plan(
        plan,
        policy=_policy(),
        adapters={RetrievalProvider.SEC: _SecAdapter(pagination_complete=False)},
    )
    frozen = freeze_research_evidence_bundle(plan, results, bundle_id="bundle-nvda-partial")
    assert frozen.bundle.status is ResearchEvidenceStatus.PARTIAL


def test_missing_application_adapter_fails_before_provider_execution() -> None:
    with pytest.raises(RetrievalExecutionError, match="no approved application-owned adapter"):
        execute_retrieval_plan(_plan(), policy=_policy(), adapters={})


def test_result_rejects_receipt_that_does_not_bind_request_parameters() -> None:
    plan = _plan()
    request = next(iter(execute_retrieval_plan.__globals__["compile_retrieval_requests"](plan, policy=_policy())))
    receipt = ProviderReadReceipt(
        provider_read_receipt_id="receipt-tampered",
        provider="SEC",
        endpoint_class=request.action.value,
        request_start=CUTOFF,
        response_received_at=CUTOFF,
        request_parameters_hash=canonical_sha256({"different": "parameters"}),
        pagination_complete=True,
        raw_payload_hash=canonical_sha256({"payload": "x"}),
        record_count=0,
    )
    with pytest.raises(ValueError, match="does not bind RetrievalRequest parameters"):
        RetrievalExecutionResult.build(
            request=request,
            receipt=receipt,
            status=RetrievalExecutionStatus.COMPLETE,
        )
