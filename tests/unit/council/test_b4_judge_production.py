from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aic.council.judge_model_selection_v01 import (
    EXPECTED_JUDGE_EVAL_ARTIFACT_HASH,
    EXPECTED_JUDGE_EVAL_RECEIPT_MANIFEST_HASH,
    EXPECTED_SELECTED_JUDGE,
    JUDGE_SELECTED_MODEL_AUTHORITY_STATUS,
    JUDGE_SELECTED_MODEL_AUTHORITY_VERSION,
    JUDGE_SELECTED_MODEL_REPLAY_VERSION,
    verify_judge_selected_model_authority,
)
from aic.council.judge_production import (
    EXPECTED_REQUIRED_UNKNOWN_REFS,
    JudgeProductionContext,
    JudgeProductionError,
    build_judge_production_request,
    build_research_reopen_request,
    validate_production_judge_proposal,
)
from aic.council.judge_production_preflight import (
    build_judge_production_cost_preflight,
    build_judge_production_request_preflight,
    verify_judge_production_cost_preflight,
    verify_judge_production_request_preflight,
)
from aic.council.proposal import (
    DecisionChangeConditionDraft,
    JudgeDecisionProposalDraft,
    JudgeEvidenceStatus,
    JudgeNextDirective,
    JudgeOutcome,
)
from aic.domain.canonical import canonical_sha256


