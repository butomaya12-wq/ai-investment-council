from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping, Self

from pydantic import field_validator, model_validator

from aic.domain.canonical import canonical_sha256

from .initial_eval_runtime import EXPECTED_INITIAL_CASE_IDS
from .model_policy import (
    INITIAL_MODEL_LADDER,
    MODEL_POLICY_VERSION,
    CouncilModelCandidate,
    CouncilModelStage,
    StageModelEvalResult,
    StageModelSelectionStatus,
    select_stage_model_from_eval,
)
from .models import B4Model
from .prompts import (
    BEAR_INITIAL_PROMPT_VERSION,
    BULL_INITIAL_PROMPT_VERSION,
    PROMPT_CONTRACT_VERSION,
    RED_TEAM_INITIAL_PROMPT_VERSION,
)


INITIAL_SELECTED_MODEL_AUTHORITY_VERSION = "B4_INITIAL_SELECTED_MODEL_AUTHORITY_v0_1"
INITIAL_SELECTED_MODEL_EVAL_VERSION = "B4_INITIAL_MODEL_EVAL_v0_2"
INITIAL_SELECTED_MODEL_EVAL_ARTIFACT_VERSION = "B4_INITIAL_MODEL_EVAL_ARTIFACT_v0_3"
DEFAULT_INITIAL_SELECTED_MODEL_AUTHORITY_PATH = Path(
    "config/event/b4_initial_selected_model_v1.json"
)
EXPECTED_INITIAL_EVAL_CALLS = 36


class InitialSelectedModelAuthorityError(ValueError):
    pass


