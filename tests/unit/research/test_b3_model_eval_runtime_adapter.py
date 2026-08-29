from types import SimpleNamespace

import pytest

from aic.research.model_eval import ModelEvalHarnessError, build_eval_cases
from aic.research.model_eval_runtime import adapt_case_for_runtime_scoring


MANDATE_VERSION = "ALPACA_COMPETITION_V1_2026_08_29"


def _enough_status():
    return SimpleNamespace(value="ENOUGH")


def test_planner_runtime_result_is_unwrapped_before_semantic_score() -> None:
    case = build_eval_cases(MANDATE_VERSION)[0]
    assert case.case_id == "E1"
    assert case.stage == "PLANNER"

    adapted = adapt_case_for_runtime_scoring(case)
    fake_plan = SimpleNamespace(
        requested_needs=(),
        material_questions=(
            SimpleNamespace(current_evidence_status=_enough_status()),
        ),
    )
    fake_runtime = SimpleNamespace(plan=fake_plan)

    assert adapted.score(fake_runtime) == (True, ())


def test_planner_runtime_adapter_fails_closed_without_plan() -> None:
    case = adapt_case_for_runtime_scoring(build_eval_cases(MANDATE_VERSION)[0])
    with pytest.raises(ModelEvalHarnessError, match="PlannerRuntimeResult.plan"):
        case.score(SimpleNamespace())


def test_synthesis_case_is_not_rewrapped() -> None:
    case = build_eval_cases(MANDATE_VERSION)[2]
    assert case.case_id == "E3"
    assert case.stage == "SYNTHESIS"
    assert adapt_case_for_runtime_scoring(case) is case
