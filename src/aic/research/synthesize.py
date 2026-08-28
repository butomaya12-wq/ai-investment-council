from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any, Literal, Mapping, Self

from pydantic import field_validator, model_validator

from aic.domain.canonical import canonical_sha256

from .evidence_bundle import ResearchEvidenceFreezeResult
from .handoff import B2RealEventHandoff
from .model_policy import API_INVARIANTS, MODEL_POLICY_VERSION, ModelCandidate
from .models import B3Model, ResearchEvidenceStatus, ResearchGapPlan
from .policy import ResearchPolicy
from .prompts import (
    SYNTHESIS_INSTRUCTIONS,
    SYNTHESIS_PROMPT_VERSION,
    synthesis_prompt_hash,
)


SYNTHESIS_SCHEMA_NAME = "b3_candidate_packet_synthesis_draft_v1"
SYNTHESIS_REQUEST_VERSION = "B3_SYNTHESIS_REQUEST_v0_1"
UNTRUSTED_EVIDENCE_MARKER = "UNTRUSTED_EVIDENCE_CONTENT"

CLAIM_CATEGORIES = (
    "business_model",
    "growth_quality",
    "financial_quality",
    "competitive_position",
    "valuation_context",
    "market_context",
    "capital_allocation",
    "catalyst",
    "risk",
    "portfolio_interaction",
)


class MaterialClaimDraft(B3Model):
    claim_id: str
    candidate_id: str
    category: str
    claim_text: str
    claim_kind: Literal["FACT", "INFERENCE"]
    materiality: Literal["MATERIAL", "SUPPORTING"]
    evidence_ids: tuple[str, ...] = ()
    computed_value_ids: tuple[str, ...] = ()
    conflict_ids: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    support_status: Literal["SUPPORTED", "CONFLICTED", "INSUFFICIENT"]
    uncertainty_note: str | None = None

    @field_validator("claim_id", "candidate_id", "category", "claim_text")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError("claim string fields must be non-empty and trimmed")
        return value

    @model_validator(mode="after")
    def _draft_contract(self) -> Self:
        if self.category not in CLAIM_CATEGORIES:
            raise ValueError("MaterialClaim category is outside B3 V1 allowlist")
        for values in (
            self.evidence_ids,
            self.computed_value_ids,
            self.conflict_ids,
            self.assumptions,
        ):
            if len(set(values)) != len(values):
                raise ValueError("MaterialClaim reference/assumption lists must be unique")
        if self.claim_kind == "INFERENCE" and self.uncertainty_note is None:
            raise ValueError("INFERENCE requires uncertainty_note")
        if self.support_status == "CONFLICTED" and not self.conflict_ids:
            raise ValueError("CONFLICTED claim requires conflict_ids")
        if self.support_status == "SUPPORTED" and not (
            self.evidence_ids or self.computed_value_ids
        ):
            raise ValueError("SUPPORTED claim requires evidence or computed-value refs")
        return self


class CandidatePacketDraft(B3Model):
    business_model_claim_ids: tuple[str, ...] = ()
    growth_quality_claim_ids: tuple[str, ...] = ()
    financial_quality_claim_ids: tuple[str, ...] = ()
    competitive_position_claim_ids: tuple[str, ...] = ()
    valuation_context_claim_ids: tuple[str, ...] = ()
    market_context_claim_ids: tuple[str, ...] = ()
    capital_allocation_claim_ids: tuple[str, ...] = ()
    catalyst_claim_ids: tuple[str, ...] = ()
    risk_claim_ids: tuple[str, ...] = ()
    portfolio_interaction_claim_ids: tuple[str, ...] = ()
    material_unknowns: tuple[str, ...] = ()
    material_conflicts: tuple[str, ...] = ()
    source_gaps: tuple[str, ...] = ()
    computed_value_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    research_questions_resolved: tuple[str, ...] = ()
    research_questions_unresolved: tuple[str, ...] = ()
    research_status: Literal["COMPLETE", "DEGRADED", "INCOMPLETE", "CONFLICTED"]

    @model_validator(mode="after")
    def _unique_lists(self) -> Self:
        for field_name in self.__class__.model_fields:
            value = getattr(self, field_name)
            if isinstance(value, tuple) and len(set(value)) != len(value):
                raise ValueError(f"{field_name} values must be unique")
        return self


