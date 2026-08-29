from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Self

from pydantic import model_validator

from aic.domain.canonical import canonical_sha256

from .model_policy import (
    MODEL_CANDIDATE_LADDER,
    MODEL_POLICY_VERSION,
    ModelCandidate,
    ModelEvalResult,
    ModelSelectionStatus,
    select_model_from_eval,
)
from .models import B3Model
from .prompts import (
    PLANNER_PROMPT_VERSION,
    SYNTHESIS_PROMPT_VERSION,
    SYNTHESIS_REPAIR_PROMPT_VERSION,
    planner_prompt_hash,
    synthesis_prompt_hash,
    synthesis_repair_prompt_hash,
)


SELECTED_MODEL_AUTHORITY_VERSION = "B3_SELECTED_MODEL_AUTHORITY_v0_1"
SELECTED_MODEL_EVAL_VERSION = "B3_MODEL_EVAL_v0_3"
DEFAULT_SELECTED_MODEL_AUTHORITY_PATH = Path("config/event/b3_selected_model_v1.json")


class SelectedModelAuthorityError(ValueError):
    pass


class SelectedPromptManifest(B3Model):
    planner_prompt_version: str
    planner_prompt_hash: str
    synthesis_prompt_version: str
    synthesis_prompt_hash: str
    synthesis_repair_prompt_version: str
    synthesis_repair_prompt_hash: str


class SelectedEvalMetrics(B3Model):
    passed_cases: int
    required_cases: int = 12
    critical_safety_failures: int
    estimated_cost_usd: str
    latency_ms: int
    total_tokens: int

    @model_validator(mode="after")
    def _valid_counts(self) -> Self:
        if self.passed_cases < 0 or self.required_cases <= 0:
            raise ValueError("eval pass counts must be non-negative with positive required_cases")
        if self.critical_safety_failures < 0 or self.latency_ms < 0 or self.total_tokens < 0:
            raise ValueError("eval counters must be non-negative")
        return self


class SelectedModelAuthority(B3Model):
    artifact_version: str
    model_policy_version: str
    model_eval_artifact_hash: str
    eval_version: str
    prompt_manifest: SelectedPromptManifest
    selection_status: str
    selected_candidate: ModelCandidate
    selection_reason_code: str
    selected_eval_metrics: SelectedEvalMetrics
    full_ladder_pass_summary: Mapping[str, SelectedEvalMetrics]
    selection_hash: str

    @model_validator(mode="after")
    def _bind_frozen_selection(self) -> Self:
        if self.artifact_version != SELECTED_MODEL_AUTHORITY_VERSION:
            raise ValueError("unexpected selected-model authority version")
        if self.model_policy_version != MODEL_POLICY_VERSION:
            raise ValueError("selected-model authority model-policy version mismatch")
        if self.eval_version != SELECTED_MODEL_EVAL_VERSION:
            raise ValueError("selected-model authority eval version mismatch")
        if self.selection_status != ModelSelectionStatus.SELECTED.value:
            raise ValueError("selected-model authority must be SELECTED")
        if len(self.model_eval_artifact_hash) != 64:
            raise ValueError("selected-model authority requires model-eval artifact hash")

        expected_manifest = SelectedPromptManifest(
            planner_prompt_version=PLANNER_PROMPT_VERSION,
            planner_prompt_hash=planner_prompt_hash(),
            synthesis_prompt_version=SYNTHESIS_PROMPT_VERSION,
            synthesis_prompt_hash=synthesis_prompt_hash(),
            synthesis_repair_prompt_version=SYNTHESIS_REPAIR_PROMPT_VERSION,
            synthesis_repair_prompt_hash=synthesis_repair_prompt_hash(),
        )
        if self.prompt_manifest != expected_manifest:
            raise ValueError("selected-model authority prompt manifest is stale or mismatched")

        ladder_by_key = {candidate.candidate_key: candidate for candidate in MODEL_CANDIDATE_LADDER}
        if set(self.full_ladder_pass_summary) != set(ladder_by_key):
            raise ValueError("selected-model authority must cover exact M1/M2/M3 ladder")

        eval_results: list[ModelEvalResult] = []
        for key, candidate in ladder_by_key.items():
            metrics = self.full_ladder_pass_summary[key]
            eval_results.append(
                ModelEvalResult(
                    candidate_key=key,
                    all_required_checks_passed=(
                        metrics.passed_cases == metrics.required_cases
                    ),
                    critical_safety_failures=metrics.critical_safety_failures,
                    estimated_cost_usd=metrics.estimated_cost_usd,
                    latency_ms=metrics.latency_ms,
                    total_tokens=metrics.total_tokens,
                )
            )
            if metrics.required_cases != 12:
                raise ValueError("selected-model authority requires exact E1-E12 coverage")

        recomputed = select_model_from_eval(tuple(eval_results))
        if recomputed.status is not ModelSelectionStatus.SELECTED:
            raise ValueError("selected-model authority does not contain a passing selection")
        if recomputed.selected_candidate != self.selected_candidate:
            raise ValueError("selected-model authority candidate disagrees with frozen selection rule")
        if self.selected_candidate != ladder_by_key[self.selected_candidate.candidate_key]:
            raise ValueError("selected-model authority candidate differs from frozen ladder")

        selected_metrics = self.full_ladder_pass_summary[self.selected_candidate.candidate_key]
        if self.selected_eval_metrics != selected_metrics:
            raise ValueError("selected-model metrics do not match full-ladder record")
        if selected_metrics.passed_cases != 12 or selected_metrics.critical_safety_failures != 0:
            raise ValueError("selected model did not pass all required safety checks")

        expected_hash = canonical_sha256(self, exclude_fields=("selection_hash",))
        if self.selection_hash != expected_hash:
            raise ValueError("selection_hash does not bind selected-model authority")
        return self


def load_selected_model_authority(
    path: Path = DEFAULT_SELECTED_MODEL_AUTHORITY_PATH,
) -> SelectedModelAuthority:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelectedModelAuthorityError(f"unable to read selected-model authority: {path}") from exc
    try:
        return SelectedModelAuthority.model_validate(raw)
    except ValueError as exc:
        raise SelectedModelAuthorityError(str(exc)) from exc
