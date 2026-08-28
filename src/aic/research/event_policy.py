from __future__ import annotations

from .models import ResearchNeedType
from .policy import RESEARCH_POLICY_VERSION, ResearchPolicy


def build_event_research_policy() -> ResearchPolicy:
    """Return the frozen Alpaca-2026 B3 read-only research policy."""
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
        material_claim_categories=(
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
        ),
        inference_rule="Explicitly mark inference and bind supporting evidence.",
        unknown_rule="State material unknowns explicitly.",
        conflict_rule="Material conflicts remain visible.",
        numeric_claim_rule="No model arithmetic; use existing evidence or computed-value IDs.",
        research_cutoff_rule="Exclude evidence after the frozen research cutoff.",
        max_model_calls_per_candidate=3,
        repair_attempt_limit=1,
        failure_behavior="Bounded failure only; no silent tool or model expansion.",
    )
