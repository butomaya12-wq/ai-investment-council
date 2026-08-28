from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts_research import (
    B3_RESEARCH_SNAPSHOT_V1,
    CANDIDATE_PACKET_V1,
    MATERIAL_CLAIM_V1,
    MODEL_RUN_RECEIPT_V1,
)


# These four aliases intentionally reuse the frozen B1 canonical contract bindings.
# B3 must not create a competing local definition for durable canonical artifacts.
B3ResearchSnapshot = B3_RESEARCH_SNAPSHOT_V1
MaterialClaim = MATERIAL_CLAIM_V1
CandidatePacket = CANDIDATE_PACKET_V1
ModelRunReceipt = MODEL_RUN_RECEIPT_V1


class B3Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResearchNeedType(StrEnum):
    NEED_B2_EVIDENCE_DETAIL = "NEED_B2_EVIDENCE_DETAIL"
    NEED_B2_COMPUTED_VALUE_DETAIL = "NEED_B2_COMPUTED_VALUE_DETAIL"
    NEED_SEC_FILING_SECTION = "NEED_SEC_FILING_SECTION"
    NEED_ALPACA_NEWS_WINDOW = "NEED_ALPACA_NEWS_WINDOW"
    NEED_CORPORATE_ACTION_DETAIL = "NEED_CORPORATE_ACTION_DETAIL"
    NEED_COMPANY_IR_DOCUMENT = "NEED_COMPANY_IR_DOCUMENT"


class CurrentEvidenceStatus(StrEnum):
    ENOUGH = "ENOUGH"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    CONFLICTED = "CONFLICTED"


class ResearchEvidenceStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    CONFLICTED = "CONFLICTED"
    STALE = "STALE"
    FAILED = "FAILED"


_URL_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_ACTION_RE = re.compile(r"\b(?:buy|sell|invest|watch|abstain)\b", re.IGNORECASE)
_BROKER_RE = re.compile(
    r"\b(?:broker(?:age)?|place\s+(?:an?\s+)?order|submit\s+(?:an?\s+)?order|"
    r"execute\s+(?:a\s+)?trade|position\s+size|api\s*key|credential|secret|tool\s+call)\b",
    re.IGNORECASE,
)
_SQL_RE = re.compile(
    r"\b(?:select\b.+\bfrom|insert\b.+\binto|update\b.+\bset|delete\b.+\bfrom|"
    r"drop\s+table|alter\s+table)\b",
    re.IGNORECASE | re.DOTALL,
)
_ARITHMETIC_RESULT_RE = re.compile(r"(?:\d+(?:\.\d+)?\s*%|=\s*[-+]?\d)")


def _safe_model_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty trimmed string")
    if _URL_RE.search(value):
        raise ValueError(f"{field_name} must not contain a model-selected URL")
    if _ACTION_RE.search(value):
        raise ValueError(f"{field_name} must not contain a B3 investment decision/action")
    if _BROKER_RE.search(value):
        raise ValueError(f"{field_name} must not request broker/order/write/tool authority")
    if _SQL_RE.search(value):
        raise ValueError(f"{field_name} must not contain SQL/query language")
    if _ARITHMETIC_RESULT_RE.search(value):
        raise ValueError(f"{field_name} must not contain an arithmetic result")
    return value


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class ResearchQuestion(B3Model):
    question_id: str
    category: str
    question_text: str
    why_material: str
    current_evidence_status: CurrentEvidenceStatus

    @field_validator("question_id")
    @classmethod
    def _question_id(cls, value: str) -> str:
        return _safe_model_text(value, field_name="question_id")

    @field_validator("category", "question_text", "why_material")
    @classmethod
    def _safe_text(cls, value: str, info) -> str:
        return _safe_model_text(value, field_name=info.field_name)


class B2EvidenceDetailParameters(B3Model):
    evidence_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _non_empty(self) -> Self:
        if not self.evidence_ids or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids must be non-empty and unique")
        return self


class B2ComputedValueDetailParameters(B3Model):
    computed_value_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _non_empty(self) -> Self:
        if not self.computed_value_ids or len(set(self.computed_value_ids)) != len(self.computed_value_ids):
            raise ValueError("computed_value_ids must be non-empty and unique")
        return self


class SecFilingSectionParameters(B3Model):
    filing_accession: str
    sections: tuple[str, ...]

    @field_validator("filing_accession")
    @classmethod
    def _accession(cls, value: str) -> str:
        return _safe_model_text(value, field_name="filing_accession")

    @model_validator(mode="after")
    def _sections(self) -> Self:
        if not self.sections or len(set(self.sections)) != len(self.sections):
            raise ValueError("sections must be non-empty and unique")
        for section in self.sections:
            _safe_model_text(section, field_name="section")
        return self


