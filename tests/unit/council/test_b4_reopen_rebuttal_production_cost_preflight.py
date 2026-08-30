from __future__ import annotations

from decimal import Decimal

import pytest

from aic.council.reopen_lifecycle_plan import EXPECTED_REBUTTAL_SELECTED
from aic.council.reopen_rebuttal_production_cost_preflight import (
    B4ReopenRebuttalCostPreflightError,
    EXPECTED_INITIAL_SPEND_UPPER_USD,
    EXPECTED_KNOWN_INITIAL_COST_USD,
    EXPECTED_RECOVERED_INITIAL_FREEZE_HASH,
    _decimal_text,
    _request_body_utf8_bytes,
    _selected_rebuttal_candidate,
    verify_recovered_initial_freeze,
    verify_reopen_lifecycle_for_rebuttal,
)


def test_selected_rebuttal_candidate_is_frozen_r3_sol_medium() -> None:
    selected = _selected_rebuttal_candidate()
    assert {
        "candidate_key": selected.candidate_key,
        "stage": selected.stage.value,
        "model": selected.model,
        "reasoning_effort": selected.reasoning_effort,
        "ladder_position": selected.ladder_position,
    } == EXPECTED_REBUTTAL_SELECTED
    assert EXPECTED_REBUTTAL_SELECTED == {
        "candidate_key": "R3",
        "stage": "REBUTTAL",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "ladder_position": 3,
    }


def test_recovered_initial_cost_bounds_are_frozen() -> None:
    assert EXPECTED_KNOWN_INITIAL_COST_USD == Decimal("0.3595905")
    assert EXPECTED_INITIAL_SPEND_UPPER_USD == Decimal("0.4963025")
    assert len(EXPECTED_RECOVERED_INITIAL_FREEZE_HASH) == 64


def test_decimal_text_is_canonical() -> None:
    assert _decimal_text(Decimal("0.5000")) == "0.5"
    assert _decimal_text(Decimal("0.0000000")) == "0"
    assert _decimal_text(Decimal("1.2345670")) == "1.234567"


def test_request_body_byte_count_uses_canonical_json_serialization() -> None:
    left = {"b": 2, "a": "é"}
    right = {"a": "é", "b": 2}
    assert _request_body_utf8_bytes(left) == _request_body_utf8_bytes(right)
    assert _request_body_utf8_bytes(left) == len('{"a":"é","b":2}'.encode("utf-8"))


def test_lifecycle_verifier_fails_closed_before_any_authority() -> None:
    with pytest.raises(B4ReopenRebuttalCostPreflightError, match="artifact_hash"):
        verify_reopen_lifecycle_for_rebuttal({"artifact_hash": "0" * 64})


def test_recovered_initial_verifier_fails_closed_on_hash_drift() -> None:
    with pytest.raises(B4ReopenRebuttalCostPreflightError, match="artifact_hash drift"):
        verify_recovered_initial_freeze(
            {"artifact_hash": "0" * 64},
            initial_plan=(),
        )
