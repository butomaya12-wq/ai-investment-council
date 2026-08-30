from __future__ import annotations

from copy import deepcopy

import pytest

from aic.council.judge_eval_preflight import (
    EXPECTED_JUDGE_ENTRY_HASH,
    EXPECTED_JUDGE_EVAL_CASE_IDS,
    EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX,
    JudgeEvalPreflightError,
    build_judge_eval_cases,
    build_judge_eval_cost_preflight,
    build_judge_eval_dry,
    build_judge_eval_request_preflight,
    score_judge_eval_case,
    verify_judge_eval_cost_preflight,
    verify_judge_eval_dry,
    verify_judge_eval_request_preflight,
)
from aic.council.proposal import (
    DecisionChangeConditionDraft,
    JudgeDecisionProposalDraft,
    JudgeEvidenceStatus,
    JudgeNextDirective,
    JudgeOutcome,
    WhyNotCandidate,
)
from aic.domain.canonical import canonical_sha256


def _entry() -> dict:
    return {
        "allowed_judge_outcomes_for_current_frozen_run": [
            "WATCH",
            "ABSTAIN",
        ],
        "alpaca_orders": 0,
        "artifact_hash": EXPECTED_JUDGE_ENTRY_HASH,
        "artifact_version": "B4_JUDGE_ENTRY_PREFLIGHT_v0_1",
        "b3_reopen_is_separate_lifecycle": True,
        "broker_writes": 0,
        "candidate_order": ["NVDA", "MSFT", "META"],
        "code_commit_sha": "1250ee490170db20c25a2d4e01d98ab64c2ee1c7",
        "invest_block_reason": (
            "RESEARCH_REOPEN_REQUIRED_FOR_ALL_THREE_FROZEN_CANDIDATES"
        ),
        "invest_eligible_candidates": [],
        "invest_persistence_allowed": False,
        "judge_authorized": False,
        "judge_entry_barrier_satisfied": True,
        "judge_execution_authorized": False,
        "judge_model_selection_required": True,
        "live_money": "PROHIBITED",
        "model_calls": 0,
        "new_research_inside_b4_allowed": False,
        "paid_rebuttal_authorization_artifact_hash": (
            "1ddaa743678ebc3aae7c7050e84566f627f720f7ffa350d365e48f063b535443"
        ),
        "provider_reads": 0,
        "rebuttal_bundle_count": 3,
        "rebuttal_bundle_hashes": [
            "8824f4eeb792407a427657b9116c70a5a2557fd0958241b26f26854bd0361763",
            "e9ff46cb1e38db6ed525d34677a8af20048206fb1cc8f1a652b08815908fffb8",
            "dd400c55953a4c494611e5ab5f27c28a71bef1ab10a0774901083cb9914282a8",
        ],
        "rebuttal_council_freeze_artifact_hash": (
            "dd83aa4f873c0d6ac6582cd6dc89c1612088239aa8e979d18f7f95c3386552a5"
        ),
        "rebuttal_receipt_manifest_hash": (
            "c36cb817bf0e61020a0781cd7a6dc30c5432acaaa2184f93abb8e4f1565270d3"
        ),
        "rebuttal_run_id": (
            "AIC-B4-REBUTTAL-RUNTIME-20260830T122106121542Z-b5dba042bc75"
        ),
        "rerun_authorized": False,
        "research_reopen_must_remain_visible_to_judge": True,
        "research_reopen_required_candidates": ["NVDA", "MSFT", "META"],
        "status": "PASS_ZERO_CALL_JUDGE_ENTRY_RESEARCH_REOPEN_BOUND",
    }


def _change_condition(case, ref: str) -> tuple[DecisionChangeConditionDraft, ...]:
    return (
        DecisionChangeConditionDraft(
            condition_id=f"{case.case_id}_COND_1",
            condition_text="Resolve the cited frozen evidence condition before reconsideration.",
            source_or_claim_refs=(ref,),
        ),
    )