class AlpacaNewsWindowParameters(B3Model):
    window_start: datetime
    window_end: datetime

    @field_validator("window_start", "window_end")
    @classmethod
    def _utc(cls, value: datetime, info) -> datetime:
        return _aware_utc(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.window_start > self.window_end:
            raise ValueError("news window_start must not be after window_end")
        return self


class CorporateActionDetailParameters(B3Model):
    action_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _action_ids(self) -> Self:
        if not self.action_ids or len(set(self.action_ids)) != len(self.action_ids):
            raise ValueError("action_ids must be non-empty and unique")
        return self


class CompanyIRDocumentParameters(B3Model):
    registry_document_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _registry_ids(self) -> Self:
        if not self.registry_document_ids or len(set(self.registry_document_ids)) != len(self.registry_document_ids):
            raise ValueError("registry_document_ids must be non-empty and unique")
        return self


ResearchNeedParameters = (
    B2EvidenceDetailParameters
    | B2ComputedValueDetailParameters
    | SecFilingSectionParameters
    | AlpacaNewsWindowParameters
    | CorporateActionDetailParameters
    | CompanyIRDocumentParameters
)


_PARAMETER_TYPES: dict[ResearchNeedType, type[B3Model]] = {
    ResearchNeedType.NEED_B2_EVIDENCE_DETAIL: B2EvidenceDetailParameters,
    ResearchNeedType.NEED_B2_COMPUTED_VALUE_DETAIL: B2ComputedValueDetailParameters,
    ResearchNeedType.NEED_SEC_FILING_SECTION: SecFilingSectionParameters,
    ResearchNeedType.NEED_ALPACA_NEWS_WINDOW: AlpacaNewsWindowParameters,
    ResearchNeedType.NEED_CORPORATE_ACTION_DETAIL: CorporateActionDetailParameters,
    ResearchNeedType.NEED_COMPANY_IR_DOCUMENT: CompanyIRDocumentParameters,
}


class ResearchNeed(B3Model):
    need_id: str
    question_id: str
    need_type: ResearchNeedType
    parameters: ResearchNeedParameters
    max_items: Annotated[int, Field(ge=1)]
    expected_evidence_role: str

    @field_validator("need_id", "question_id", "expected_evidence_role")
    @classmethod
    def _safe_text(cls, value: str, info) -> str:
        return _safe_model_text(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _parameters_match_need_type(self) -> Self:
        expected = _PARAMETER_TYPES[self.need_type]
        if not isinstance(self.parameters, expected):
            raise ValueError(f"parameters do not match {self.need_type.value}")
        return self


class ResearchGapPlan(B3Model):
    research_plan_id: str
    candidate_id: str
    b2_snapshot_id: str
    deep_comparison_id: str
    research_policy_version: str
    model_policy_version: str
    research_cutoff: datetime
    material_questions: tuple[ResearchQuestion, ...]
    requested_needs: tuple[ResearchNeed, ...]

    @field_validator(
        "research_plan_id",
        "candidate_id",
        "b2_snapshot_id",
        "deep_comparison_id",
        "research_policy_version",
        "model_policy_version",
    )
    @classmethod
    def _ids(cls, value: str, info) -> str:
        return _safe_model_text(value, field_name=info.field_name)

    @field_validator("research_cutoff")
    @classmethod
    def _cutoff(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="research_cutoff")

    @model_validator(mode="after")
    def _lineage_and_refs(self) -> Self:
        if not self.material_questions:
            raise ValueError("material_questions must not be empty")
        question_ids = tuple(question.question_id for question in self.material_questions)
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("question_id values must be unique")
        need_ids = tuple(need.need_id for need in self.requested_needs)
        if len(set(need_ids)) != len(need_ids):
            raise ValueError("need_id values must be unique")
        unknown_question_refs = tuple(
            need.question_id for need in self.requested_needs if need.question_id not in set(question_ids)
        )
        if unknown_question_refs:
            raise ValueError("every ResearchNeed must reference an existing material question")
        return self


def research_gap_plan_hash(plan: ResearchGapPlan) -> str:
    return canonical_sha256(plan)


class ResearchEvidenceBundle(B3Model):
    bundle_id: str
    candidate_id: str
    b2_snapshot_id: str
    deep_comparison_id: str
    research_cutoff: datetime
    research_policy_version: str
    model_policy_version: str
    provider_read_receipt_ids: tuple[str, ...]
    base_b2_evidence_ids: tuple[str, ...]
    added_b3_evidence_ids: tuple[str, ...]
    computed_value_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]
    raw_content_hashes: tuple[str, ...]
    status: ResearchEvidenceStatus
    bundle_hash: str

    @field_validator("research_cutoff")
    @classmethod
    def _cutoff(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="research_cutoff")

    @field_validator("raw_content_hashes")
    @classmethod
    def _hashes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in values):
            raise ValueError("raw_content_hashes must contain lowercase SHA-256 hex values")
        return values

    @model_validator(mode="after")
    def _bundle_hash(self) -> Self:
        expected = canonical_sha256(self, exclude_fields=("bundle_hash",))
        if self.bundle_hash != expected:
            raise ValueError("bundle_hash does not bind ResearchEvidenceBundle")
        return self

    @classmethod
    def build(cls, **payload: Any) -> Self:
        data = dict(payload)
        data["bundle_hash"] = canonical_sha256(data)
        return cls(**data)


class ResearchBatchManifest(B3Model):
    batch_id: str
    b2_snapshot_id: str
    deep_comparison_id: str
    research_policy_version: str
    model_policy_version: str
    candidate_ids: tuple[str, str, str]
    frozen_candidate_packet_ids: tuple[str, ...] = ()
    batch_hash: str

    @model_validator(mode="after")
    def _exact_three_and_hash(self) -> Self:
        if len(set(self.candidate_ids)) != 3:
            raise ValueError("ResearchBatchManifest requires exactly three unique candidates")
        if len(set(self.frozen_candidate_packet_ids)) != len(self.frozen_candidate_packet_ids):
            raise ValueError("frozen_candidate_packet_ids must be unique")
        if len(self.frozen_candidate_packet_ids) > 3:
            raise ValueError("at most three candidate packets may be frozen")
        expected = canonical_sha256(self, exclude_fields=("batch_hash",))
        if self.batch_hash != expected:
            raise ValueError("batch_hash does not bind ResearchBatchManifest")
        return self

    @classmethod
    def build(cls, **payload: Any) -> Self:
        data = dict(payload)
        data["batch_hash"] = canonical_sha256(data)
        return cls(**data)
