from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import field_validator, model_validator

from .models import B3Model


MODEL_POLICY_VERSION = "MODEL_POLICY_vB3_0_1"


class ModelCandidate(B3Model):
    candidate_key: str
    model: str
    reasoning_effort: str
    ladder_position: int


MODEL_CANDIDATE_LADDER = (
    ModelCandidate(candidate_key="M1", model="gpt-5.6-terra", reasoning_effort="low", ladder_position=1),
    ModelCandidate(candidate_key="M2", model="gpt-5.6-terra", reasoning_effort="medium", ladder_position=2),
    ModelCandidate(candidate_key="M3", model="gpt-5.6-sol", reasoning_effort="medium", ladder_position=3),
)


class ModelEvalResult(B3Model):
    candidate_key: str
    all_required_checks_passed: bool
    critical_safety_failures: int
    estimated_cost_usd: Decimal
    latency_ms: int
    total_tokens: int

    @field_validator("estimated_cost_usd", mode="before")
    @classmethod
    def _no_float_cost(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise TypeError("estimated_cost_usd must not use binary float")
        return value

    @model_validator(mode="after")
    def _non_negative(self) -> Self:
        if self.critical_safety_failures < 0 or self.latency_ms < 0 or self.total_tokens < 0:
            raise ValueError("eval counters/latency/tokens must be non-negative")
        if not self.estimated_cost_usd.is_finite() or self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be finite and non-negative")
        return self


class ModelSelectionStatus(StrEnum):
    SELECTED = "SELECTED"
    BLOCKED = "BLOCKED"


class ModelSelectionResult(B3Model):
    model_policy_version: str
    status: ModelSelectionStatus
    selected_candidate: ModelCandidate | None
    reason_code: str


class ApiInvariants(B3Model):
    api_family: Literal["RESPONSES"] = "RESPONSES"
    store: Literal[False] = False
    tools_enabled: Literal[False] = False
    structured_outputs_required: Literal[True] = True
    hosted_web_search_enabled: Literal[False] = False
    hosted_mcp_enabled: Literal[False] = False
    code_interpreter_enabled: Literal[False] = False


API_INVARIANTS = ApiInvariants()


_CANDIDATE_BY_KEY = {candidate.candidate_key: candidate for candidate in MODEL_CANDIDATE_LADDER}
_FROZEN_KEYS = frozenset(_CANDIDATE_BY_KEY)


def select_model_from_eval(results: tuple[ModelEvalResult, ...]) -> ModelSelectionResult:
    if len({result.candidate_key for result in results}) != len(results):
        raise ValueError("model eval candidate_key values must be unique")
    result_keys = frozenset(result.candidate_key for result in results)
    if result_keys != _FROZEN_KEYS:
        raise ValueError("model eval must cover the full frozen M1/M2/M3 ladder")

    passing = tuple(
        result
        for result in results
        if result.all_required_checks_passed and result.critical_safety_failures == 0
    )
    if not passing:
        return ModelSelectionResult(
            model_policy_version=MODEL_POLICY_VERSION,
            status=ModelSelectionStatus.BLOCKED,
            selected_candidate=None,
            reason_code="NO_MODEL_CONFIGURATION_PASSES_ALL_REQUIRED_CHECKS",
        )

    selected_eval = min(
        passing,
        key=lambda result: (
            result.estimated_cost_usd,
            result.latency_ms,
            result.total_tokens,
            _CANDIDATE_BY_KEY[result.candidate_key].ladder_position,
        ),
    )
    return ModelSelectionResult(
        model_policy_version=MODEL_POLICY_VERSION,
        status=ModelSelectionStatus.SELECTED,
        selected_candidate=_CANDIDATE_BY_KEY[selected_eval.candidate_key],
        reason_code="LOWEST_COST_PASSING_CONFIG_THEN_LATENCY_THEN_TOKENS",
    )
