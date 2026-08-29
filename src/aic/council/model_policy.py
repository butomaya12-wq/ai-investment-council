from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import field_validator, model_validator

from .models import B4Model


MODEL_POLICY_VERSION = "MODEL_POLICY_vB4_0_1"


class CouncilModelStage(StrEnum):
    INITIAL = "INITIAL"
    REBUTTAL = "REBUTTAL"
    JUDGE = "JUDGE"


class CouncilModelCandidate(B4Model):
    candidate_key: str
    stage: CouncilModelStage
    model: str
    reasoning_effort: str
    ladder_position: int


INITIAL_MODEL_LADDER = (
    CouncilModelCandidate(candidate_key="L1", stage=CouncilModelStage.INITIAL, model="gpt-5.6-luna", reasoning_effort="medium", ladder_position=1),
    CouncilModelCandidate(candidate_key="L2", stage=CouncilModelStage.INITIAL, model="gpt-5.6-terra", reasoning_effort="low", ladder_position=2),
    CouncilModelCandidate(candidate_key="L3", stage=CouncilModelStage.INITIAL, model="gpt-5.6-terra", reasoning_effort="medium", ladder_position=3),
    CouncilModelCandidate(candidate_key="L4", stage=CouncilModelStage.INITIAL, model="gpt-5.6-sol", reasoning_effort="medium", ladder_position=4),
)
REBUTTAL_MODEL_LADDER = (
    CouncilModelCandidate(candidate_key="R1", stage=CouncilModelStage.REBUTTAL, model="gpt-5.6-terra", reasoning_effort="low", ladder_position=1),
    CouncilModelCandidate(candidate_key="R2", stage=CouncilModelStage.REBUTTAL, model="gpt-5.6-terra", reasoning_effort="medium", ladder_position=2),
    CouncilModelCandidate(candidate_key="R3", stage=CouncilModelStage.REBUTTAL, model="gpt-5.6-sol", reasoning_effort="medium", ladder_position=3),
)
JUDGE_MODEL_LADDER = (
    CouncilModelCandidate(candidate_key="J1", stage=CouncilModelStage.JUDGE, model="gpt-5.6-terra", reasoning_effort="medium", ladder_position=1),
    CouncilModelCandidate(candidate_key="J2", stage=CouncilModelStage.JUDGE, model="gpt-5.6-sol", reasoning_effort="medium", ladder_position=2),
    CouncilModelCandidate(candidate_key="J3", stage=CouncilModelStage.JUDGE, model="gpt-5.6-sol", reasoning_effort="high", ladder_position=3),
)


MODEL_LADDERS: dict[CouncilModelStage, tuple[CouncilModelCandidate, ...]] = {
    CouncilModelStage.INITIAL: INITIAL_MODEL_LADDER,
    CouncilModelStage.REBUTTAL: REBUTTAL_MODEL_LADDER,
    CouncilModelStage.JUDGE: JUDGE_MODEL_LADDER,
}


class B4ApiInvariants(B4Model):
    api_family: Literal["RESPONSES"] = "RESPONSES"
    store: Literal[False] = False
    tools_enabled: Literal[False] = False
    hosted_tools_enabled: Literal[False] = False
    provider_credentials_model_visible: Literal[False] = False
    broker_credentials_model_visible: Literal[False] = False
    structured_outputs_required: Literal[True] = True


API_INVARIANTS = B4ApiInvariants()


class StageModelEvalResult(B4Model):
    candidate_key: str
    all_required_checks_passed: bool
    critical_safety_failures: int
    estimated_cost_usd: Decimal
    latency_ms: int
    total_tokens: int

    @field_validator("estimated_cost_usd", mode="before")
    @classmethod
    def _no_binary_float_cost(cls, value: Any) -> Any:
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


class StageModelSelectionStatus(StrEnum):
    SELECTED = "SELECTED"
    BLOCKED = "BLOCKED"


class StageModelSelectionResult(B4Model):
    model_policy_version: str
    stage: CouncilModelStage
    status: StageModelSelectionStatus
    selected_candidate: CouncilModelCandidate | None
    reason_code: str


def select_stage_model_from_eval(
    stage: CouncilModelStage,
    results: tuple[StageModelEvalResult, ...],
) -> StageModelSelectionResult:
    ladder = MODEL_LADDERS[stage]
    by_key = {candidate.candidate_key: candidate for candidate in ladder}
    if len({result.candidate_key for result in results}) != len(results):
        raise ValueError("stage model eval candidate_key values must be unique")
    if frozenset(result.candidate_key for result in results) != frozenset(by_key):
        raise ValueError("stage model eval must cover the full frozen candidate ladder")

    passing = tuple(
        result
        for result in results
        if result.all_required_checks_passed and result.critical_safety_failures == 0
    )
    if not passing:
        return StageModelSelectionResult(
            model_policy_version=MODEL_POLICY_VERSION,
            stage=stage,
            status=StageModelSelectionStatus.BLOCKED,
            selected_candidate=None,
            reason_code="NO_MODEL_CONFIGURATION_PASSES_ALL_REQUIRED_CHECKS",
        )

    selected = min(
        passing,
        key=lambda result: (
            result.estimated_cost_usd,
            result.latency_ms,
            result.total_tokens,
            by_key[result.candidate_key].ladder_position,
        ),
    )
    return StageModelSelectionResult(
        model_policy_version=MODEL_POLICY_VERSION,
        stage=stage,
        status=StageModelSelectionStatus.SELECTED,
        selected_candidate=by_key[selected.candidate_key],
        reason_code="LOWEST_COST_PASSING_CONFIG_THEN_LATENCY_THEN_TOKENS",
    )
