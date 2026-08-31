from __future__ import annotations

import pytest

from aic.council import reopen_rebuttal_runtime_v02 as rt


def _context(*, effective, candidate_gaps):
    return {
        "effective_data_gap_refs": effective,
        "model_input": {
            "candidate_model_input": {
                "data_gap_refs": candidate_gaps,
            }
        },
    }


def test_v02_runtime_versions_are_new_evidence_contracts() -> None:
    assert rt.RUNTIME_VERSION == "B4_REOPEN_REBUTTAL_PRODUCTION_RUNTIME_v0_2"
    assert rt.DRY_VERSION == "B4_REOPEN_REBUTTAL_RUNTIME_DRY_v0_2"
    assert rt.AUTH_VERSION == "B4_REOPEN_REBUTTAL_RUNTIME_PAID_AUTHORIZATION_v0_2"
    assert rt.EVENT_VERSION == "B4_REOPEN_REBUTTAL_RUNTIME_JOURNAL_EVENT_v0_2"
    assert rt.RECEIPT_VERSION == "B4_REOPEN_REBUTTAL_RUNTIME_PAID_CALL_RECEIPT_v0_2"
    assert rt.FREEZE_VERSION == "B4_REOPEN_REBUTTAL_COUNCIL_FREEZE_v0_2"
    assert rt.BLOCKED_VERSION == "B4_REOPEN_REBUTTAL_COUNCIL_BLOCKED_v0_2"


def test_required_unknown_refs_are_derived_from_closed_effective_data_gaps() -> None:
    assert rt.required_unknown_refs_from_context(
        _context(effective=[], candidate_gaps=[])
    ) == ()


def test_required_unknown_refs_preserve_nonempty_effective_gaps_when_consistent() -> None:
    assert rt.required_unknown_refs_from_context(
        _context(effective=["GAP_A", "GAP_B"], candidate_gaps=["GAP_A", "GAP_B"])
    ) == ("GAP_A", "GAP_B")


def test_required_unknown_refs_reject_candidate_input_mismatch() -> None:
    with pytest.raises(rt.B4ReopenRebuttalRuntimeError, match="differ"):
        rt.required_unknown_refs_from_context(
            _context(effective=[], candidate_gaps=["GAP_A"])
        )


def test_required_unknown_refs_reject_duplicates() -> None:
    with pytest.raises(rt.B4ReopenRebuttalRuntimeError, match="unique"):
        rt.required_unknown_refs_from_context(
            _context(effective=["GAP_A", "GAP_A"], candidate_gaps=["GAP_A", "GAP_A"])
        )
