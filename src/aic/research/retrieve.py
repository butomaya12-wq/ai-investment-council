from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol, Self

from pydantic import model_validator

from aic.b2.models import ComputedValue, EvidenceItem, ProviderReadReceipt
from aic.domain.canonical import canonical_sha256

from .models import (
    AlpacaNewsWindowParameters,
    B2ComputedValueDetailParameters,
    B2EvidenceDetailParameters,
    B3Model,
    CompanyIRDocumentParameters,
    CorporateActionDetailParameters,
    ResearchGapPlan,
    ResearchNeed,
    ResearchNeedType,
    SecFilingSectionParameters,
)
from .policy import ResearchPolicy, ResearchPolicyError, validate_research_plan
from .sec_schema import validate_runtime_sec_sections


RETRIEVAL_DISPATCH_VERSION = "B3_RETRIEVAL_DISPATCH_v0_1"
RETRIEVAL_EXECUTION_VERSION = "B3_RETRIEVAL_EXECUTION_v0_1"


class RetrievalProvider(StrEnum):
    B2_STORE = "B2_STORE"
    SEC = "SEC"
    ALPACA = "ALPACA"
    IR_REGISTRY = "IR_REGISTRY"


class RetrievalAction(StrEnum):
    GET_EVIDENCE_BY_IDS = "GET_EVIDENCE_BY_IDS"
    GET_COMPUTED_VALUES_BY_IDS = "GET_COMPUTED_VALUES_BY_IDS"
    GET_FILING_SECTIONS = "GET_FILING_SECTIONS"
    GET_NEWS_WINDOW = "GET_NEWS_WINDOW"
    GET_CORPORATE_ACTIONS_BY_IDS = "GET_CORPORATE_ACTIONS_BY_IDS"
    GET_IR_DOCUMENTS_BY_IDS = "GET_IR_DOCUMENTS_BY_IDS"


class RetrievalExecutionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class RetrievalExecutionError(RuntimeError):
    pass


class RetrievalRequest(B3Model):
    dispatch_version: str
    research_plan_id: str
    candidate_id: str
    need_id: str
    question_id: str
    provider: RetrievalProvider
    action: RetrievalAction
    parameters: Mapping[str, Any]
    max_items: int
    request_hash: str

    @model_validator(mode="after")
    def _bind_hash_and_forbid_escape_keys(self) -> Self:
        forbidden_keys = {
            "url",
            "uri",
            "sql",
            "query",
            "api_key",
            "apikey",
            "credential",
            "secret",
            "order",
            "broker",
        }
        if set(key.lower() for key in self.parameters) & forbidden_keys:
            raise ValueError("retrieval parameters contain forbidden escape/credential/write key")
        if self.dispatch_version != RETRIEVAL_DISPATCH_VERSION:
            raise ValueError("unexpected retrieval dispatch version")
        expected = canonical_sha256(self, exclude_fields=("request_hash",))
        if self.request_hash != expected:
            raise ValueError("request_hash does not bind RetrievalRequest")
        return self

    @classmethod
    def build(cls, **payload: Any) -> Self:
        data = dict(payload)
        data["request_hash"] = canonical_sha256(data)
        return cls(**data)


class RetrievalExecutionResult(B3Model):
    execution_version: str
    request: RetrievalRequest
    receipt: ProviderReadReceipt
    evidence_items: tuple[EvidenceItem, ...] = ()
    computed_values: tuple[ComputedValue, ...] = ()
    conflict_ids: tuple[str, ...] = ()
    status: RetrievalExecutionStatus
    result_hash: str

    @model_validator(mode="after")
    def _bind_execution(self) -> Self:
        if self.execution_version != RETRIEVAL_EXECUTION_VERSION:
            raise ValueError("unexpected retrieval execution version")
        if self.receipt.provider != self.request.provider.value:
            raise ValueError("provider receipt does not match RetrievalRequest provider")
        if self.receipt.endpoint_class != self.request.action.value:
            raise ValueError("provider receipt endpoint_class does not match RetrievalRequest action")
        if self.receipt.request_parameters_hash != canonical_sha256(self.request.parameters):
            raise ValueError("provider receipt does not bind RetrievalRequest parameters")

        evidence_ids = tuple(item.evidence_id for item in self.evidence_items)
        computed_ids = tuple(item.computed_value_id for item in self.computed_values)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("retrieval evidence IDs must be unique")
        if len(set(computed_ids)) != len(computed_ids):
            raise ValueError("retrieval computed-value IDs must be unique")
        if len(set(self.conflict_ids)) != len(self.conflict_ids):
            raise ValueError("retrieval conflict IDs must be unique")
        if len(self.evidence_items) + len(self.computed_values) > self.request.max_items:
            raise ValueError("retrieval output exceeds ResearchNeed max_items")
        if any(
            item.provider_read_receipt_id != self.receipt.provider_read_receipt_id
            for item in self.evidence_items
        ):
            raise ValueError("retrieval evidence must bind the provider receipt")

        if self.status is RetrievalExecutionStatus.COMPLETE:
            if self.receipt.error is not None:
                raise ValueError("COMPLETE retrieval cannot contain provider error")
            if not self.receipt.pagination_complete:
                raise ValueError("COMPLETE retrieval requires complete pagination")
        if self.status is RetrievalExecutionStatus.FAILED and (
            self.evidence_items or self.computed_values
        ):
            raise ValueError("FAILED retrieval cannot expose provider data as valid output")

        expected = canonical_sha256(self, exclude_fields=("result_hash",))
        if self.result_hash != expected:
            raise ValueError("result_hash does not bind RetrievalExecutionResult")
        return self

    @classmethod
    def build(cls, **payload: Any) -> Self:
        data = dict(payload)
        data.setdefault("execution_version", RETRIEVAL_EXECUTION_VERSION)
        data["result_hash"] = canonical_sha256(data)
        return cls(**data)