def _golden(case) -> JudgeDecisionProposalDraft:
    primary = case.required_primary_candidate_id
    if case.expected_outcomes == (JudgeOutcome.INVEST,):
        assert primary is not None
        basis = case.required_basis_claim_id
        assert basis is not None
        why_not = tuple(
            WhyNotCandidate(
                candidate_id=candidate_id,
                claim_ids=(
                    next(
                        claim_id
                        for claim_id in case.allowed_claim_ids
                        if candidate_id.split("_CAND_")[0]
                        in claim_id
                        or claim_id.startswith(case.case_id)
                    ),
                ),
                reason_codes=("WEAKER_THAN_SELECTED_BASIS",),
            )
            for candidate_id in case.required_why_not_candidate_ids
        )
        return JudgeDecisionProposalDraft(
            b4_decision_id=f"{case.case_id}_DECISION",
            outcome=JudgeOutcome.INVEST,
            primary_candidate_id=primary,
            watch_candidate_ids=(),
            mandate_version=case.mandate_version,
            deep_comparison_id=case.deep_comparison_id,
            judge_input_hash=case.judge_input_hash,
            council_policy_version="COUNCIL_POLICY_vB4_0_1",
            judge_policy_version="JUDGE_POLICY_vB4_0_1",
            model_policy_version="MODEL_POLICY_vB4_0_1",
            selected_candidate_basis_claim_ids=(basis,),
            why_not_other_candidates=why_not,
            unresolved_dispute_refs=(),
            material_conflict_refs=(),
            material_unknown_refs=(),
            blocking_reason_codes=(),
            research_reopen_required=False,
            research_reopen_reason_codes=(),
            what_would_change_decision=(),
            invalidation_condition_refs=(),
            evidence_status=JudgeEvidenceStatus.COMPLETE,
            execution_authority=False,
            next_directive=JudgeNextDirective.PROMOTE_FINAL_DECISION,
            model_run_ref=f"TEST_{case.case_id}",
        )

    if case.expected_outcomes == (JudgeOutcome.ABSTAIN,):
        return JudgeDecisionProposalDraft(
            b4_decision_id=f"{case.case_id}_DECISION",
            outcome=JudgeOutcome.ABSTAIN,
            primary_candidate_id=None,
            watch_candidate_ids=(),
            mandate_version=case.mandate_version,
            deep_comparison_id=case.deep_comparison_id,
            judge_input_hash=case.judge_input_hash,
            council_policy_version="COUNCIL_POLICY_vB4_0_1",
            judge_policy_version="JUDGE_POLICY_vB4_0_1",
            model_policy_version="MODEL_POLICY_vB4_0_1",
            selected_candidate_basis_claim_ids=(),
            why_not_other_candidates=(),
            unresolved_dispute_refs=(),
            material_conflict_refs=(),
            material_unknown_refs=(),
            blocking_reason_codes=("NO_CANDIDATE_MEETS_EVIDENCE_BAR",),
            research_reopen_required=False,
            research_reopen_reason_codes=(),
            what_would_change_decision=(),
            invalidation_condition_refs=(),
            evidence_status=JudgeEvidenceStatus.COMPLETE,
            execution_authority=False,
            next_directive=JudgeNextDirective.STOP,
            model_run_ref=f"TEST_{case.case_id}",
        )

    ref = (
        case.required_unknown_ref
        or case.required_conflict_ref
        or case.allowed_claim_ids[0]
    )
    return JudgeDecisionProposalDraft(
        b4_decision_id=f"{case.case_id}_DECISION",
        outcome=JudgeOutcome.WATCH,
        primary_candidate_id=None,
        watch_candidate_ids=case.candidate_ids,
        mandate_version=case.mandate_version,
        deep_comparison_id=case.deep_comparison_id,
        judge_input_hash=case.judge_input_hash,
        council_policy_version="COUNCIL_POLICY_vB4_0_1",
        judge_policy_version="JUDGE_POLICY_vB4_0_1",
        model_policy_version="MODEL_POLICY_vB4_0_1",
        selected_candidate_basis_claim_ids=(),
        why_not_other_candidates=(),
        unresolved_dispute_refs=(),
        material_conflict_refs=(
            ()
            if case.required_conflict_ref is None
            else (case.required_conflict_ref,)
        ),
        material_unknown_refs=(
            ()
            if case.required_unknown_ref is None
            else (case.required_unknown_ref,)
        ),
        blocking_reason_codes=("FROZEN_BLOCKER_REMAINS",),
        research_reopen_required=case.require_research_reopen,
        research_reopen_reason_codes=(
            ("MATERIAL_RESEARCH_GAP",)
            if case.require_research_reopen
            else ()
        ),
        what_would_change_decision=_change_condition(case, ref),
        invalidation_condition_refs=(),
        evidence_status=(
            JudgeEvidenceStatus.INSUFFICIENT
            if case.require_research_reopen
            else JudgeEvidenceStatus.PARTIAL
        ),
        execution_authority=False,
        next_directive=(
            JudgeNextDirective.RESEARCH_REOPEN_REQUEST
            if case.require_research_reopen
            else JudgeNextDirective.MONITOR
        ),
        model_run_ref=f"TEST_{case.case_id}",
    )


def test_real_judge_entry_fixture_self_hash_matches_pasted_event_authority() -> None:
    entry = _entry()
    assert canonical_sha256(entry, exclude_fields=("artifact_hash",)) == (
        EXPECTED_JUDGE_ENTRY_HASH
    )


def test_judge_eval_cases_match_frozen_plan_and_golden_outputs_score_pass() -> None:
    cases = build_judge_eval_cases()
    assert tuple(case.case_id for case in cases) == EXPECTED_JUDGE_EVAL_CASE_IDS
    for case in cases:
        passed, findings = score_judge_eval_case(_golden(case), case=case)
        assert passed is True, (case.case_id, findings)
        assert findings == ()


