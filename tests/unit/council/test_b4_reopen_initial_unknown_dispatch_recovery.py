from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from aic.council.models import CouncilLane
from aic.council.reopen_initial_unknown_dispatch_recovery import (
    B4ReopenInitialUnknownDispatchRecoveryError,
    EXPECTED_ONE_CALL_RECOVERY_CEILING_USD,
    EXPECTED_POST_RECOVERY_AGGREGATE_SPEND_UPPER_USD,
    EXPECTED_PRE_RECOVERY_STAGE_SPEND_UPPER_USD,
    EXPECTED_UNKNOWN_REQUEST_BYTES,
    EXPECTED_UNKNOWN_REQUEST_HASH,
    compute_recovery_cost_bounds,
    validate_missing_plan_item,
)
from aic.council.request import CouncilRequestStage


def _item(*, store: bool = False, candidate: str = "META"):
    return SimpleNamespace(
        dispatch_index=9,
        candidate_id=candidate,
        lane=CouncilLane.RED_TEAM,
        stage=CouncilRequestStage.RED_TEAM_INITIAL,
        request=SimpleNamespace(
            request_hash=EXPECTED_UNKNOWN_REQUEST_HASH,
            request_payload={
                "store": store,
                "max_output_tokens": 4096,
            },
        ),
        request_body_utf8_bytes=EXPECTED_UNKNOWN_REQUEST_BYTES,
    )


def _row():
    return {
        "candidate_id": "META",
        "lane": "RED_TEAM",
        "stage": "RED_TEAM_INITIAL",
        "request_hash": EXPECTED_UNKNOWN_REQUEST_HASH,
        "request_body_utf8_bytes": EXPECTED_UNKNOWN_REQUEST_BYTES,
        "max_output_tokens": 4096,
        "model": "gpt-5.6-terra",
        "reasoning_effort": "low",
        "per_call_cost_upper_bound_usd": str(EXPECTED_ONE_CALL_RECOVERY_CEILING_USD),
    }


def test_recovery_cost_envelope_counts_unknown_original_and_one_recovery() -> None:
    pre, post = compute_recovery_cost_bounds(
        known_cost_usd=Decimal("0.3255090"),
        missing_call_ceiling_usd=EXPECTED_ONE_CALL_RECOVERY_CEILING_USD,
    )
    assert pre == EXPECTED_PRE_RECOVERY_STAGE_SPEND_UPPER_USD
    assert post == EXPECTED_POST_RECOVERY_AGGREGATE_SPEND_UPPER_USD


def test_missing_meta_red_team_request_is_exact_and_store_false() -> None:
    ceiling = validate_missing_plan_item(_item(), _row())
    assert ceiling == Decimal("0.136712")


def test_missing_request_recovery_rejects_store_true() -> None:
    with pytest.raises(
        B4ReopenInitialUnknownDispatchRecoveryError,
        match="store=false",
    ):
        validate_missing_plan_item(_item(store=True), _row())


def test_missing_request_recovery_rejects_candidate_drift() -> None:
    with pytest.raises(
        B4ReopenInitialUnknownDispatchRecoveryError,
        match="candidate drift",
    ):
        validate_missing_plan_item(_item(candidate="MSFT"), _row())
