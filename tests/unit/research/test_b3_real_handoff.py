import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aic.research.handoff import (
    EXPECTED_TOP3,
    B2RealEventHandoff,
    build_planner_input_from_handoff,
    load_real_event_handoff,
)


HANDOFF_PATH = Path("config/event/b2_real_event_handoff_v0_1.json")
EXPECTED_HASH = "75df1e47b1f469bdce6d118f7a529b3f7a95061bcd760d756918a0e13e1a04e7"


def test_real_event_handoff_loads_and_binds_exact_top3() -> None:
    handoff = load_real_event_handoff(HANDOFF_PATH)
    assert handoff.top3 == EXPECTED_TOP3
    assert handoff.handoff_hash == EXPECTED_HASH
    assert tuple(candidate.symbol for candidate in handoff.candidates) == EXPECTED_TOP3


@pytest.mark.parametrize("symbol", EXPECTED_TOP3)
def test_planner_input_uses_real_handoff_ids_without_numeric_values_in_prompt_context(symbol: str) -> None:
    handoff = load_real_event_handoff(HANDOFF_PATH)
    candidate = handoff.candidate(symbol)
    planner_input = build_planner_input_from_handoff(handoff, symbol=symbol)

    assert planner_input.candidate_id == symbol
    assert planner_input.b2_snapshot_id == handoff.b2_snapshot_ref
    assert planner_input.deep_comparison_id == handoff.deep_comparison_ref
    assert candidate.sec_accession in planner_input.allowed_source_handles
    refs = {
        ref
        for item in planner_input.context_items
        for ref in item.computed_value_refs
    }
    assert refs == {metric.computed_value_id for metric in candidate.metrics}
    descriptions = " ".join(item.description for item in planner_input.context_items)
    assert all(metric.value not in descriptions for metric in candidate.metrics)


def test_handoff_tamper_is_rejected() -> None:
    payload = json.loads(HANDOFF_PATH.read_text(encoding="utf-8"))
    payload["candidates"][0]["metrics"][0]["value"] = "0"
    with pytest.raises(ValidationError, match="handoff_hash"):
        B2RealEventHandoff.model_validate(payload)


def test_unknown_candidate_is_rejected() -> None:
    handoff = load_real_event_handoff(HANDOFF_PATH)
    with pytest.raises(KeyError, match="not present"):
        build_planner_input_from_handoff(handoff, symbol="AAPL")
