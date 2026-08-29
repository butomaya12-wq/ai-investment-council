from __future__ import annotations

from typing import Literal, Self

from pydantic import model_validator

from .models import B4Model, CouncilLane, INITIAL_COUNCIL_LANES


COUNCIL_POLICY_VERSION = "COUNCIL_POLICY_vB4_0_1"
JUDGE_POLICY_VERSION = "JUDGE_POLICY_vB4_0_1"


class CouncilPolicy(B4Model):
    policy_version: Literal["COUNCIL_POLICY_vB4_0_1"] = COUNCIL_POLICY_VERSION
    allowed_roles: tuple[CouncilLane, CouncilLane, CouncilLane] = INITIAL_COUNCIL_LANES
    initial_rounds: Literal[1] = 1
    rebuttal_rounds_max: Literal[1] = 1
    new_evidence_allowed: Literal[False] = False
    new_provider_reads_allowed: Literal[False] = False
    numeric_authority: Literal["NONE"] = "NONE"
    max_initial_model_calls: Literal[9] = 9
    max_rebuttal_model_calls: Literal[3] = 3
    max_judge_model_calls: Literal[1] = 1
    repair_attempt_limit_per_output: Literal[1] = 1
    majority_vote_rule: Literal["FORBIDDEN"] = "FORBIDDEN"

    @model_validator(mode="after")
    def _topology(self) -> Self:
        if self.allowed_roles != INITIAL_COUNCIL_LANES:
            raise ValueError("B4 V1 roles are exactly BULL/BEAR/RED_TEAM")
        if (
            self.max_initial_model_calls
            + self.max_rebuttal_model_calls
            + self.max_judge_model_calls
            != 13
        ):
            raise ValueError("B4 V1 baseline topology must contain exactly 13 model calls")
        return self


class JudgePolicy(B4Model):
    policy_version: Literal["JUDGE_POLICY_vB4_0_1"] = JUDGE_POLICY_VERSION
    majority_vote_rule: Literal["FORBIDDEN"] = "FORBIDDEN"
    red_team_directional_vote: Literal[False] = False
    blocking_conflict_allows_invest: Literal[False] = False
    blocking_unknown_allows_invest: Literal[False] = False
    unresolved_blocking_integrity_finding_allows_invest: Literal[False] = False
    research_reopen_allows_invest: Literal[False] = False
    watch_allowed: Literal[True] = True
    abstain_allowed: Literal[True] = True
    execution_authority: Literal[False] = False
    risk_authority: Literal[False] = False
    approval_authority: Literal[False] = False


COUNCIL_POLICY = CouncilPolicy()
JUDGE_POLICY = JudgePolicy()
