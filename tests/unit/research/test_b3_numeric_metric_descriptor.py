from __future__ import annotations

from types import SimpleNamespace

import pytest

from aic.research.validate import (
    CandidatePacketValidationError,
    _validate_numeric_provenance,
)


COMPUTED_ID = "B2_META_return_20s"
COMPUTED_VALUE = "0.059495760903845797079939892028273"


def _input():
    return SimpleNamespace(
        computed_values=(
            SimpleNamespace(
                computed_value_id=COMPUTED_ID,
                metric_id="return_20s",
                value=COMPUTED_VALUE,
                unit="ratio",
            ),
        ),
        evidence_items=(),
    )


def _draft(claim_text: str):
    return SimpleNamespace(
        claims=(
            SimpleNamespace(
                claim_id="CLM_META_MARKET_1",
                claim_text=claim_text,
                computed_value_ids=(COMPUTED_ID,),
                evidence_ids=(),
            ),
        )
    )


def test_application_owned_20_session_metric_descriptor_is_bound() -> None:
    _validate_numeric_provenance(
        _draft("The frozen B2 20-session trailing-return metric is decision-relevant context."),
        _input(),
    )


def test_metric_descriptor_binding_does_not_authorize_percentage_value() -> None:
    with pytest.raises(CandidatePacketValidationError, match=r"20%"):
        _validate_numeric_provenance(
            _draft("The frozen B2 metric supports a 20% growth conclusion."),
            _input(),
        )


def test_metric_descriptor_binding_does_not_authorize_wrong_window() -> None:
    with pytest.raises(CandidatePacketValidationError, match=r"42"):
        _validate_numeric_provenance(
            _draft("The frozen B2 42-session trailing-return metric is decision-relevant context."),
            _input(),
        )


def test_exact_computed_value_still_passes() -> None:
    _validate_numeric_provenance(
        _draft(f"The frozen B2 20-session trailing-return metric is {COMPUTED_VALUE}."),
        _input(),
    )