class RetrievalAdapter(Protocol):
    provider: RetrievalProvider

    def execute(
        self,
        *,
        request: RetrievalRequest,
        research_cutoff: datetime,
    ) -> RetrievalExecutionResult:
        ...


def _dispatch_need(plan: ResearchGapPlan, need: ResearchNeed) -> RetrievalRequest:
    provider: RetrievalProvider
    action: RetrievalAction
    parameters: dict[str, Any]

    if need.need_type is ResearchNeedType.NEED_B2_EVIDENCE_DETAIL:
        assert isinstance(need.parameters, B2EvidenceDetailParameters)
        provider = RetrievalProvider.B2_STORE
        action = RetrievalAction.GET_EVIDENCE_BY_IDS
        parameters = {"evidence_ids": need.parameters.evidence_ids}
    elif need.need_type is ResearchNeedType.NEED_B2_COMPUTED_VALUE_DETAIL:
        assert isinstance(need.parameters, B2ComputedValueDetailParameters)
        provider = RetrievalProvider.B2_STORE
        action = RetrievalAction.GET_COMPUTED_VALUES_BY_IDS
        parameters = {"computed_value_ids": need.parameters.computed_value_ids}
    elif need.need_type is ResearchNeedType.NEED_SEC_FILING_SECTION:
        assert isinstance(need.parameters, SecFilingSectionParameters)
        try:
            validate_runtime_sec_sections(need.parameters.sections)
        except ValueError as exc:
            raise ResearchPolicyError(str(exc)) from exc
        provider = RetrievalProvider.SEC
        action = RetrievalAction.GET_FILING_SECTIONS
        parameters = {
            "filing_accession": need.parameters.filing_accession,
            "sections": need.parameters.sections,
        }
    elif need.need_type is ResearchNeedType.NEED_ALPACA_NEWS_WINDOW:
        assert isinstance(need.parameters, AlpacaNewsWindowParameters)
        provider = RetrievalProvider.ALPACA
        action = RetrievalAction.GET_NEWS_WINDOW
        parameters = {
            "candidate_id": plan.candidate_id,
            "window_start": need.parameters.window_start,
            "window_end": need.parameters.window_end,
        }
    elif need.need_type is ResearchNeedType.NEED_CORPORATE_ACTION_DETAIL:
        assert isinstance(need.parameters, CorporateActionDetailParameters)
        provider = RetrievalProvider.ALPACA
        action = RetrievalAction.GET_CORPORATE_ACTIONS_BY_IDS
        parameters = {"action_ids": need.parameters.action_ids}
    elif need.need_type is ResearchNeedType.NEED_COMPANY_IR_DOCUMENT:
        assert isinstance(need.parameters, CompanyIRDocumentParameters)
        provider = RetrievalProvider.IR_REGISTRY
        action = RetrievalAction.GET_IR_DOCUMENTS_BY_IDS
        parameters = {"registry_document_ids": need.parameters.registry_document_ids}
    else:
        raise ValueError("unsupported ResearchNeed type")

    return RetrievalRequest.build(
        dispatch_version=RETRIEVAL_DISPATCH_VERSION,
        research_plan_id=plan.research_plan_id,
        candidate_id=plan.candidate_id,
        need_id=need.need_id,
        question_id=need.question_id,
        provider=provider,
        action=action,
        parameters=parameters,
        max_items=need.max_items,
    )


def compile_retrieval_requests(
    plan: ResearchGapPlan,
    *,
    policy: ResearchPolicy,
) -> tuple[RetrievalRequest, ...]:
    validate_research_plan(plan, policy)
    return tuple(_dispatch_need(plan, need) for need in plan.requested_needs)


def execute_retrieval_plan(
    plan: ResearchGapPlan,
    *,
    policy: ResearchPolicy,
    adapters: Mapping[RetrievalProvider, RetrievalAdapter],
) -> tuple[RetrievalExecutionResult, ...]:
    requests = compile_retrieval_requests(plan, policy=policy)
    results: list[RetrievalExecutionResult] = []
    for request in requests:
        adapter = adapters.get(request.provider)
        if adapter is None:
            raise RetrievalExecutionError(
                f"no approved application-owned adapter for provider {request.provider.value}"
            )
        if adapter.provider is not request.provider:
            raise RetrievalExecutionError("retrieval adapter provider identity mismatch")
        result = adapter.execute(request=request, research_cutoff=plan.research_cutoff)
        if result.request.request_hash != request.request_hash:
            raise RetrievalExecutionError("adapter returned result for a different RetrievalRequest")
        results.append(result)
    return tuple(results)
