"""Deterministic B5 risk and option-selection code.

B5 contains no LLM authority and no broker-write authority.
"""

from .alpaca_b5_normalization_v1 import (
    B5AlpacaNormalizationError,
    B5NormalizedAlpacaInputs,
    normalize_b5_alpaca_inputs,
)
from .alpaca_options_readonly_v1 import (
    ReadOnlyAlpacaRequest,
    assert_read_only_request_plan,
    build_b5_read_only_request_plan,
)
from .b5_competition_pipeline_v1 import (
    B5CompetitionProposal,
    B5ReadOnlyRiskSnapshot,
    run_b5_competition_options,
)
from .options_competition_v1 import (
    B5CompetitionOptionsError,
    CompetitionOptionsPolicy,
    InvestHandoff,
    OptionContractCandidate,
    PremiumRiskBudgetInputs,
    SelectionResult,
    SizingResult,
    derive_premium_risk_budgets,
    load_competition_options_policy,
    select_long_call,
    size_long_call,
    validate_b4_invest_handoff,
)

__all__ = [
    "B5AlpacaNormalizationError",
    "B5CompetitionOptionsError",
    "B5CompetitionProposal",
    "B5NormalizedAlpacaInputs",
    "B5ReadOnlyRiskSnapshot",
    "CompetitionOptionsPolicy",
    "InvestHandoff",
    "OptionContractCandidate",
    "PremiumRiskBudgetInputs",
    "ReadOnlyAlpacaRequest",
    "SelectionResult",
    "SizingResult",
    "assert_read_only_request_plan",
    "build_b5_read_only_request_plan",
    "derive_premium_risk_budgets",
    "load_competition_options_policy",
    "normalize_b5_alpaca_inputs",
    "run_b5_competition_options",
    "select_long_call",
    "size_long_call",
    "validate_b4_invest_handoff",
]
