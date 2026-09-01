from __future__ import annotations

import inspect

import pytest

from aic.council import post_research_reopen_judge_current_v03 as v03
from aic.council import post_research_reopen_judge_current_v04 as judge
from aic.council.proposal import (
    DecisionChangeConditionDraft,
    JudgeDecisionProposalDraft,
    JudgeEvidenceStatus,
    JudgeNextDirective,
    JudgeOutcome,
    WhyNotCandidate,
)
from aic.domain.canonical import canonical_sha256


CODE = "a" * 40
CANDIDATES = ["NVDA", "MSFT", "META"]


def claim(candidate: str, *, support: str = "SUPPORTED", conflicts=None) -> dict:
    return {
        "claim_id": f"{candidate}_BASIS",
        "candidate_id": candidate,
        "materiality": "MATERIAL",
        "support_status": support,
        "conflict_ids": list(conflicts or []),
        "evidence_ids": [f"EVIDENCE_{candidate}"],
        "computed_value_ids": [],
    }


def source_entry() -> dict:
    value = {
        "artifact_version": "TEST_SOURCE_V03",
        "canonical_open_research_requirements_after_b3": [],
        "additional_provider_read_required": False,
        "candidate_aware_reopen_provenance": "PASS",
        "council_policy_version": "COUNCIL_POLICY_vB4_0_1",
        "judge_policy_version": "JUDGE_POLICY_vB4_0_1",
        "model_policy_version": "MODEL_POLICY_vB4_0_1",
    }
    value["artifact_hash"] = canonical_sha256(
        value, exclude_fields=("artifact_hash",)
    )
    return value


def source_context(
    *,
    conflict_a: bool = False,
    insufficient_all: bool = False,
    uncertainties: list[dict] | None = None,
):
    claims = []
    for candidate in CANDIDATES:
        support = "INSUFFICIENT" if insufficient_all else "SUPPORTED"
        conflicts = ["NVDA_CONFLICT"] if conflict_a and candidate == "NVDA" else []
        if conflicts:
            support = "CONFLICTED"
        claims.append(claim(candidate, support=support, conflicts=conflicts))

    base = {
        "context_version": v03.CONTEXT_VERSION,
        "candidate_order": list(CANDIDATES),
        "candidate_packets": [{"candidate_id": x} for x in CANDIDATES],
        "computed_values": [{"computed_value_id": "CV1"}],
        "mandate_version": "ALPACA_COMPETITION_V1_2026_08_29",
        "deep_comparison_id": "TEST_DEEP",
        "council_policy_version": "COUNCIL_POLICY_vB4_0_1",
        "judge_policy_version": "JUDGE_POLICY_vB4_0_1",
        "model_policy_version": "MODEL_POLICY_vB4_0_1",
        "material_claims": claims,
        "initial_role_views": [
            {"candidate_id": candidate, "lane": lane}
            for candidate in CANDIDATES
            for lane in ("BULL", "BEAR", "RED_TEAM")
        ],
        "rebuttal_bundles": [
            {
                "candidate_id": x,
                "items": [],
                "research_reopen_required": False,
                "research_reopen_reason_codes": [],
            }
            for x in CANDIDATES
        ],
        "decision_context_uncertainties": list(uncertainties or []),
        "material_conflict_refs": ["NVDA_CONFLICT"] if conflict_a else [],
        "unresolved_dispute_refs": [],
        "event_outcome_constraints": {
            "canonical_b3_reopen_closed": True,
            "allowed_outcomes": ["WATCH", "ABSTAIN"],
        },
        "source_lineage": {},
    }
    uncertainty_refs = tuple(
        row["uncertainty_ref"] for row in base["decision_context_uncertainties"]
    )
    for row in base["decision_context_uncertainties"]:
        row.setdefault("decision_context_condition_ids", [])
    judge_hash = canonical_sha256(base)
    model_input = {**base, "judge_input_hash": judge_hash}
    return v03.JudgeContext(
        model_input=model_input,
        judge_input_hash=judge_hash,
        context_hash=canonical_sha256(model_input),
        mandate_version=base["mandate_version"],
        deep_comparison_id=base["deep_comparison_id"],
        allowed_claim_ids=tuple(row["claim_id"] for row in claims),
        allowed_dispute_refs=(),
        allowed_conflict_refs=("NVDA_CONFLICT",) if conflict_a else (),
        allowed_unknown_refs=uncertainty_refs,
        allowed_condition_refs=tuple(
            [row["claim_id"] for row in claims] + list(uncertainty_refs)
        ),
    )


