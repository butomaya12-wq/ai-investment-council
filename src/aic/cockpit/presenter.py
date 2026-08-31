"""A deliberately non-authoritative read model for the B7 cockpit.

This module projects only stable, branch-local B4 authority constants.  It does
not construct a canonical decision, calculate hashes, derive a trade proposal,
or call any provider or broker.  When an underlying canonical record is absent,
the projection keeps that absence visible instead of filling it with a value.
"""

from __future__ import annotations

from dataclasses import dataclass

from aic.council.judge_entry_preflight import (
    EXPECTED_CANDIDATE_ORDER,
    EXPECTED_REBUTTAL_FREEZE_HASH,
    EXPECTED_RESEARCH_REOPEN_CANDIDATES,
    JUDGE_ENTRY_PREFLIGHT_STATUS,
    JUDGE_ENTRY_PREFLIGHT_VERSION,
)
from aic.council.judge_production import EXPECTED_REQUIRED_UNKNOWN_REFS


@dataclass(frozen=True, slots=True)
class DecisionLaneView:
    name: str
    role: str
    state: str
    summary: str
    kind: str


@dataclass(frozen=True, slots=True)
class TraceBinding:
    label: str
    value: str
    source: str


@dataclass(frozen=True, slots=True)
class DecisionReadModel:
    """Presentation-only state; values are never a canonical decision record."""

    projection_id: str
    source_status: str
    candidates: tuple[str, ...]
    lanes: tuple[DecisionLaneView, ...]
    unknown_refs: tuple[str, ...]
    trace_bindings: tuple[TraceBinding, ...]


def build_b4_research_reopen_projection() -> DecisionReadModel:
    """Project the frozen B4 entry boundary without claiming a final decision.

    The source modules bind all three event candidates to a required B3 research
    reopen and explicitly leave Judge execution unauthorized.  That supports a
    truthful ``NO ORDER`` display, but not an invented Judge result or a B5/B6
    payload.
    """

    unknown_ref = EXPECTED_REQUIRED_UNKNOWN_REFS[0]
    return DecisionReadModel(
        projection_id="B4-RESEARCH-REOPEN-BOUNDARY",
        source_status=JUDGE_ENTRY_PREFLIGHT_STATUS,
        candidates=EXPECTED_CANDIDATE_ORDER,
        lanes=(
            DecisionLaneView(
                name="Bull",
                role="Thesis case",
                state="UNKNOWN",
                summary="No canonical Bull opinion record is loaded into this UI projection.",
                kind="bull",
            ),
            DecisionLaneView(
                name="Bear",
                role="Counter-thesis case",
                state="UNKNOWN",
                summary="No canonical Bear opinion record is loaded into this UI projection.",
                kind="bear",
            ),
            DecisionLaneView(
                name="Red Team",
                role="Integrity challenge — not a third vote",
                state="BLOCKING",
                summary=(
                    f"Material unknown {unknown_ref} remains visible and requires "
                    "a separate B3 research reopen lifecycle."
                ),
                kind="red-team",
            ),
            DecisionLaneView(
                name="Judge",
                role="Independent verdict authority after adversarial deliberation",
                state="PENDING / NOT AUTHORIZED",
                summary=(
                    "No canonical final decision is loaded. The frozen entry boundary "
                    "does not authorize Judge execution or INVEST persistence."
                ),
                kind="judge",
            ),
        ),
        unknown_refs=EXPECTED_REQUIRED_UNKNOWN_REFS,
        trace_bindings=(
            TraceBinding(
                label="B4 judge-entry protocol",
                value=JUDGE_ENTRY_PREFLIGHT_VERSION,
                source="aic.council.judge_entry_preflight",
            ),
            TraceBinding(
                label="B4 entry status",
                value=JUDGE_ENTRY_PREFLIGHT_STATUS,
                source="aic.council.judge_entry_preflight",
            ),
            TraceBinding(
                label="Rebuttal freeze binding",
                value=EXPECTED_REBUTTAL_FREEZE_HASH,
                source="aic.council.judge_entry_preflight",
            ),
            TraceBinding(
                label="Research-reopen candidates",
                value=", ".join(EXPECTED_RESEARCH_REOPEN_CANDIDATES),
                source="aic.council.judge_entry_preflight",
            ),
        ),
    )
