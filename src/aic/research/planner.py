from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping, Self

from pydantic import field_validator, model_validator

from aic.domain.canonical import canonical_sha256

from .model_policy import API_INVARIANTS, MODEL_POLICY_VERSION, ModelCandidate
from .models import (
    AlpacaNewsWindowParameters,
    B2ComputedValueDetailParameters,
    B2EvidenceDetailParameters,
    B3Model,
    CompanyIRDocumentParameters,
    CorporateActionDetailParameters,
    ResearchGapPlan,
    SecFilingSectionParameters,
)
from .policy import ResearchPolicy, ResearchPolicyError, validate_research_plan
from .prompts import PLANNER_INSTRUCTIONS, PLANNER_PROMPT_VERSION, planner_prompt_hash
from .schema_constraints import constrain_planner_schema
from .sec_schema import constrain_sec_sections_in_schema


PLANNER_SCHEMA_NAME = "b3_research_gap_plan_v1"
PLANNER_REQUEST_VERSION = "B3_PLANNER_REQUEST_v0_1"


class PlannerContextItem(B3Model):
    item_id: str
    category: str
    evidence_status: str
    description: str
    evidence_refs: tuple[str, ...] = ()
    computed_value_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _refs_unique(self) -> Self:
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence_refs must be unique")
        if len(set(self.computed_value_refs)) != len(self.computed_value_refs):
            raise ValueError("computed_value_refs must be unique")
        return self