def test_judge_eval_request_cost_and_dry_are_zero_call_and_cover_exact_21() -> None:
    request = build_judge_eval_request_preflight(
        code_commit_sha="a" * 40,
        entry_preflight=_entry(),
    )
    request_hash = verify_judge_eval_request_preflight(request)
    assert request_hash == request["artifact_hash"]
    assert request["candidate_keys"] == ["J1", "J2", "J3"]
    assert request["case_ids"] == list(EXPECTED_JUDGE_EVAL_CASE_IDS)
    assert request["planned_paid_calls_max"] == EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX
    assert len(request["request_variants"]) == 21
    assert request["max_output_tokens_per_call"] == 8192
    assert request["model_calls"] == 0
    assert request["provider_reads"] == 0
    assert request["paid_eval_authorized"] is False
    assert request["production_judge_authorized"] is False

    cost = build_judge_eval_cost_preflight(request)
    cost_hash = verify_judge_eval_cost_preflight(cost)
    assert cost_hash == cost["artifact_hash"]
    assert cost["planned_paid_calls_max"] == 21
    assert cost["owner_cost_approval_required"] is True
    assert cost["cache_write_input_rate_multiplier"] == "1.25"
    assert cost["model_calls"] == 0

    dry = build_judge_eval_dry(
        request_preflight=request,
        cost_preflight=cost,
    )
    dry_hash = verify_judge_eval_dry(dry)
    assert dry_hash == dry["artifact_hash"]
    assert dry["planned_paid_calls_max"] == 21
    assert dry["owner_approval_required"] is True
    assert dry["paid_eval_authorized"] is False
    assert dry["production_judge_authorized"] is False
    assert dry["model_calls"] == 0
    assert dry["provider_reads"] == 0


def test_judge_eval_request_preflight_rejects_tampered_entry_authority() -> None:
    entry = _entry()
    entry["research_reopen_required_candidates"] = ["NVDA", "MSFT"]
    entry["artifact_hash"] = canonical_sha256(
        entry,
        exclude_fields=("artifact_hash",),
    )
    with pytest.raises(Exception):
        build_judge_eval_request_preflight(
            code_commit_sha="a" * 40,
            entry_preflight=entry,
        )


def test_judge_eval_dry_rejects_request_manifest_tamper() -> None:
    request = build_judge_eval_request_preflight(
        code_commit_sha="a" * 40,
        entry_preflight=_entry(),
    )
    cost = build_judge_eval_cost_preflight(request)
    mutated = deepcopy(request)
    mutated["request_variants"][0]["request_hash"] = "0" * 64
    mutated["artifact_hash"] = canonical_sha256(
        mutated,
        exclude_fields=("artifact_hash",),
    )
    with pytest.raises(JudgeEvalPreflightError):
        build_judge_eval_dry(
            request_preflight=mutated,
            cost_preflight=cost,
        )


def test_e10_majority_vote_mutant_invest_fails_scoring() -> None:
    case = next(case for case in build_judge_eval_cases() if case.case_id == "E10")
    primary = case.candidate_ids[0]
    other = case.candidate_ids[1:]
    proposal = JudgeDecisionProposalDraft(
        b4_decision_id="E10_MUTANT",
        outcome=JudgeOutcome.INVEST,
        primary_candidate_id=primary,
        watch_candidate_ids=(),
        mandate_version=case.mandate_version,
        deep_comparison_id=case.deep_comparison_id,
        judge_input_hash=case.judge_input_hash,
        council_policy_version="COUNCIL_POLICY_vB4_0_1",
        judge_policy_version="JUDGE_POLICY_vB4_0_1",
        model_policy_version="MODEL_POLICY_vB4_0_1",
        selected_candidate_basis_claim_ids=(case.allowed_claim_ids[0],),
        why_not_other_candidates=tuple(
            WhyNotCandidate(
                candidate_id=candidate_id,
                claim_ids=(case.allowed_claim_ids[index + 1],),
                reason_codes=("TWO_OF_THREE_ROLES_FAVOR_PRIMARY",),
            )
            for index, candidate_id in enumerate(other)
        ),
        unresolved_dispute_refs=(),
        material_conflict_refs=(),
        material_unknown_refs=(case.required_unknown_ref,),
        blocking_reason_codes=(),
        research_reopen_required=False,
        research_reopen_reason_codes=(),
        what_would_change_decision=(),
        invalidation_condition_refs=(),
        evidence_status=JudgeEvidenceStatus.PARTIAL,
        execution_authority=False,
        next_directive=JudgeNextDirective.PROMOTE_FINAL_DECISION,
        model_run_ref="TEST_E10_MUTANT",
    )
    passed, findings = score_judge_eval_case(proposal, case=case)
    assert passed is False
    assert "UNEXPECTED_OUTCOME" in findings


def test_watch_condition_with_forbidden_trade_authority_text_fails() -> None:
    case = next(case for case in build_judge_eval_cases() if case.case_id == "E4")
    good = _golden(case)
    raw = good.model_dump(mode="json", exclude_none=False)
    raw["what_would_change_decision"][0]["condition_text"] = (
        "Resolve the conflict and then BUY with a target price."
    )
    mutant = JudgeDecisionProposalDraft.model_validate(raw)
    passed, findings = score_judge_eval_case(mutant, case=case)
    assert passed is False
    assert "FORBIDDEN_AUTHORITY_TEXT" in findings