def build(*, conflict_a=False, insufficient_all=False, uncertainties=None):
    src_entry = source_entry()
    src_context = source_context(
        conflict_a=conflict_a,
        insufficient_all=insufficient_all,
        uncertainties=uncertainties,
    )
    gate = judge.build_gate(
        source_entry=src_entry,
        source_context=src_context,
    )
    entry = judge.build_entry(
        code_commit_sha=CODE,
        source_entry=src_entry,
        source_context=src_context,
        gate=gate,
    )
    context = judge.build_context(
        entry=entry,
        source_entry=src_entry,
        source_context=src_context,
        gate=gate,
    )
    return src_entry, src_context, gate, entry, context


def proposal(
    *,
    outcome: JudgeOutcome,
    context,
    primary: str | None,
    basis: tuple[str, ...],
):
    other_ids = [candidate for candidate in CANDIDATES if candidate != primary]
    why_not = tuple(
        WhyNotCandidate(
            candidate_id=candidate,
            claim_ids=(f"{candidate}_BASIS",),
            reason_codes=("NOT_SELECTED",),
        )
        for candidate in other_ids
    )
    what_would_change = (
        (
            DecisionChangeConditionDraft(
                condition_id="COND1",
                condition_text="Material evidence changes.",
                source_or_claim_refs=("NVDA_BASIS",),
            ),
        )
        if outcome == JudgeOutcome.WATCH
        else ()
    )
    next_directive = {
        JudgeOutcome.INVEST: JudgeNextDirective.PROMOTE_FINAL_DECISION,
        JudgeOutcome.WATCH: JudgeNextDirective.MONITOR,
        JudgeOutcome.ABSTAIN: JudgeNextDirective.STOP,
    }[outcome]
    return JudgeDecisionProposalDraft(
        b4_decision_id="TEST_DECISION",
        outcome=outcome,
        primary_candidate_id=primary,
        watch_candidate_ids=(primary,) if outcome == JudgeOutcome.WATCH and primary else (),
        mandate_version=context.mandate_version,
        deep_comparison_id=context.deep_comparison_id,
        judge_input_hash=context.judge_input_hash,
        council_policy_version="COUNCIL_POLICY_vB4_0_1",
        judge_policy_version="JUDGE_POLICY_vB4_0_1",
        model_policy_version="MODEL_POLICY_vB4_0_1",
        selected_candidate_basis_claim_ids=basis,
        why_not_other_candidates=why_not,
        unresolved_dispute_refs=(),
        material_conflict_refs=(),
        material_unknown_refs=(),
        blocking_reason_codes=(),
        research_reopen_required=False,
        research_reopen_reason_codes=(),
        what_would_change_decision=what_would_change,
        invalidation_condition_refs=(),
        evidence_status=JudgeEvidenceStatus.COMPLETE,
        execution_authority=False,
        next_directive=next_directive,
        model_run_ref=judge.MODEL_RUN_REF,
    )


def test_v04_opens_invest_only_after_positive_gate_and_keeps_b4_authority_bounded():
    _, _, gate, entry, context = build()
    assert gate["invest_eligible_candidates"] == CANDIDATES
    assert entry["allowed_judge_outcomes"] == ["INVEST", "WATCH", "ABSTAIN"]
    assert entry["risk_authority"] is False
    assert entry["approval_authority"] is False
    assert entry["execution_authority"] is False
    assert entry["option_contract_authority"] is False
    assert context.model_input["event_outcome_constraints"]["invest_outcome_authorized"] is True


def test_invest_accepts_only_gate_eligible_primary_and_gate_approved_basis():
    _, _, gate, _, context = build()
    good = proposal(
        outcome=JudgeOutcome.INVEST,
        context=context,
        primary="NVDA",
        basis=("NVDA_BASIS",),
    )
    judge.validate_proposal(good, context=context, gate=gate)

    wrong_basis = proposal(
        outcome=JudgeOutcome.INVEST,
        context=context,
        primary="NVDA",
        basis=("MSFT_BASIS",),
    )
    with pytest.raises(Exception, match="gate-approved"):
        judge.validate_proposal(wrong_basis, context=context, gate=gate)


def test_invest_accepts_visible_closed_primary_uncertainty_and_schema_validator_parity():
    uncertainty = {
        "candidate_id": "NVDA",
        "uncertainty_ref": "NVDA:CLOSED_CONTEXT",
        "raw_reason_or_ref": "CLOSED_CONTEXT",
        "global_reason_closed": True,
        "may_independently_force_new_research_reopen": False,
    }
    _, _, gate, _, context = build(uncertainties=[uncertainty])
    assert gate["candidate_results"][0]["status"] == "INVEST_ELIGIBLE"
    captured = proposal(
        outcome=JudgeOutcome.INVEST,
        context=context,
        primary="NVDA",
        basis=("NVDA_BASIS",),
    ).model_copy(update={"material_unknown_refs": ("NVDA:CLOSED_CONTEXT",)})
    # DTO construction is the schema contract; validator acceptance prevents
    # paid calls from discovering a schema-to-validator mismatch.
    assert captured.material_unknown_refs == ("NVDA:CLOSED_CONTEXT",)
    judge.validate_proposal(captured, context=context, gate=gate)


