from __future__ import annotations

from typing import Self

from pydantic import model_validator

from .models import (
    AlpacaNewsWindowParameters,
    CompanyIRDocumentParameters,
    ResearchGapPlan,
    ResearchNeedType,
    SecFilingSectionParameters,
    B3Model,
)


RESEARCH_POLICY_VERSION = "RESEARCH_POLICY_vB3_0_1"
MAX_NEEDS_PER_CANDIDATE = 6
MAX_ITEMS_PER_NEED = 5
MAX_TOTAL_EVIDENCE_ITEMS_PER_CANDIDATE = 30
REPAIR_ATTEMPT_LIMIT = 1


class ResearchPolicyError(ValueError):
    pass


class ResearchPolicy(B3Model):
    policy_version: str
    allowed_need_types: tuple[ResearchNeedType, ...]
    max_needs_per_candidate: int
    max_items_per_need: int
    max_total_evidence_items_per_candidate: int
    allowed_source_tiers: tuple[str, ...]
    allowed_sec_forms: tuple[str, ...]
    allowed_sec_sections: tuple[str, ...]
    company_ir_policy_ref: str | None
    news_window_policy_ref: str
    material_claim_categories: tuple[str, ...]
    inference_rule: str
    unknown_rule: str
    conflict_rule: str
    numeric_claim_rule: str
    research_cutoff_rule: str
    max_model_calls_per_candidate: int
    repair_attempt_limit: int
    failure_behavior: str

    @model_validator(mode="after")
    def _bounded_v1(self) -> Self:
        if self.policy_version != RESEARCH_POLICY_VERSION:
            raise ValueError("unexpected B3 research policy version")
        if self.max_needs_per_candidate != MAX_NEEDS_PER_CANDIDATE:
            raise ValueError("max_needs_per_candidate must equal frozen B3 V1 bound")
        if self.max_items_per_need != MAX_ITEMS_PER_NEED:
            raise ValueError("max_items_per_need must equal frozen B3 V1 bound")
        if self.max_total_evidence_items_per_candidate != MAX_TOTAL_EVIDENCE_ITEMS_PER_CANDIDATE:
            raise ValueError("max_total_evidence_items_per_candidate must equal frozen B3 V1 bound")
        if self.max_model_calls_per_candidate < 2:
            raise ValueError("max_model_calls_per_candidate must allow at least planner + synthesis")
        if self.repair_attempt_limit != REPAIR_ATTEMPT_LIMIT:
            raise ValueError("repair_attempt_limit must equal frozen B3 V1 bound")
        if not self.allowed_need_types or len(set(self.allowed_need_types)) != len(self.allowed_need_types):
            raise ValueError("allowed_need_types must be non-empty and unique")
        if set(self.allowed_need_types) - set(ResearchNeedType):
            raise ValueError("unknown ResearchNeed type in policy")
        required_non_empty = (
            self.allowed_source_tiers,
            self.allowed_sec_forms,
            self.allowed_sec_sections,
            self.material_claim_categories,
        )
        if any(not values for values in required_non_empty):
            raise ValueError("source/form/section/category allowlists must be non-empty")
        for text in (
            self.news_window_policy_ref,
            self.inference_rule,
            self.unknown_rule,
            self.conflict_rule,
            self.numeric_claim_rule,
            self.research_cutoff_rule,
            self.failure_behavior,
        ):
            if not text or text != text.strip():
                raise ValueError("policy text/reference fields must be non-empty trimmed strings")
        return self


def _required_source_tier(need_type: ResearchNeedType) -> str:
    return {
        ResearchNeedType.NEED_B2_EVIDENCE_DETAIL: "B2",
        ResearchNeedType.NEED_B2_COMPUTED_VALUE_DETAIL: "B2",
        ResearchNeedType.NEED_SEC_FILING_SECTION: "SEC",
        ResearchNeedType.NEED_ALPACA_NEWS_WINDOW: "ALPACA_NEWS",
        ResearchNeedType.NEED_CORPORATE_ACTION_DETAIL: "ALPACA_CORPORATE_ACTIONS",
        ResearchNeedType.NEED_COMPANY_IR_DOCUMENT: "IR_REGISTRY",
    }[need_type]


def validate_research_plan(plan: ResearchGapPlan, policy: ResearchPolicy) -> None:
    if plan.research_policy_version != policy.policy_version:
        raise ResearchPolicyError("ResearchGapPlan research_policy_version mismatch")
    if len(plan.requested_needs) > policy.max_needs_per_candidate:
        raise ResearchPolicyError("ResearchGapPlan exceeds max_needs_per_candidate")

    total_requested_items = 0
    allowed_tiers = set(policy.allowed_source_tiers)
    for need in plan.requested_needs:
        if need.need_type not in policy.allowed_need_types:
            raise ResearchPolicyError(f"need type not allowed: {need.need_type.value}")
        required_tier = _required_source_tier(need.need_type)
        if required_tier not in allowed_tiers:
            raise ResearchPolicyError(
                f"need source tier not allowed by research policy: {required_tier}"
            )
        if need.max_items > policy.max_items_per_need:
            raise ResearchPolicyError("ResearchNeed exceeds max_items_per_need")
        total_requested_items += need.max_items

        if isinstance(need.parameters, SecFilingSectionParameters):
            not_allowed = tuple(
                section for section in need.parameters.sections if section not in policy.allowed_sec_sections
            )
            if not_allowed:
                raise ResearchPolicyError("SEC section requested outside policy allowlist")

        if isinstance(need.parameters, AlpacaNewsWindowParameters):
            if need.parameters.window_end > plan.research_cutoff:
                raise ResearchPolicyError("news need extends beyond research_cutoff")

        if isinstance(need.parameters, CompanyIRDocumentParameters) and policy.company_ir_policy_ref is None:
            raise ResearchPolicyError("company IR need requested without approved IR policy")

    if total_requested_items > policy.max_total_evidence_items_per_candidate:
        raise ResearchPolicyError("ResearchGapPlan exceeds total evidence budget")