def _selection() -> dict:
    payload = {
        "artifact_version": JUDGE_SELECTED_MODEL_AUTHORITY_VERSION,
        "replay_contract_version": JUDGE_SELECTED_MODEL_REPLAY_VERSION,
        "status": JUDGE_SELECTED_MODEL_AUTHORITY_STATUS,
        "stage": "JUDGE",
        "model_policy_version": "MODEL_POLICY_vB4_0_1",
        "source_model_eval_artifact_hash": EXPECTED_JUDGE_EVAL_ARTIFACT_HASH,
        "source_model_eval_run_id": "AIC-B4-JUDGE-EVAL-20260830T132815326431Z-4a1caeb8a184",
        "source_git_commit_sha": "372141b928ad126e3989d3df2ccfa1d48392952b",
        "source_paid_authorization_artifact_hash": "75b95f381471cdf50d638382b2d4f119b21cce3f7c4becbb3b976eaba8dcbabd",
        "source_receipt_manifest_hash": EXPECTED_JUDGE_EVAL_RECEIPT_MANIFEST_HASH,
        "semantic_replay_receipt_count": 21,
        "semantic_replay_passed_cases": 21,
        "replayed_result_hash_count": 21,
        "actual_eval_cost_usd": "0.4492515",
        "candidate_summaries": [],
        "selected_candidate": dict(EXPECTED_SELECTED_JUDGE),
        "selection_reason_code": "LOWEST_COST_PASSING_CONFIG_THEN_LATENCY_THEN_TOKENS",
        "selection_rule": "LOWEST_COST_PASSING_CONFIG_THEN_LATENCY_THEN_TOKENS",
        "production_judge_authorized": False,
        "rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def _context() -> JudgeProductionContext:
    base = {
        "production_context_version": "B4_JUDGE_PRODUCTION_CONTEXT_v0_1",
        "candidate_order": ["NVDA", "MSFT", "META"],
        "candidate_packets": [],
        "computed_values": [],
        "mandate_version": "ALPACA_COMPETITION_V1_2026_08_29",
        "deep_comparison_id": "DEEP_TEST",
        "council_policy_version": "COUNCIL_POLICY_vB4_0_1",
        "judge_policy_version": "JUDGE_POLICY_vB4_0_1",
        "model_policy_version": "MODEL_POLICY_vB4_0_1",
        "material_claims": [],
        "initial_role_views": [],
        "rebuttal_bundles": [],
        "material_conflict_refs": [],
        "material_unknown_refs": list(EXPECTED_REQUIRED_UNKNOWN_REFS),
        "unresolved_dispute_refs": [],
        "frozen_authorities": {},
        "current_event_constraints": {
            "research_reopen_required_candidates": ["NVDA", "MSFT", "META"],
            "required_unknown_refs": list(EXPECTED_REQUIRED_UNKNOWN_REFS),
            "invest_persistence_allowed": False,
            "allowed_outcomes": ["WATCH", "ABSTAIN"],
            "required_next_directive": "RESEARCH_REOPEN_REQUEST",
            "new_research_inside_b4_allowed": False,
            "b3_reopen_is_separate_lifecycle": True,
        },
        "judge_policy_surface": {
            "majority_vote_rule": "FORBIDDEN",
            "red_team_directional_vote": False,
            "blocking_conflict_allows_invest": False,
            "blocking_unknown_allows_invest": False,
            "research_reopen_allows_invest": False,
            "watch_allowed": True,
            "abstain_allowed": True,
            "execution_authority": False,
        },
    }
    judge_input_hash = canonical_sha256(base)
    model_input = {**base, "judge_input_hash": judge_input_hash}
    return JudgeProductionContext(
        candidate_ids=("NVDA", "MSFT", "META"),
        mandate_version="ALPACA_COMPETITION_V1_2026_08_29",
        deep_comparison_id="DEEP_TEST",
        judge_input_hash=judge_input_hash,
        model_input=model_input,
        allowed_claim_ids=(),
        allowed_dispute_refs=(),
        allowed_conflict_refs=(),
        allowed_unknown_refs=EXPECTED_REQUIRED_UNKNOWN_REFS,
        allowed_condition_refs=EXPECTED_REQUIRED_UNKNOWN_REFS,
        context_hash=canonical_sha256(model_input),
    )


def _watch(context: JudgeProductionContext) -> JudgeDecisionProposalDraft:
    gap = EXPECTED_REQUIRED_UNKNOWN_REFS[0]
    return JudgeDecisionProposalDraft(
        b4_decision_id="B4_TEST_JUDGE_DECISION",
        outcome=JudgeOutcome.WATCH,
        primary_candidate_id=None,
        watch_candidate_ids=("NVDA", "MSFT", "META"),
        mandate_version=context.mandate_version,
        deep_comparison_id=context.deep_comparison_id,
        judge_input_hash=context.judge_input_hash,
        council_policy_version="COUNCIL_POLICY_vB4_0_1",
        judge_policy_version="JUDGE_POLICY_vB4_0_1",
        model_policy_version="MODEL_POLICY_vB4_0_1",
        selected_candidate_basis_claim_ids=(),
        why_not_other_candidates=(),
        unresolved_dispute_refs=(),
        material_conflict_refs=(),
        material_unknown_refs=(gap,),
        blocking_reason_codes=("MATERIAL_RESEARCH_GAP",),
        research_reopen_required=True,
        research_reopen_reason_codes=("MATERIAL_RESEARCH_GAP",),
        what_would_change_decision=(
            DecisionChangeConditionDraft(
                condition_id="COND_1",
                condition_text="Resolve the frozen recent-news coverage gap.",
                source_or_claim_refs=(gap,),
            ),
        ),
        invalidation_condition_refs=(),
        evidence_status=JudgeEvidenceStatus.INSUFFICIENT,
        execution_authority=False,
        next_directive=JudgeNextDirective.RESEARCH_REOPEN_REQUEST,
        model_run_ref="B4_PRODUCTION_JUDGE_J1",
    )


def _find_root_schema(schema: dict) -> dict:
    matches = []

    def walk(value):
        if isinstance(value, dict):
            props = value.get("properties")
            if isinstance(props, dict) and {
                "outcome",
                "primary_candidate_id",
                "research_reopen_required",
                "next_directive",
            }.issubset(props):
                matches.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)
    assert len(matches) == 1
    return matches[0]


def test_selected_model_authority_is_zero_call_and_frozen_to_j1() -> None:
    selection = _selection()
    assert verify_judge_selected_model_authority(selection) == selection["artifact_hash"]
    assert selection["selected_candidate"] == EXPECTED_SELECTED_JUDGE
    assert selection["model_calls"] == 0
    assert selection["production_judge_authorized"] is False


