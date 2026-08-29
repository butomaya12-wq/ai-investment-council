from __future__ import annotations

from dataclasses import replace

from .model_eval import EvalCase, ModelEvalHarnessError


def adapt_case_for_runtime_scoring(case: EvalCase) -> EvalCase:
    """Adapt planner eval scorers to the production planner runtime envelope.

    Planner cases define semantic scorers against ResearchGapPlan, while the
    production runtime returns PlannerRuntimeResult so observability/call evidence
    remains available. Synthesis scorers already consume their runtime result and
    therefore require no adaptation.
    """
    if case.stage != "PLANNER":
        return case

    semantic_score = case.score

    def score_runtime(runtime_result: object) -> tuple[bool, tuple[str, ...]]:
        plan = getattr(runtime_result, "plan", None)
        if plan is None:
            raise ModelEvalHarnessError(
                "planner eval scorer requires PlannerRuntimeResult.plan"
            )
        return semantic_score(plan)

    return replace(case, score=score_runtime)