class SelectedInitialEvalMetrics(B4Model):
    passed_cases: int
    required_cases: int
    critical_safety_failures: int
    estimated_cost_usd: Decimal
    latency_ms: int
    total_tokens: int
    record_hash: str

    @field_validator("estimated_cost_usd", mode="before")
    @classmethod
    def _no_binary_float_cost(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise TypeError("estimated_cost_usd must not use binary float")
        return value

    @model_validator(mode="after")
    def _valid_metrics(self) -> Self:
        if self.required_cases != len(EXPECTED_INITIAL_CASE_IDS):
            raise ValueError("Initial selected-model metrics require exact eval-case count")
        if not 0 <= self.passed_cases <= self.required_cases:
            raise ValueError("Initial selected-model pass count is invalid")
        if self.critical_safety_failures < 0 or self.latency_ms < 0 or self.total_tokens < 0:
            raise ValueError("Initial selected-model counters must be non-negative")
        if not self.estimated_cost_usd.is_finite() or self.estimated_cost_usd < 0:
            raise ValueError("Initial selected-model cost must be finite and non-negative")
        if len(self.record_hash) != 64:
            raise ValueError("Initial selected-model record_hash must be sha256")
        return self


class InitialSelectedModelAuthority(B4Model):
    artifact_version: str
    stage: CouncilModelStage
    model_policy_version: str
    model_eval_artifact_version: str
    model_eval_artifact_hash: str
    eval_version: str
    source_git_commit: str
    prompt_contract_version: str
    bull_initial_prompt_version: str
    bear_initial_prompt_version: str
    red_team_initial_prompt_version: str
    request_preflight_artifact_hash: str
    cost_preflight_artifact_hash: str
    dry_run_manifest_hash: str
    paid_run_id: str
    paid_authorization_artifact_hash: str
    receipt_manifest_hash: str
    cost_receipt_status: str
    semantic_replay_receipts_complete: int
    selection_status: str
    selected_candidate: CouncilModelCandidate
    selection_reason_code: str
    selected_eval_metrics: SelectedInitialEvalMetrics
    full_ladder_pass_summary: Mapping[str, SelectedInitialEvalMetrics]
    actual_paid_eval_cost_usd: Decimal
    selection_hash: str

    @field_validator("actual_paid_eval_cost_usd", mode="before")
    @classmethod
    def _no_binary_float_actual_cost(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise TypeError("actual_paid_eval_cost_usd must not use binary float")
        return value

    @model_validator(mode="after")
    def _bind_frozen_initial_selection(self) -> Self:
        if self.artifact_version != INITIAL_SELECTED_MODEL_AUTHORITY_VERSION:
            raise ValueError("unexpected B4 Initial selected-model authority version")
        if self.stage is not CouncilModelStage.INITIAL:
            raise ValueError("selected-model authority must bind INITIAL stage")
        if self.model_policy_version != MODEL_POLICY_VERSION:
            raise ValueError("selected-model authority model-policy version mismatch")
        if self.model_eval_artifact_version != INITIAL_SELECTED_MODEL_EVAL_ARTIFACT_VERSION:
            raise ValueError("selected-model authority eval artifact version mismatch")
        if self.eval_version != INITIAL_SELECTED_MODEL_EVAL_VERSION:
            raise ValueError("selected-model authority eval version mismatch")
        if self.prompt_contract_version != PROMPT_CONTRACT_VERSION:
            raise ValueError("selected-model authority prompt contract mismatch")
        if self.bull_initial_prompt_version != BULL_INITIAL_PROMPT_VERSION:
            raise ValueError("selected-model authority Bull prompt version mismatch")
        if self.bear_initial_prompt_version != BEAR_INITIAL_PROMPT_VERSION:
            raise ValueError("selected-model authority Bear prompt version mismatch")
        if self.red_team_initial_prompt_version != RED_TEAM_INITIAL_PROMPT_VERSION:
            raise ValueError("selected-model authority Red-Team prompt version mismatch")
        if self.selection_status != StageModelSelectionStatus.SELECTED.value:
            raise ValueError("selected-model authority must be SELECTED")
        if self.cost_receipt_status != "COMPLETE":
            raise ValueError("selected-model authority requires complete paid cost receipts")
        if self.semantic_replay_receipts_complete != EXPECTED_INITIAL_EVAL_CALLS:
            raise ValueError("selected-model authority requires 36 replayable paid receipts")
        if len(self.source_git_commit) != 40:
            raise ValueError("selected-model authority requires exact git commit")
        for value in (
            self.model_eval_artifact_hash,
            self.request_preflight_artifact_hash,
            self.cost_preflight_artifact_hash,
            self.dry_run_manifest_hash,
            self.paid_authorization_artifact_hash,
            self.receipt_manifest_hash,
        ):
            if len(value) != 64:
                raise ValueError("selected-model authority evidence hashes must be sha256")

        ladder_by_key = {item.candidate_key: item for item in INITIAL_MODEL_LADDER}
        if set(self.full_ladder_pass_summary) != set(ladder_by_key):
            raise ValueError("selected-model authority must cover exact L1-L4 ladder")

        eval_results: list[StageModelEvalResult] = []
        for key in ladder_by_key:
            metrics = self.full_ladder_pass_summary[key]
            eval_results.append(
                StageModelEvalResult(
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

        recomputed = select_stage_model_from_eval(
            CouncilModelStage.INITIAL, tuple(eval_results)
        )
        if recomputed.status is not StageModelSelectionStatus.SELECTED:
            raise ValueError("selected-model authority does not contain a passing model")
        if recomputed.selected_candidate != self.selected_candidate:
            raise ValueError(
                "selected-model authority candidate disagrees with frozen selection rule"
            )
        if self.selected_candidate != ladder_by_key[self.selected_candidate.candidate_key]:
            raise ValueError("selected-model authority candidate differs from frozen ladder")

        selected_metrics = self.full_ladder_pass_summary[
            self.selected_candidate.candidate_key
        ]
        if self.selected_eval_metrics != selected_metrics:
            raise ValueError("selected-model metrics do not match full-ladder record")
        if (
            selected_metrics.passed_cases != len(EXPECTED_INITIAL_CASE_IDS)
            or selected_metrics.critical_safety_failures != 0
        ):
            raise ValueError("selected Initial model did not pass all required checks")

        summed_cost = sum(
            (item.estimated_cost_usd for item in self.full_ladder_pass_summary.values()),
            Decimal("0"),
        )
        if self.actual_paid_eval_cost_usd != summed_cost:
            raise ValueError("actual paid eval cost disagrees with frozen ladder cost sum")

        expected_hash = canonical_sha256(self, exclude_fields=("selection_hash",))
        if self.selection_hash != expected_hash:
            raise ValueError("selection_hash does not bind B4 Initial selected-model authority")
        return self


def load_initial_selected_model_authority(
    path: Path = DEFAULT_INITIAL_SELECTED_MODEL_AUTHORITY_PATH,
) -> InitialSelectedModelAuthority:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InitialSelectedModelAuthorityError(
            f"unable to read B4 Initial selected-model authority: {path}"
        ) from exc
    try:
        return InitialSelectedModelAuthority.model_validate(raw)
    except ValueError as exc:
        raise InitialSelectedModelAuthorityError(str(exc)) from exc


def _candidate_metrics_from_paid_record(
    item: Mapping[str, Any],
) -> SelectedInitialEvalMetrics:
    cases = item.get("cases")
    if not isinstance(cases, list) or len(cases) != len(EXPECTED_INITIAL_CASE_IDS):
        raise InitialSelectedModelAuthorityError(
            "paid Initial eval candidate requires exact case records"
        )
    observed_case_ids: list[str] = []
    passed_cases = 0
    critical_failures = 0
    for case in cases:
        if not isinstance(case, Mapping):
            raise InitialSelectedModelAuthorityError("paid Initial eval case must be object")
        case_id = case.get("case_id")
        passed = case.get("passed")
        critical = case.get("critical_safety")
        if not isinstance(case_id, str) or type(passed) is not bool or type(critical) is not bool:
            raise InitialSelectedModelAuthorityError("paid Initial eval case fields invalid")
        observed_case_ids.append(case_id)
        passed_cases += int(passed)
        critical_failures += int(critical and not passed)
    if tuple(observed_case_ids) != EXPECTED_INITIAL_CASE_IDS:
        raise InitialSelectedModelAuthorityError(
            "paid Initial eval cases differ from frozen ordered case set"
        )

    record_hash = item.get("record_hash")
    if record_hash != canonical_sha256(item, exclude_fields=("record_hash",)):
        raise InitialSelectedModelAuthorityError("paid Initial eval candidate record hash mismatch")
    if item.get("passed_cases") != passed_cases:
        raise InitialSelectedModelAuthorityError("paid Initial eval passed_cases drift")
    if item.get("critical_safety_failures") != critical_failures:
        raise InitialSelectedModelAuthorityError("paid Initial eval critical count drift")
    expected_all = passed_cases == len(EXPECTED_INITIAL_CASE_IDS)
    if item.get("all_required_checks_passed") is not expected_all:
        raise InitialSelectedModelAuthorityError("paid Initial eval all-required flag drift")

    return SelectedInitialEvalMetrics(
        passed_cases=passed_cases,
        required_cases=item.get("required_cases"),
        critical_safety_failures=critical_failures,
        estimated_cost_usd=item.get("estimated_cost_usd"),
        latency_ms=item.get("latency_ms"),
        total_tokens=item.get("total_tokens"),
        record_hash=record_hash,
    )


def verify_initial_model_eval_artifact(
    payload: Mapping[str, Any],
    *,
    authority: InitialSelectedModelAuthority,
) -> None:
    actual_hash = payload.get("artifact_hash")
    if actual_hash != authority.model_eval_artifact_hash:
        raise InitialSelectedModelAuthorityError(
            "paid Initial eval artifact does not match frozen authority"
        )
    if actual_hash != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise InitialSelectedModelAuthorityError("paid Initial eval artifact hash mismatch")
    if payload.get("artifact_version") != authority.model_eval_artifact_version:
        raise InitialSelectedModelAuthorityError("paid Initial eval artifact version mismatch")
    if payload.get("status") != "PASS_SELECTED":
        raise InitialSelectedModelAuthorityError("paid Initial eval artifact is not PASS_SELECTED")
    if payload.get("run_id") != authority.paid_run_id:
        raise InitialSelectedModelAuthorityError("paid Initial eval run_id mismatch")
    if payload.get("code_commit_sha") != authority.source_git_commit:
        raise InitialSelectedModelAuthorityError("paid Initial eval git commit mismatch")
    if payload.get("eval_version") != authority.eval_version:
        raise InitialSelectedModelAuthorityError("paid Initial eval version mismatch")
    if payload.get("model_policy_version") != authority.model_policy_version:
        raise InitialSelectedModelAuthorityError("paid Initial eval model policy mismatch")
    if payload.get("cost_preflight_artifact_hash") != authority.cost_preflight_artifact_hash:
        raise InitialSelectedModelAuthorityError("paid Initial eval cost authority mismatch")
    if payload.get("paid_authorization_artifact_hash") != authority.paid_authorization_artifact_hash:
        raise InitialSelectedModelAuthorityError("paid Initial eval authorization mismatch")
    if payload.get("dry_run_manifest_hash") != authority.dry_run_manifest_hash:
        raise InitialSelectedModelAuthorityError("paid Initial eval dry-run manifest mismatch")
    if payload.get("receipt_manifest_hash") != authority.receipt_manifest_hash:
        raise InitialSelectedModelAuthorityError("paid Initial eval receipt manifest mismatch")
    if payload.get("cost_receipt_status") != authority.cost_receipt_status:
        raise InitialSelectedModelAuthorityError("paid Initial eval receipt status mismatch")
    if payload.get("dispatch_attempts") != EXPECTED_INITIAL_EVAL_CALLS:
        raise InitialSelectedModelAuthorityError("paid Initial eval dispatch count mismatch")
    if payload.get("model_calls") != EXPECTED_INITIAL_EVAL_CALLS:
        raise InitialSelectedModelAuthorityError("paid Initial eval model-call count mismatch")
    if Decimal(str(payload.get("actual_cost_usd"))) != authority.actual_paid_eval_cost_usd:
        raise InitialSelectedModelAuthorityError("paid Initial eval actual cost mismatch")
    if tuple(payload.get("case_ids", ())) != EXPECTED_INITIAL_CASE_IDS:
        raise InitialSelectedModelAuthorityError("paid Initial eval case_ids mismatch")

    candidate_records = payload.get("candidate_records")
    if not isinstance(candidate_records, list) or len(candidate_records) != len(INITIAL_MODEL_LADDER):
        raise InitialSelectedModelAuthorityError("paid Initial eval requires exact L1-L4 records")
    by_key = {
        item.get("candidate_key"): item
        for item in candidate_records
        if isinstance(item, Mapping) and isinstance(item.get("candidate_key"), str)
    }
    if set(by_key) != set(authority.full_ladder_pass_summary):
        raise InitialSelectedModelAuthorityError("paid Initial eval ladder coverage mismatch")

    ladder_by_key = {item.candidate_key: item for item in INITIAL_MODEL_LADDER}
    for key, expected_metrics in authority.full_ladder_pass_summary.items():
        item = by_key[key]
        expected_candidate = ladder_by_key[key]
        if (
            item.get("model") != expected_candidate.model
            or item.get("reasoning_effort") != expected_candidate.reasoning_effort
            or item.get("ladder_position") != expected_candidate.ladder_position
        ):
            raise InitialSelectedModelAuthorityError(
                f"paid Initial eval frozen candidate drift for {key}"
            )
        observed_metrics = _candidate_metrics_from_paid_record(item)
        if observed_metrics != expected_metrics:
            raise InitialSelectedModelAuthorityError(
                f"paid Initial eval metrics drift for {key}"
            )

    selection = payload.get("selection")
    if not isinstance(selection, Mapping) or selection.get("status") != "SELECTED":
        raise InitialSelectedModelAuthorityError("paid Initial eval selection missing")
    selected = selection.get("selected_candidate")
    if not isinstance(selected, Mapping):
        raise InitialSelectedModelAuthorityError("paid Initial eval selected candidate missing")
    selected_with_stage = dict(selected)
    selected_with_stage["stage"] = CouncilModelStage.INITIAL.value
    if CouncilModelCandidate.model_validate(selected_with_stage) != authority.selected_candidate:
        raise InitialSelectedModelAuthorityError("paid Initial eval selected candidate mismatch")
    if selection.get("reason_code") != authority.selection_reason_code:
        raise InitialSelectedModelAuthorityError("paid Initial eval selection reason mismatch")