class CandidateSynthesisDraft(B3Model):
    candidate_id: str
    claims: tuple[MaterialClaimDraft, ...]
    packet: CandidatePacketDraft

    @model_validator(mode="after")
    def _candidate_claim_ids(self) -> Self:
        if not self.candidate_id:
            raise ValueError("candidate_id must be non-empty")
        if len({claim.claim_id for claim in self.claims}) != len(self.claims):
            raise ValueError("claim_id values must be unique")
        if any(claim.candidate_id != self.candidate_id for claim in self.claims):
            raise ValueError("all synthesis claims must bind one candidate")
        return self


class SynthesisEvidenceItem(B3Model):
    evidence_id: str
    provider: str
    source_type: str
    field_or_claim: str
    normalized_value: Any
    published_at: datetime | None
    observed_at: datetime | None
    as_of: datetime
    authoritative_for: tuple[str, ...]
    content_trust: Literal["UNTRUSTED_EVIDENCE_CONTENT"] = UNTRUSTED_EVIDENCE_MARKER


class SynthesisComputedValue(B3Model):
    computed_value_id: str
    metric_id: str
    value: str
    unit: str


class SynthesisQuestion(B3Model):
    question_id: str
    category: str
    question_text: str
    why_material: str


class SynthesisInputEnvelope(B3Model):
    candidate_id: str
    symbol: str
    issuer_id: str
    b2_snapshot_id: str
    research_snapshot_id: str
    mandate_version: str | None
    deep_comparison_id: str
    research_policy_version: str
    model_policy_version: str
    research_cutoff: datetime
    evidence_bundle_hash: str
    evidence_status: ResearchEvidenceStatus
    evidence_items: tuple[SynthesisEvidenceItem, ...]
    computed_values: tuple[SynthesisComputedValue, ...]
    conflict_ids: tuple[str, ...]
    research_questions: tuple[SynthesisQuestion, ...]
    application_source_gaps: tuple[str, ...]

    @field_validator("research_cutoff")
    @classmethod
    def _aware_cutoff(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("research_cutoff must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _lineage_contract(self) -> Self:
        if self.candidate_id != self.symbol:
            raise ValueError("event candidate_id and symbol must match")
        if self.model_policy_version != MODEL_POLICY_VERSION:
            raise ValueError("unexpected model policy version")
        for values in (
            tuple(item.evidence_id for item in self.evidence_items),
            tuple(item.computed_value_id for item in self.computed_values),
            self.conflict_ids,
            tuple(q.question_id for q in self.research_questions),
            self.application_source_gaps,
        ):
            if len(set(values)) != len(values):
                raise ValueError("synthesis input identity lists must be unique")
        return self


class SynthesisRequestEnvelope(B3Model):
    request_version: str
    prompt_version: str
    prompt_hash: str
    input_hash: str
    model_candidate_key: str
    request_payload: Mapping[str, Any]
    request_hash: str

    @model_validator(mode="after")
    def _hashes_bind_payload(self) -> Self:
        expected = canonical_sha256(
            {
                "request_version": self.request_version,
                "prompt_version": self.prompt_version,
                "prompt_hash": self.prompt_hash,
                "input_hash": self.input_hash,
                "model_candidate_key": self.model_candidate_key,
                "request_payload": self.request_payload,
            }
        )
        if self.request_hash != expected:
            raise ValueError("request_hash does not bind synthesis request")
        return self


def _issuer_id_from_sec_uri(source_uri: str) -> str:
    match = re.fullmatch(
        r"https://www\.sec\.gov/Archives/edgar/data/([0-9]+)/.+",
        source_uri,
    )
    if match is None:
        raise ValueError("issuer identity requires frozen official SEC archive URI")
    return f"SEC_CIK_{int(match.group(1)):010d}"


def build_synthesis_input(
    *,
    handoff: B2RealEventHandoff,
    plan: ResearchGapPlan,
    frozen_evidence: ResearchEvidenceFreezeResult,
    mandate_version: str | None,
    application_source_gaps: tuple[str, ...] = (),
) -> SynthesisInputEnvelope:
    candidate = handoff.candidate(plan.candidate_id)
    bundle = frozen_evidence.bundle
    if bundle.candidate_id != plan.candidate_id:
        raise ValueError("ResearchEvidenceBundle candidate does not match plan")
    if bundle.b2_snapshot_id != plan.b2_snapshot_id:
        raise ValueError("ResearchEvidenceBundle B2 snapshot lineage mismatch")
    if bundle.deep_comparison_id != plan.deep_comparison_id:
        raise ValueError("ResearchEvidenceBundle deep-comparison lineage mismatch")
    if bundle.research_cutoff != plan.research_cutoff:
        raise ValueError("ResearchEvidenceBundle cutoff lineage mismatch")

    evidence_items = tuple(
        SynthesisEvidenceItem(
            evidence_id=item.evidence_id,
            provider=item.provider,
            source_type=item.source_type,
            field_or_claim=item.field_or_claim,
            normalized_value=item.normalized_value,
            published_at=item.published_at,
            observed_at=item.observed_at,
            as_of=item.as_of,
            authoritative_for=item.authoritative_for,
        )
        for item in frozen_evidence.evidence_items
    )
    handoff_computed = tuple(
        SynthesisComputedValue(
            computed_value_id=metric.computed_value_id,
            metric_id=metric.metric_id,
            value=metric.value,
            unit=metric.unit,
        )
        for metric in candidate.metrics
        if metric.computed_value_id in set(bundle.computed_value_ids)
    )
    retrieved_computed = tuple(
        SynthesisComputedValue(
            computed_value_id=value.computed_value_id,
            metric_id=value.metric_id,
            value=str(value.value),
            unit=value.unit,
        )
        for value in frozen_evidence.computed_values
        if value.computed_value_id not in {item.computed_value_id for item in handoff_computed}
    )
    questions = tuple(
        SynthesisQuestion(
            question_id=question.question_id,
            category=question.category,
            question_text=question.question_text,
            why_material=question.why_material,
        )
        for question in plan.material_questions
    )
    return SynthesisInputEnvelope(
        candidate_id=plan.candidate_id,
        symbol=candidate.symbol,
        issuer_id=_issuer_id_from_sec_uri(candidate.sec_source_uri),
        b2_snapshot_id=bundle.b2_snapshot_id,
        research_snapshot_id=bundle.bundle_id,
        mandate_version=mandate_version,
        deep_comparison_id=bundle.deep_comparison_id,
        research_policy_version=bundle.research_policy_version,
        model_policy_version=bundle.model_policy_version,
        research_cutoff=bundle.research_cutoff,
        evidence_bundle_hash=bundle.bundle_hash,
        evidence_status=bundle.status,
        evidence_items=evidence_items,
        computed_values=handoff_computed + retrieved_computed,
        conflict_ids=bundle.conflict_ids,
        research_questions=questions,
        application_source_gaps=application_source_gaps,
    )


def _draft_schema(synthesis_input: SynthesisInputEnvelope) -> dict[str, Any]:
    schema = CandidateSynthesisDraft.model_json_schema(mode="validation")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError("CandidateSynthesisDraft root schema must be strict object")
    # Candidate identity is immutable application-owned lineage. Post-validation repeats this check.
    candidate_property = schema.get("properties", {}).get("candidate_id")
    if isinstance(candidate_property, dict):
        candidate_property.clear()
        candidate_property.update({"type": "string", "const": synthesis_input.candidate_id})
    return schema


def build_synthesis_request(
    *,
    model_candidate: ModelCandidate,
    synthesis_input: SynthesisInputEnvelope,
) -> SynthesisRequestEnvelope:
    input_hash = canonical_sha256(synthesis_input)
    request_payload: dict[str, Any] = {
        "model": model_candidate.model,
        "reasoning": {"effort": model_candidate.reasoning_effort},
        "instructions": SYNTHESIS_INSTRUCTIONS,
        "input": json.dumps(
            synthesis_input.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "store": API_INVARIANTS.store,
        "tools": [],
        "parallel_tool_calls": False,
        "truncation": "disabled",
        "text": {
            "format": {
                "type": "json_schema",
                "name": SYNTHESIS_SCHEMA_NAME,
                "strict": True,
                "schema": _draft_schema(synthesis_input),
            }
        },
    }
    envelope = {
        "request_version": SYNTHESIS_REQUEST_VERSION,
        "prompt_version": SYNTHESIS_PROMPT_VERSION,
        "prompt_hash": synthesis_prompt_hash(),
        "input_hash": input_hash,
        "model_candidate_key": model_candidate.candidate_key,
        "request_payload": request_payload,
    }
    return SynthesisRequestEnvelope(
        **envelope,
        request_hash=canonical_sha256(envelope),
    )


def parse_synthesis_output(
    output_text: str,
    *,
    synthesis_input: SynthesisInputEnvelope,
) -> CandidateSynthesisDraft:
    if not isinstance(output_text, str) or not output_text.strip():
        raise ValueError("synthesis output_text must be a non-empty string")
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise ValueError("synthesis output is not valid JSON") from exc
    draft = CandidateSynthesisDraft.model_validate(payload)
    if draft.candidate_id != synthesis_input.candidate_id:
        raise ValueError("synthesis candidate identity drift")
    return draft
