from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self

from pydantic import model_validator

from aic.domain.canonical import canonical_sha256

from .handoff import EXPECTED_TOP3
from .models import B3Model, ResearchGapPlan
from .runtime import ResponsesUsage


PLANNER_BATCH_ARTIFACT_VERSION = "B3_PLANNER_BATCH_ARTIFACT_v0_1"


class FrozenPlannerResult(B3Model):
    candidate: str
    handoff_hash: str
    response_id: str
    requested_model: str
    effective_model: str
    latency_ms: int
    usage: ResponsesUsage
    plan_hash: str
    research_plan: ResearchGapPlan

    @model_validator(mode="after")
    def _lineage(self) -> Self:
        if self.candidate != self.research_plan.candidate_id:
            raise ValueError("frozen planner candidate does not match ResearchGapPlan")
        if self.plan_hash != canonical_sha256(self.research_plan):
            raise ValueError("frozen planner plan_hash does not bind ResearchGapPlan")
        if self.latency_ms < 0:
            raise ValueError("frozen planner latency_ms must be non-negative")
        if not self.response_id or not self.requested_model or not self.effective_model:
            raise ValueError("frozen planner runtime identity fields must be non-empty")
        return self


class FrozenPlannerBatch(B3Model):
    artifact_version: str
    model_candidate: str
    handoff_hash: str
    results: tuple[FrozenPlannerResult, FrozenPlannerResult, FrozenPlannerResult]
    artifact_hash: str

    @model_validator(mode="after")
    def _batch_identity(self) -> Self:
        if self.artifact_version != PLANNER_BATCH_ARTIFACT_VERSION:
            raise ValueError("unexpected planner batch artifact version")
        if tuple(result.candidate for result in self.results) != EXPECTED_TOP3:
            raise ValueError("planner batch must contain exact frozen top-3 order")
        if any(result.handoff_hash != self.handoff_hash for result in self.results):
            raise ValueError("planner batch handoff hash mismatch")
        expected = canonical_sha256(self, exclude_fields=("artifact_hash",))
        if self.artifact_hash != expected:
            raise ValueError("artifact_hash does not bind frozen planner batch")
        return self

    @classmethod
    def build(cls, **payload: Any) -> Self:
        data = dict(payload)
        data.setdefault("artifact_version", PLANNER_BATCH_ARTIFACT_VERSION)
        data["artifact_hash"] = canonical_sha256(data)
        return cls(**data)


def save_frozen_planner_batch(batch: FrozenPlannerBatch, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        batch.model_dump_json(indent=2),
        encoding="utf-8",
    )


def load_frozen_planner_batch(path: str | Path) -> FrozenPlannerBatch:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("unable to read frozen planner batch artifact") from exc
    if not isinstance(payload, dict):
        raise ValueError("frozen planner batch artifact root must be an object")
    return FrozenPlannerBatch.model_validate(payload)
