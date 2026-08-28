from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research.handoff import EXPECTED_TOP3
from aic.research.models import CurrentEvidenceStatus, ResearchGapPlan, ResearchQuestion
from aic.research.plan_freeze import (
    FrozenPlannerBatch,
    FrozenPlannerResult,
    load_frozen_planner_batch,
    save_frozen_planner_batch,
)
from aic.research.runtime import ResponsesUsage


CUTOFF = datetime(2026, 8, 28, 17, 34, tzinfo=UTC)
HANDOFF_HASH = "75df1e47b1f469bdce6d118f7a529b3f7a95061bcd760d756918a0e13e1a04e7"


def _plan(symbol: str) -> ResearchGapPlan:
    return ResearchGapPlan(
        research_plan_id=f"plan-{symbol}",
        candidate_id=symbol,
        b2_snapshot_id="B2_EVENT_SNAPSHOT",
        deep_comparison_id="B2_EVENT_COMPARISON",
        research_policy_version="RESEARCH_POLICY_vB3_0_1",
        model_policy_version="MODEL_POLICY_vB3_0_1",
        research_cutoff=CUTOFF,
        material_questions=(
            ResearchQuestion(
                question_id=f"q-{symbol}",
                category="risk",
                question_text="What material evidence remains?",
                why_material="Required before Council synthesis.",
                current_evidence_status=CurrentEvidenceStatus.MISSING,
            ),
        ),
        requested_needs=(),
    )


def _result(symbol: str) -> FrozenPlannerResult:
    plan = _plan(symbol)
    return FrozenPlannerResult(
        candidate=symbol,
        handoff_hash=HANDOFF_HASH,
        response_id=f"resp-{symbol}",
        requested_model="gpt-5.6-terra",
        effective_model="gpt-5.6-terra",
        latency_ms=100,
        usage=ResponsesUsage(
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=0,
            cached_tokens=0,
        ),
        plan_hash=canonical_sha256(plan),
        research_plan=plan,
    )


def test_three_candidate_planner_batch_round_trip_is_hash_bound(tmp_path) -> None:
    batch = FrozenPlannerBatch.build(
        model_candidate="M1",
        handoff_hash=HANDOFF_HASH,
        results=tuple(_result(symbol) for symbol in EXPECTED_TOP3),
    )
    path = tmp_path / "planner.json"
    save_frozen_planner_batch(batch, path)
    loaded = load_frozen_planner_batch(path)
    assert loaded == batch
    assert len(loaded.artifact_hash) == 64


def test_planner_batch_rejects_candidate_order_or_hash_tamper() -> None:
    good = FrozenPlannerBatch.build(
        model_candidate="M1",
        handoff_hash=HANDOFF_HASH,
        results=tuple(_result(symbol) for symbol in EXPECTED_TOP3),
    )
    payload = good.model_dump(mode="python")
    payload["results"] = tuple(reversed(payload["results"]))
    payload["artifact_hash"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "artifact_hash"}
    )
    with pytest.raises(ValueError, match="top-3 order"):
        FrozenPlannerBatch.model_validate(payload)

    payload = good.model_dump(mode="python")
    payload["artifact_hash"] = "0" * 64
    with pytest.raises(ValueError, match="artifact_hash"):
        FrozenPlannerBatch.model_validate(payload)
