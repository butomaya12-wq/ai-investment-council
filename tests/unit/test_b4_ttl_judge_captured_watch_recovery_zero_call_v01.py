from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

from aic.council.proposal import (
    DecisionChangeConditionDraft,
    JudgeDecisionProposalDraft,
    JudgeEvidenceStatus,
    JudgeNextDirective,
    JudgeOutcome,
)


MODULE_PATH = (
    Path(__file__)
    .resolve()
    .parents[2]
    / "scripts"
    / "b4_ttl_judge_captured_watch_recovery_zero_call_v01.py"
)


def _load():
    spec = (
        importlib.util
        .spec_from_file_location(
            "test_b4_ttl_watch_recovery",
            MODULE_PATH,
        )
    )

    assert spec is not None
    assert spec.loader is not None

    module = (
        importlib.util
        .module_from_spec(
            spec
        )
    )

    sys.modules[
        spec.name
    ] = module

    spec.loader.exec_module(
        module
    )

    return module


MODULE = _load()


def _captured_watch():
    return JudgeDecisionProposalDraft(
        b4_decision_id=(
            "B4_JUDGE_DECISION_PROPOSAL_001"
        ),

        outcome=JudgeOutcome.WATCH,

        primary_candidate_id="NVDA",

        watch_candidate_ids=(
            "NVDA",
            "MSFT",
            "META",
        ),

        mandate_version="MANDATE_TEST",

        deep_comparison_id=(
            "DEEP_COMPARISON_TEST"
        ),

        judge_input_hash=(
            "1" * 64
        ),

        council_policy_version=(
            "COUNCIL_TEST"
        ),

        judge_policy_version=(
            "JUDGE_TEST"
        ),

        model_policy_version=(
            "MODEL_TEST"
        ),

        selected_candidate_basis_claim_ids=(),

        why_not_other_candidates=(),

        unresolved_dispute_refs=(),

        material_conflict_refs=(),

        material_unknown_refs=(),

        blocking_reason_codes=(
            "ALPACA_NEWS_PAGINATION_INCOMPLETE",
            "VALUATION_EVIDENCE_NOT_SUPPLIED",
        ),

        research_reopen_required=True,

        research_reopen_reason_codes=(
            "ALPACA_NEWS_PAGINATION_INCOMPLETE",
            "VALUATION_EVIDENCE_NOT_SUPPLIED",
        ),

        what_would_change_decision=(
            DecisionChangeConditionDraft(
                condition_id="COND_1",
                condition_text=(
                    "Monitor supplied evidence."
                ),
                source_or_claim_refs=(),
            ),
        ),

        invalidation_condition_refs=(),

        evidence_status=(
            JudgeEvidenceStatus.PARTIAL
        ),

        execution_authority=False,

        next_directive=(
            JudgeNextDirective.MONITOR
        ),

        model_run_ref=(
            "B4_TTL_REEVALUATION_JUDGE_J1_V01"
        ),
    )


def test_projection_changes_only_closed_b3_reopen_fields():
    source = _captured_watch()

    recovered = (
        MODULE
        .closed_b3_watch_projection(
            source
        )
    )

    before = source.model_dump(
        mode="json",
        exclude_none=False,
    )

    after = recovered.model_dump(
        mode="json",
        exclude_none=False,
    )

    changed = sorted(
        key
        for key in before
        if before[key]
        != after[key]
    )

    assert changed == [
        "research_reopen_reason_codes",
        "research_reopen_required",
    ]

    assert recovered.outcome == JudgeOutcome.WATCH

    assert (
        recovered.next_directive
        == JudgeNextDirective.MONITOR
    )

    assert (
        recovered.primary_candidate_id
        == "NVDA"
    )

    assert tuple(
        recovered.watch_candidate_ids
    ) == (
        "NVDA",
        "MSFT",
        "META",
    )

    assert (
        recovered.blocking_reason_codes
        == source.blocking_reason_codes
    )

    assert (
        recovered
        .research_reopen_required
        is False
    )

    assert (
        recovered
        .research_reopen_reason_codes
        == ()
    )


def test_projection_rejects_already_non_reopen_watch():
    source = (
        _captured_watch()
        .model_copy(
            update={
                "research_reopen_required":
                    False,

                "research_reopen_reason_codes":
                    (),
            }
        )
    )

    with pytest.raises(
        MODULE
        .TtlCapturedWatchRecoveryError
    ):
        MODULE.closed_b3_watch_projection(
            source
        )


def test_projection_rejects_reason_code_drift():
    source = (
        _captured_watch()
        .model_copy(
            update={
                "research_reopen_reason_codes":
                    (
                        "VALUATION_EVIDENCE_NOT_SUPPLIED",
                        "ALPACA_NEWS_PAGINATION_INCOMPLETE",
                    ),
            }
        )
    )

    with pytest.raises(
        MODULE
        .TtlCapturedWatchRecoveryError
    ):
        MODULE.closed_b3_watch_projection(
            source
        )


def test_recovery_script_has_no_paid_transport_surface():
    source = MODULE_PATH.read_text(
        encoding="utf-8"
    )

    for forbidden in (
        "StdlibResponsesTransport",
        "load_openai_api_key",
        "_real_transport_factory(",
        "_execute_once(",
        "--execute-paid-judge",
    ):
        assert forbidden not in source


def test_recovery_paths_are_request_hash_scoped():
    paths = MODULE._paths(
        Path("/tmp/repo")
    )

    suffix = MODULE.SOURCE_REQUEST_HASH

    assert (
        suffix
        in paths[
            "recovered_result"
        ].name
    )

    assert (
        suffix
        in paths[
            "recovery_receipt"
        ].name
    )
