from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping, Self

from pydantic import model_validator

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
from .policy import ResearchPolicy, validate_research_plan


RETRIEVAL_DISPATCH_VERSION = "B3_RETRIEVAL_DISPATCH_v0_1"


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
        expected = canonical_sha256(self, exclude_fields=("request_hash",))
        if self.request_hash != expected:
            raise ValueError("request_hash does not bind RetrievalRequest")
        return self

    @classmethod
    def build(cls, **payload: Any) -> Self:
        data = dict(payload)
        data["request_hash"] = canonical_sha256(data)
        return cls(**data)


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