def test_production_judge_request_schema_forbids_invest_and_requires_reopen() -> None:
    context = _context()
    request = build_judge_production_request(context, _selection())
    assert request.request_payload["model"] == "gpt-5.6-terra"
    assert request.request_payload["reasoning"] == {"effort": "medium"}
    assert request.request_payload["max_output_tokens"] == 8192
    root = _find_root_schema(request.request_payload["text"]["format"]["schema"])
    props = root["properties"]
    assert props["outcome"]["enum"] == ["WATCH", "ABSTAIN"]
    assert props["primary_candidate_id"] == {"type": "null"}
    assert props["research_reopen_required"]["const"] is True
    assert props["next_directive"]["const"] == "RESEARCH_REOPEN_REQUEST"
    assert props["material_unknown_refs"]["minItems"] == 1
    assert props["material_unknown_refs"]["maxItems"] == 1
    assert props["material_unknown_refs"]["items"]["enum"] == list(EXPECTED_REQUIRED_UNKNOWN_REFS)


def test_production_judge_validation_and_canonical_reopen_request() -> None:
    context = _context()
    proposal = _watch(context)
    validate_production_judge_proposal(proposal, context=context)
    reopen = build_research_reopen_request(
        proposal,
        parent_run_id="AIC-B4-JUDGE-PRODUCTION-TEST",
        judge_proposal_hash=canonical_sha256(proposal),
        requested_at=datetime(2026, 8, 30, 13, 30, tzinfo=UTC),
    )
    assert reopen.new_run_start_state == "S00"
    assert reopen.parent_decision_id is None
    assert reopen.trigger_bundle_id is None
    assert EXPECTED_REQUIRED_UNKNOWN_REFS[0] in reopen.source_ref_ids
    assert reopen.request_hash == canonical_sha256(
        reopen,
        exclude_fields=("request_hash",),
    )


def test_production_judge_validation_rejects_reopen_suppression() -> None:
    context = _context()
    raw = _watch(context).model_dump(mode="json", exclude_none=False)
    raw["research_reopen_required"] = False
    raw["research_reopen_reason_codes"] = []
    raw["next_directive"] = "MONITOR"
    proposal = JudgeDecisionProposalDraft.model_validate(raw)
    with pytest.raises(JudgeProductionError):
        validate_production_judge_proposal(proposal, context=context)


def test_production_request_and_cost_preflight_remain_zero_call_and_owner_gated() -> None:
    selection = _selection()
    context = _context()
    request = build_judge_production_request_preflight(
        code_commit_sha="a" * 40,
        context=context,
        selected_model_authority=selection,
    )
    assert verify_judge_production_request_preflight(request) == request["artifact_hash"]
    assert request["planned_paid_calls_max"] == 1
    assert request["model_calls"] == 0
    assert request["production_judge_authorized"] is False
    cost = build_judge_production_cost_preflight(request)
    assert verify_judge_production_cost_preflight(cost) == cost["artifact_hash"]
    assert cost["planned_paid_calls_max"] == 1
    assert cost["owner_cost_approval_required"] is True
    assert cost["model_calls"] == 0
    assert cost["production_judge_authorized"] is False


def test_paid_runner_is_durable_before_key_and_has_no_repair_or_retry_surface() -> None:
    text = Path("scripts/b4_run_judge_production_v01.py").read_text(encoding="utf-8")
    assert text.index("_write_durable_new(args.authorization_output, authorization)") < text.index("load_openai_api_key()")
    assert '"consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT"' in text
    assert '"automatic_repair_calls_authorized": False' in text
    assert '"automatic_repair_attempted": False' in text
    assert '"planned_paid_calls_max": 1' in text
    assert "BLOCKED_UNKNOWN_PROVIDER_DISPATCH" in text
    assert "BLOCKED_INCOMPLETE_COST_RECEIPT" in text
    assert "BLOCKED_JUDGE_VALIDATION_FAILED" in text
    assert '"rerun_authorized": False' in text
    assert '"broker_writes": 0' in text
    assert '"alpaca_orders": 0' in text
    assert '"live_money": "PROHIBITED"' in text