class PlannerInputEnvelope(B3Model):
    candidate_id: str
    b2_snapshot_id: str
    deep_comparison_id: str
    research_policy_version: str
    model_policy_version: str
    research_cutoff: datetime
    context_items: tuple[PlannerContextItem, ...]
    allowed_source_handles: tuple[str, ...]

    @field_validator("research_cutoff")
    @classmethod
    def _aware_cutoff(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("research_cutoff must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _unique_context_and_handles(self) -> Self:
        item_ids = tuple(item.item_id for item in self.context_items)
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("PlannerContextItem item_id values must be unique")
        if len(set(self.allowed_source_handles)) != len(self.allowed_source_handles):
            raise ValueError("allowed_source_handles must be unique")
        if self.model_policy_version != MODEL_POLICY_VERSION:
            raise ValueError("unexpected planner model_policy_version")
        return self


class PlannerRequestEnvelope(B3Model):
    request_version: str
    prompt_version: str
    prompt_hash: str
    input_hash: str
    model_candidate_key: str
    request_payload: Mapping[str, Any]
    request_hash: str

    @model_validator(mode="after")
    def _hashes_bind_payload(self) -> Self:
        expected_request_hash = canonical_sha256(
            {
                "request_version": self.request_version,
                "prompt_version": self.prompt_version,
                "prompt_hash": self.prompt_hash,
                "input_hash": self.input_hash,
                "model_candidate_key": self.model_candidate_key,
                "request_payload": self.request_payload,
            }
        )
        if self.request_hash != expected_request_hash:
            raise ValueError("request_hash does not bind PlannerRequestEnvelope")
        return self


def _ordered_context_refs(
    planner_input: PlannerInputEnvelope,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    evidence_refs: list[str] = []
    computed_refs: list[str] = []
    for item in planner_input.context_items:
        evidence_refs.extend(item.evidence_refs)
        computed_refs.extend(item.computed_value_refs)
    return (
        tuple(dict.fromkeys(evidence_refs)),
        tuple(dict.fromkeys(computed_refs)),
    )


def _planner_output_schema(planner_input: PlannerInputEnvelope) -> dict[str, Any]:
    schema = ResearchGapPlan.model_json_schema(mode="validation")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError("ResearchGapPlan root schema must be strict object")
    schema = constrain_sec_sections_in_schema(schema)
    evidence_refs, computed_refs = _ordered_context_refs(planner_input)
    return constrain_planner_schema(
        schema,
        evidence_refs=evidence_refs,
        computed_value_refs=computed_refs,
        allowed_source_handles=planner_input.allowed_source_handles,
    )


def build_planner_request(
    *,
    model_candidate: ModelCandidate,
    planner_input: PlannerInputEnvelope,
) -> PlannerRequestEnvelope:
    input_hash = canonical_sha256(planner_input)
    user_payload = json.dumps(
        planner_input.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    request_payload: dict[str, Any] = {
        "model": model_candidate.model,
        "reasoning": {"effort": model_candidate.reasoning_effort},
        "instructions": PLANNER_INSTRUCTIONS,
        "input": user_payload,
        "store": API_INVARIANTS.store,
        "tools": [],
        "parallel_tool_calls": False,
        "truncation": "disabled",
        "text": {
            "format": {
                "type": "json_schema",
                "name": PLANNER_SCHEMA_NAME,
                "strict": True,
                "schema": _planner_output_schema(planner_input),
            }
        },
    }
    envelope_payload = {
        "request_version": PLANNER_REQUEST_VERSION,
        "prompt_version": PLANNER_PROMPT_VERSION,
        "prompt_hash": planner_prompt_hash(),
        "input_hash": input_hash,
        "model_candidate_key": model_candidate.candidate_key,
        "request_payload": request_payload,
    }
    return PlannerRequestEnvelope(
        **envelope_payload,
        request_hash=canonical_sha256(envelope_payload),
    )


def _validate_plan_source_refs(
    plan: ResearchGapPlan,
    planner_input: PlannerInputEnvelope,
) -> None:
    evidence_refs, computed_refs = _ordered_context_refs(planner_input)
    allowed_evidence = set(evidence_refs)
    allowed_computed = set(computed_refs)
    allowed_handles = set(planner_input.allowed_source_handles)

    for need in plan.requested_needs:
        parameters = need.parameters
        if isinstance(parameters, B2EvidenceDetailParameters):
            if not set(parameters.evidence_ids).issubset(allowed_evidence):
                raise ResearchPolicyError("B2 evidence id requested outside planner input refs")
        elif isinstance(parameters, B2ComputedValueDetailParameters):
            if not set(parameters.computed_value_ids).issubset(allowed_computed):
                raise ResearchPolicyError("B2 computed-value id requested outside planner input refs")
        elif isinstance(parameters, SecFilingSectionParameters):
            if parameters.filing_accession not in allowed_handles:
                raise ResearchPolicyError("SEC filing accession requested outside allowed source handles")
        elif isinstance(parameters, CorporateActionDetailParameters):
            if not set(parameters.action_ids).issubset(allowed_handles):
                raise ResearchPolicyError("corporate-action id requested outside allowed source handles")
        elif isinstance(parameters, CompanyIRDocumentParameters):
            if not set(parameters.registry_document_ids).issubset(allowed_handles):
                raise ResearchPolicyError("IR document id requested outside allowed source handles")
        elif isinstance(parameters, AlpacaNewsWindowParameters):
            has_news_authority = any(
                "NEWS_WINDOW" in handle.upper() for handle in planner_input.allowed_source_handles
            )
            if not has_news_authority:
                raise ResearchPolicyError("Alpaca news requested without an allowed news-window handle")


def parse_planner_output(
    output_text: str,
    *,
    planner_input: PlannerInputEnvelope,
    research_policy: ResearchPolicy,
) -> ResearchGapPlan:
    if not isinstance(output_text, str) or not output_text.strip():
        raise ValueError("planner output_text must be a non-empty string")
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise ValueError("planner output is not valid JSON") from exc
    plan = ResearchGapPlan.model_validate(payload)

    expected_lineage = (
        planner_input.candidate_id,
        planner_input.b2_snapshot_id,
        planner_input.deep_comparison_id,
        planner_input.research_policy_version,
        planner_input.model_policy_version,
        planner_input.research_cutoff,
    )
    actual_lineage = (
        plan.candidate_id,
        plan.b2_snapshot_id,
        plan.deep_comparison_id,
        plan.research_policy_version,
        plan.model_policy_version,
        plan.research_cutoff,
    )
    if actual_lineage != expected_lineage:
        raise ValueError("planner output lineage does not match immutable planner input")
    if research_policy.policy_version != planner_input.research_policy_version:
        raise ResearchPolicyError("planner input research policy version mismatch")
    _validate_plan_source_refs(plan, planner_input)
    validate_research_plan(plan, research_policy)
    return plan