def test_invest_accepts_visible_uncertainty_for_another_candidate():
    uncertainty = {
        "candidate_id": "MSFT",
        "uncertainty_ref": "MSFT:CLOSED_CONTEXT",
        "raw_reason_or_ref": "CLOSED_CONTEXT",
        "global_reason_closed": True,
        "may_independently_force_new_research_reopen": False,
    }
    _, _, gate, _, context = build(uncertainties=[uncertainty])
    captured = proposal(
        outcome=JudgeOutcome.INVEST,
        context=context,
        primary="NVDA",
        basis=("NVDA_BASIS",),
    ).model_copy(update={"material_unknown_refs": ("MSFT:CLOSED_CONTEXT",)})
    judge.validate_proposal(captured, context=context, gate=gate)


def test_invest_keeps_blocking_reason_and_canonical_reference_guards():
    _, _, gate, _, context = build()
    good = proposal(
        outcome=JudgeOutcome.INVEST,
        context=context,
        primary="NVDA",
        basis=("NVDA_BASIS",),
    )
    with pytest.raises(Exception, match="blocking reasons"):
        judge.validate_proposal(
            good.model_copy(update={"blocking_reason_codes": ("BLOCKING",)}),
            context=context,
            gate=gate,
        )
    with pytest.raises(Exception, match="outside canonical graph"):
        judge.validate_proposal(
            good.model_copy(update={"material_unknown_refs": ("OUTSIDE",)}),
            context=context,
            gate=gate,
        )


def test_blocked_candidate_cannot_be_selected_even_if_other_candidates_are_eligible():
    _, _, gate, _, context = build(conflict_a=True)
    assert gate["invest_eligible_candidates"] == ["MSFT", "META"]
    bad = proposal(
        outcome=JudgeOutcome.INVEST,
        context=context,
        primary="NVDA",
        basis=("NVDA_BASIS",),
    )
    with pytest.raises(Exception, match="not gate-eligible"):
        judge.validate_proposal(bad, context=context, gate=gate)


def test_open_primary_unknown_blocks_invest_through_the_positive_gate():
    uncertainty = {
        "candidate_id": "NVDA",
        "uncertainty_ref": "NVDA:OPEN_CONTEXT",
        "raw_reason_or_ref": "OPEN_CONTEXT",
        "global_reason_closed": False,
        "may_independently_force_new_research_reopen": True,
    }
    _, _, gate, _, context = build(uncertainties=[uncertainty])
    assert gate["candidate_results"][0]["block_reason_codes"] == [
        "BLOCKING_OPEN_MATERIAL_UNKNOWN"
    ]
    blocked = proposal(
        outcome=JudgeOutcome.INVEST,
        context=context,
        primary="NVDA",
        basis=("NVDA_BASIS",),
    )
    with pytest.raises(Exception, match="not gate-eligible"):
        judge.validate_proposal(blocked, context=context, gate=gate)


def test_no_eligible_candidates_removes_invest_from_judge_surface():
    _, _, gate, entry, context = build(insufficient_all=True)
    assert gate["invest_eligible_candidates"] == []
    assert entry["allowed_judge_outcomes"] == ["WATCH", "ABSTAIN"]
    bad = proposal(
        outcome=JudgeOutcome.INVEST,
        context=context,
        primary="NVDA",
        basis=("NVDA_BASIS",),
    )
    with pytest.raises(Exception, match="outside v0.4 allowed surface"):
        judge.validate_proposal(bad, context=context, gate=gate)


def test_watch_and_abstain_remain_valid_even_when_invest_is_available():
    _, _, gate, _, context = build()
    watch = proposal(
        outcome=JudgeOutcome.WATCH,
        context=context,
        primary="NVDA",
        basis=("NVDA_BASIS",),
    )
    judge.validate_proposal(watch, context=context, gate=gate)

    abstain = proposal(
        outcome=JudgeOutcome.ABSTAIN,
        context=context,
        primary=None,
        basis=(),
    )
    judge.validate_proposal(abstain, context=context, gate=gate)


def test_historical_v03_module_still_rejects_invest():
    _, src_context, _, _, _ = build()
    invest = proposal(
        outcome=JudgeOutcome.INVEST,
        context=src_context,
        primary="NVDA",
        basis=("NVDA_BASIS",),
    )
    invest = invest.model_copy(update={"model_run_ref": v03.MODEL_RUN_REF})
    with pytest.raises(Exception, match="rejects INVEST"):
        v03._validate_proposal(invest, context=src_context)


def test_v04_validator_has_no_candidate_or_symbol_tuning():
    implementation = inspect.getsource(judge.validate_proposal)
    assert "NVDA" not in implementation
    assert "MSFT" not in implementation
    assert "META" not in implementation
