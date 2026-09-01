"""B4 current-lineage Judge v0.4 with deterministic positive INVEST eligibility.

v0.4 is additive. It never mutates or reinterprets the historical v0.3 WATCH
artifact. It reuses the evidence-complete v0.3 context, evaluates the frozen
candidate-independent positive eligibility gate, and exposes INVEST to the
Judge only when at least one candidate passes that gate.

B4 still has no risk, approval, sizing, option-selection, price, or execution
authority.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from aic.domain.canonical import canonical_sha256

from .bounded_request import assert_bounded_request_invariants, build_bounded_judge_request
from .initial_runtime_cost_v02 import actual_cost_usd, runtime_cost_upper_bound_usd
from .invest_eligibility_v1 import (
    POLICY,
    POLICY_VERSION,
    evaluate_positive_invest_eligibility,
    verify_positive_invest_eligibility,
)
from .post_research_reopen_initial_execute_production_v01 import (
    _external_json_value,
    _replace_durable,
    _write_exclusive,
    external_provider_json_sha256,
)
from . import post_research_reopen_judge_current_v03 as v03
from .proposal import (
    FrozenJudgeDecisionProposal,
    JudgeDecisionProposalDraft,
    JudgeEvidenceStatus,
    JudgeNextDirective,
    JudgeOutcome,
)
from .request import parse_council_responses_payload


ENTRY_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_ENTRY_v0_4"
CONTEXT_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_CONTEXT_v0_4"
PREFLIGHT_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_PREFLIGHT_ZERO_CALL_v0_4"
READINESS_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_READINESS_ZERO_CALL_v0_4"
APPROVAL_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_OWNER_APPROVAL_v0_4"
LEDGER_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_PAID_DISPATCH_LEDGER_v0_4"
RAW_CAPTURE_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_RAW_PROVIDER_RESPONSE_v0_4"
RESULT_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_COUNCIL_FREEZE_v0_4"
MODEL_RUN_REF = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_J1_V04"


class CurrentJudgeV04Error(RuntimeError):
    pass


def _need(ok: bool, message: str) -> None:
    if not ok:
        raise CurrentJudgeV04Error(message)


def _hash(payload: Mapping[str, Any], field: str = "artifact_hash") -> str:
    value = payload.get(field)
    _need(
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        f"{field} missing",
    )
    _need(
        value == canonical_sha256(payload, exclude_fields=(field,)),
        f"{field} mismatch",
    )
    return value


def _utc(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_gate(
    *,
    source_entry: Mapping[str, Any],
    source_context: v03.JudgeContext,
) -> dict[str, Any]:
    v03.verify_context(source_context)
    return evaluate_positive_invest_eligibility(
        source_entry=source_entry,
        model_input=source_context.model_input,
        policy=POLICY,
    )


def verify_gate(
    payload: Mapping[str, Any],
    *,
    source_entry: Mapping[str, Any],
    source_context: v03.JudgeContext,
) -> str:
    return verify_positive_invest_eligibility(
        payload,
        source_entry=source_entry,
        model_input=source_context.model_input,
        policy=POLICY,
    )


def build_entry(
    *,
    code_commit_sha: str,
    source_entry: Mapping[str, Any],
    source_context: v03.JudgeContext,
    gate: Mapping[str, Any],
) -> dict[str, Any]:
    _need(
        isinstance(code_commit_sha, str)
        and re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None,
        "entry SHA invalid",
    )
    source_entry_hash = v03._hash(source_entry)
    v03.verify_context(source_context)
    gate_hash = verify_gate(
        gate,
        source_entry=source_entry,
        source_context=source_context,
    )
    eligible = list(gate["invest_eligible_candidates"])
    blocked = list(gate["invest_blocked_candidates"])
    allowed = list(gate["allowed_judge_outcomes"])
    _need(
        ("INVEST" in allowed) == bool(eligible),
        "INVEST outcome surface does not match gate",
    )
    value: dict[str, Any] = {
        "artifact_version": ENTRY_VERSION,
        "status": (
            "PASS_POSITIVE_INVEST_JUDGE_AUTHORITY"
            if eligible
            else "PASS_FAIL_CLOSED_NO_INVEST_ELIGIBLE_CANDIDATE"
        ),
        "code_commit_sha": code_commit_sha,
        "source_v03_entry_hash": source_entry_hash,
        "source_v03_context_hash": source_context.context_hash,
        "positive_invest_policy_version": POLICY_VERSION,
        "positive_invest_policy_hash": gate["policy_hash"],
        "positive_invest_evaluation_hash": gate_hash,
        "candidate_order": list(gate["candidate_order"]),
        "invest_eligible_candidates": eligible,
        "invest_blocked_candidates": blocked,
        "candidate_eligibility": list(gate["candidate_results"]),
        "allowed_judge_outcomes": allowed,
        "canonical_open_research_requirements_after_b3": list(
            source_entry.get("canonical_open_research_requirements_after_b3", [])
        ),
        "additional_provider_read_required": source_entry.get(
            "additional_provider_read_required"
        ),
        "candidate_aware_reopen_provenance": source_entry.get(
            "candidate_aware_reopen_provenance"
        ),
        "eligibility_is_necessary_not_sufficient_for_invest": True,
        "judge_retains_terminal_outcome_authority": True,
        "mandate_version": source_context.mandate_version,
        "deep_comparison_id": source_context.deep_comparison_id,
        "council_policy_version": source_entry["council_policy_version"],
        "judge_policy_version": source_entry["judge_policy_version"],
        "model_policy_version": source_entry["model_policy_version"],
        "risk_authority": False,
        "approval_authority": False,
        "execution_authority": False,
        "option_contract_authority": False,
        "quantity_authority": False,
        "price_authority": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    value["artifact_hash"] = canonical_sha256(value, exclude_fields=("artifact_hash",))
    return value


def verify_entry(
    payload: Mapping[str, Any],
    *,
    code_commit_sha: str,
    source_entry: Mapping[str, Any],
    source_context: v03.JudgeContext,
    gate: Mapping[str, Any],
) -> str:
    observed = _hash(payload)
    expected = build_entry(
        code_commit_sha=code_commit_sha,
        source_entry=source_entry,
        source_context=source_context,
        gate=gate,
    )
    _need(dict(payload) == expected, "v0.4 entry drift")
    return observed


def build_context(
    *,
    entry: Mapping[str, Any],
    source_entry: Mapping[str, Any],
    source_context: v03.JudgeContext,
    gate: Mapping[str, Any],
) -> v03.JudgeContext:
    v03.verify_context(source_context)
    verify_gate(
        gate,
        source_entry=source_entry,
        source_context=source_context,
    )
    _need(
        gate.get("artifact_hash") == entry.get("positive_invest_evaluation_hash"),
        "entry/gate hash mismatch",
    )
    _need(
        gate.get("policy_hash") == entry.get("positive_invest_policy_hash"),
        "entry/policy hash mismatch",
    )
    eligible = list(gate["invest_eligible_candidates"])
    base = dict(source_context.model_input)
    source_constraints = dict(base.get("event_outcome_constraints", {}))
    base["context_version"] = CONTEXT_VERSION
    base["event_outcome_constraints"] = {
        **source_constraints,
        "invest_authorized": bool(eligible),
        "invest_outcome_authorized": bool(eligible),
        "positive_invest_policy_version": POLICY_VERSION,
        "positive_invest_policy_hash": gate["policy_hash"],
        "positive_invest_evaluation_hash": gate["artifact_hash"],
        "invest_eligible_candidates": eligible,
        "invest_blocked_candidates": list(gate["invest_blocked_candidates"]),
        "candidate_eligibility": list(gate["candidate_results"]),
        "allowed_outcomes": list(gate["allowed_judge_outcomes"]),
        "canonical_b3_reopen_closed": True,
        "new_research_inside_current_b4_allowed": False,
        "risk_authority": False,
        "approval_authority": False,
        "execution_authority": False,
    }
    source_lineage = dict(base.get("source_lineage", {}))
    base["source_lineage"] = {
        **source_lineage,
        "source_v03_context_hash": source_context.context_hash,
        "v04_entry_hash": entry["artifact_hash"],
        "positive_invest_policy_hash": gate["policy_hash"],
        "positive_invest_evaluation_hash": gate["artifact_hash"],
    }
    base.pop("judge_input_hash", None)
    judge_input_hash = canonical_sha256(base)
    model_input = {**base, "judge_input_hash": judge_input_hash}
    return v03.JudgeContext(
        model_input=model_input,
        judge_input_hash=judge_input_hash,
        context_hash=canonical_sha256(model_input),
        mandate_version=source_context.mandate_version,
        deep_comparison_id=source_context.deep_comparison_id,
        allowed_claim_ids=source_context.allowed_claim_ids,
        allowed_dispute_refs=source_context.allowed_dispute_refs,
        allowed_conflict_refs=source_context.allowed_conflict_refs,
        allowed_unknown_refs=source_context.allowed_unknown_refs,
        allowed_condition_refs=source_context.allowed_condition_refs,
    )


def verify_context(
    context: v03.JudgeContext,
    *,
    entry: Mapping[str, Any],
    source_entry: Mapping[str, Any],
    source_context: v03.JudgeContext,
    gate: Mapping[str, Any],
) -> None:
    expected = build_context(
        entry=entry,
        source_entry=source_entry,
        source_context=source_context,
        gate=gate,
    )
    _need(context == expected, "v0.4 context drift")
    _need(
        context.model_input["event_outcome_constraints"]["allowed_outcomes"]
        == list(gate["allowed_judge_outcomes"]),
        "v0.4 outcome surface drift",
    )


def _request(entry: Mapping[str, Any], context: v03.JudgeContext) -> Any:
    selected = v03._selected()
    request = build_bounded_judge_request(
        model_candidate=selected,
        model_input=context.model_input,
        candidate_ids=tuple(entry["candidate_order"]),
        mandate_version=context.mandate_version,
        deep_comparison_id=context.deep_comparison_id,
        judge_input_hash=context.judge_input_hash,
        council_policy_version=entry["council_policy_version"],
        judge_policy_version=entry["judge_policy_version"],
        model_policy_version=entry["model_policy_version"],
        model_run_ref=MODEL_RUN_REF,
        allowed_claim_ids=context.allowed_claim_ids,
        allowed_dispute_refs=context.allowed_dispute_refs,
        allowed_conflict_refs=context.allowed_conflict_refs,
        allowed_unknown_refs=context.allowed_unknown_refs,
        allowed_condition_refs=context.allowed_condition_refs,
    )
    assert_bounded_request_invariants(request)
    _need(
        request.request_payload.get("model") == "gpt-5.6-terra"
        and request.request_payload.get("reasoning") == {"effort": "medium"}
        and request.request_payload.get("max_output_tokens") == 8192,
        "v0.4 request policy drift",
    )
    return request


def build_preflight(
    *,
    code_commit_sha: str,
    entry: Mapping[str, Any],
    context: v03.JudgeContext,
    source_entry: Mapping[str, Any],
    source_context: v03.JudgeContext,
    gate: Mapping[str, Any],
    pricing: Mapping[str, Any],
    historical_request_hashes: list[str],
) -> dict[str, Any]:
    entry_hash = verify_entry(
        entry,
        code_commit_sha=code_commit_sha,
        source_entry=source_entry,
        source_context=source_context,
        gate=gate,
    )
    verify_context(
        context,
        entry=entry,
        source_entry=source_entry,
        source_context=source_context,
        gate=gate,
    )
    request = _request(entry, context)
    _need(
        request.request_hash not in historical_request_hashes,
        "historical Judge request reuse",
    )
    nbytes = len(
        json.dumps(
            request.request_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    cost = runtime_cost_upper_bound_usd(
        model="gpt-5.6-terra",
        input_tokens_upper_bound=nbytes,
        output_tokens_upper_bound=8192,
        call_count=1,
        pricing=pricing,
    )
    value: dict[str, Any] = {
        "artifact_version": PREFLIGHT_VERSION,
        "status": "PASS_ZERO_CALL_POSITIVE_INVEST_JUDGE_PREFLIGHT",
        "code_commit_sha": code_commit_sha,
        "entry_hash": entry_hash,
        "context_hash": context.context_hash,
        "positive_invest_policy_hash": gate["policy_hash"],
        "positive_invest_evaluation_hash": gate["artifact_hash"],
        "invest_eligible_candidates": list(gate["invest_eligible_candidates"]),
        "allowed_judge_outcomes": list(gate["allowed_judge_outcomes"]),
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "max_output_tokens": 8192,
        "request_hash": request.request_hash,
        "request_payload": request.request_payload,
        "request_body_utf8_bytes": nbytes,
        "judge_max_cost_usd": format(cost, "f"),
        "new_paid_calls_planned": 1,
        "new_paid_call_count_ceiling": 1,
        "automatic_retries": 0,
        "owner_approval_required": True,
        "owner_approval_status": "NOT_GRANTED",
        "model_calls_authorized": False,
        "provider_reads_authorized": False,
        "model_calls_this_step": 0,
        "provider_reads_this_step": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd_this_step": "0",
        "live_money": "PROHIBITED",
    }
    value["artifact_hash"] = canonical_sha256(value, exclude_fields=("artifact_hash",))
    return value


def verify_preflight(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload)
    _need(dict(payload) == build_preflight(**inputs), "v0.4 preflight drift")
    return observed


def build_readiness(
    *,
    code_commit_sha: str,
    preflight: Mapping[str, Any],
    **inputs: Any,
) -> dict[str, Any]:
    source = verify_preflight(
        preflight,
        code_commit_sha=code_commit_sha,
        **inputs,
    )
    gate = inputs["gate"]
    value: dict[str, Any] = {
        "artifact_version": READINESS_VERSION,
        "status": "PASS_ZERO_CALL_POSITIVE_INVEST_JUDGE_READINESS",
        "code_commit_sha": code_commit_sha,
        "source_preflight_hash": source,
        "positive_invest_policy_version": POLICY_VERSION,
        "positive_invest_policy_hash": gate["policy_hash"],
        "positive_invest_evaluation_hash": gate["artifact_hash"],
        "invest_eligible_candidates": list(gate["invest_eligible_candidates"]),
        "invest_blocked_candidates": list(gate["invest_blocked_candidates"]),
        "allowed_judge_outcomes": list(gate["allowed_judge_outcomes"]),
        "eligibility_is_necessary_not_sufficient_for_invest": True,
        "judge_retains_terminal_outcome_authority": True,
        "judge_request_hash": preflight["request_hash"],
        "judge_max_cost_usd": preflight["judge_max_cost_usd"],
        "automatic_retries": 0,
        "owner_approval_required": True,
        "owner_approval_status": "NOT_GRANTED",
        "model_calls_authorized": False,
        "model_calls_this_step": 0,
        "provider_reads_this_step": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd_this_step": "0",
        "live_money": "PROHIBITED",
    }
    value["artifact_hash"] = canonical_sha256(value, exclude_fields=("artifact_hash",))
    return value


def verify_readiness(
    payload: Mapping[str, Any],
    *,
    code_commit_sha: str,
    preflight: Mapping[str, Any],
    **inputs: Any,
) -> str:
    observed = _hash(payload)
    expected = build_readiness(
        code_commit_sha=code_commit_sha,
        preflight=preflight,
        **inputs,
    )
    _need(dict(payload) == expected, "v0.4 readiness drift")
    return observed


def build_owner_approval(
    *,
    code_commit_sha: str,
    readiness_hash: str,
    preflight: Mapping[str, Any],
    entry: Mapping[str, Any],
    gate: Mapping[str, Any],
    owner_approval_id: str,
    owner_approval_at_utc: str,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "artifact_version": APPROVAL_VERSION,
        "owner_approval_granted": True,
        "owner_approval_id": owner_approval_id,
        "owner_approval_at_utc": owner_approval_at_utc,
        "approved_executor_code_commit_sha": code_commit_sha,
        "readiness_hash": readiness_hash,
        "entry_hash": entry["artifact_hash"],
        "positive_invest_policy_hash": gate["policy_hash"],
        "positive_invest_evaluation_hash": gate["artifact_hash"],
        "invest_eligible_candidates": list(gate["invest_eligible_candidates"]),
        "allowed_judge_outcomes": list(gate["allowed_judge_outcomes"]),
        "request_hash": preflight["request_hash"],
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "new_paid_call_count": 1,
        "new_paid_call_count_ceiling": 1,
        "max_output_tokens": 8192,
        "approved_judge_max_cost_usd": preflight["judge_max_cost_usd"],
        "automatic_retries": 0,
    }
    value["artifact_hash"] = canonical_sha256(value, exclude_fields=("artifact_hash",))
    return value


def verify_owner_approval(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload)
    expected = build_owner_approval(
        **inputs,
        owner_approval_id=str(payload.get("owner_approval_id", "")),
        owner_approval_at_utc=str(payload.get("owner_approval_at_utc", "")),
    )
    _need(dict(payload) == expected, "v0.4 owner approval drift")
    return observed


def _candidate_gate_row(
    gate: Mapping[str, Any],
    candidate_id: str,
) -> Mapping[str, Any]:
    rows = [
        row
        for row in gate["candidate_results"]
        if row.get("candidate_id") == candidate_id
    ]
    _need(len(rows) == 1, "candidate eligibility row missing or duplicated")
    return rows[0]


def validate_proposal(
    proposal: JudgeDecisionProposalDraft,
    *,
    context: v03.JudgeContext,
    gate: Mapping[str, Any],
) -> None:
    allowed = {JudgeOutcome(value) for value in gate["allowed_judge_outcomes"]}
    _need(proposal.outcome in allowed, "Judge outcome outside v0.4 allowed surface")
    _need(
        proposal.judge_input_hash == context.judge_input_hash
        and proposal.mandate_version == context.mandate_version
        and proposal.deep_comparison_id == context.deep_comparison_id
        and proposal.model_run_ref == MODEL_RUN_REF,
        "v0.4 lineage violation",
    )
    _need(proposal.execution_authority is False, "B4 cannot gain execution authority")
    _need(
        proposal.research_reopen_required is False
        and not proposal.research_reopen_reason_codes,
        "v0.4 current closed-B3 lifecycle forbids embedded research reopen",
    )
    _need(
        set(proposal.selected_candidate_basis_claim_ids).issubset(
            context.allowed_claim_ids
        ),
        "basis claim outside canonical graph",
    )
    _need(
        all(
            set(row.claim_ids).issubset(context.allowed_claim_ids)
            for row in proposal.why_not_other_candidates
        ),
        "why-not claim outside canonical graph",
    )
    _need(
        set(proposal.unresolved_dispute_refs).issubset(context.allowed_dispute_refs)
        and set(proposal.material_conflict_refs).issubset(
            context.allowed_conflict_refs
        )
        and set(proposal.material_unknown_refs).issubset(
            context.allowed_unknown_refs
        ),
        "proposal reference outside canonical graph",
    )
    _need(
        all(
            set(row.source_or_claim_refs).issubset(context.allowed_condition_refs)
            for row in proposal.what_would_change_decision
        )
        and set(proposal.invalidation_condition_refs).issubset(
            context.allowed_condition_refs
        ),
        "condition outside canonical graph",
    )

    if proposal.outcome == JudgeOutcome.INVEST:
        primary = proposal.primary_candidate_id
        _need(
            isinstance(primary, str)
            and primary in gate["invest_eligible_candidates"],
            "INVEST primary candidate is not gate-eligible",
        )
        row = _candidate_gate_row(gate, primary)
        allowed_basis = set(row["supported_basis_claim_ids"])
        _need(
            bool(proposal.selected_candidate_basis_claim_ids)
            and set(proposal.selected_candidate_basis_claim_ids).issubset(
                allowed_basis
            ),
            "INVEST basis is not a gate-approved supported canonical basis",
        )
        # The positive gate is the single deterministic authority for whether
        # the selected primary has a blocking conflict, uncertainty, research,
        # or integrity state.  The Judge is allowed to keep canonical,
        # non-blocking decision context visible (including context belonging to
        # other candidates); every reference is still constrained above.
        _need(
            not proposal.blocking_reason_codes,
            "INVEST cannot carry blocking reasons",
        )
        _need(
            proposal.evidence_status != JudgeEvidenceStatus.INSUFFICIENT,
            "INVEST cannot use insufficient evidence",
        )
        _need(
            proposal.next_directive == JudgeNextDirective.PROMOTE_FINAL_DECISION,
            "INVEST requires PROMOTE_FINAL_DECISION",
        )
        why_not_ids = {row.candidate_id for row in proposal.why_not_other_candidates}
        _need(
            why_not_ids
            == set(gate["candidate_order"]) - {primary},
            "INVEST must explicitly explain why every other candidate was not selected",
        )
    elif proposal.outcome == JudgeOutcome.WATCH:
        _need(
            proposal.next_directive == JudgeNextDirective.MONITOR
            and bool(proposal.what_would_change_decision),
            "WATCH requires MONITOR and what-would-change conditions",
        )
    elif proposal.outcome == JudgeOutcome.ABSTAIN:
        _need(
            proposal.primary_candidate_id is None
            and proposal.next_directive == JudgeNextDirective.STOP,
            "ABSTAIN requires null primary candidate and STOP",
        )


def build_raw_capture(
    *,
    request_hash: str,
    raw: Mapping[str, Any],
    started_at: str,
    captured_at: str,
) -> dict[str, Any]:
    external = _external_json_value(raw)
    _need(isinstance(external, Mapping), "provider response must be Mapping")
    value: dict[str, Any] = {
        "capture_version": RAW_CAPTURE_VERSION,
        "request_hash": request_hash,
        "provider_response_id": external.get("id"),
        "dispatch_started_at_utc": started_at,
        "captured_at_utc": captured_at,
        "raw_response": dict(external),
    }
    value["raw_response_hash"] = external_provider_json_sha256(value)
    return value


def verify_raw_capture(
    payload: Mapping[str, Any],
    *,
    request_hash: str,
) -> str:
    observed = payload.get("raw_response_hash")
    _need(
        isinstance(observed, str)
        and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        "raw response hash missing",
    )
    stripped = dict(payload)
    stripped.pop("raw_response_hash", None)
    _need(
        observed == external_provider_json_sha256(stripped)
        and payload.get("capture_version") == RAW_CAPTURE_VERSION
        and payload.get("request_hash") == request_hash,
        "v0.4 raw capture drift",
    )
    return observed


def execute_paid(
    *,
    execute_paid_judge: bool,
    branch: str,
    code_commit_sha: str,
    worktree_clean: bool,
    preflight: Mapping[str, Any],
    readiness: Mapping[str, Any],
    approval: Mapping[str, Any] | None,
    entry: Mapping[str, Any],
    context: v03.JudgeContext,
    source_entry: Mapping[str, Any],
    source_context: v03.JudgeContext,
    gate: Mapping[str, Any],
    pricing: Mapping[str, Any],
    historical_request_hashes: list[str],
    ledger_path: Path,
    raw_path: Path,
    result_path: Path,
    transport_factory: Callable[
        [], Callable[[Mapping[str, Any]], Mapping[str, Any]]
    ],
) -> dict[str, Any]:
    """Execute at most one paid Judge call after explicit owner approval."""
    _need(
        execute_paid_judge is True and approval is not None,
        "explicit paid Judge flag and owner approval required",
    )
    _need(
        branch == "hackathon/alpaca-2026"
        and worktree_clean
        and not ledger_path.exists()
        and not raw_path.exists()
        and not result_path.exists(),
        "v0.4 pre-transport gate failed",
    )
    readiness_hash = verify_readiness(
        readiness,
        code_commit_sha=code_commit_sha,
        preflight=preflight,
        entry=entry,
        context=context,
        source_entry=source_entry,
        source_context=source_context,
        gate=gate,
        pricing=pricing,
        historical_request_hashes=historical_request_hashes,
    )
    verify_preflight(
        preflight,
        code_commit_sha=code_commit_sha,
        entry=entry,
        context=context,
        source_entry=source_entry,
        source_context=source_context,
        gate=gate,
        pricing=pricing,
        historical_request_hashes=historical_request_hashes,
    )
    approval_hash = verify_owner_approval(
        approval,
        code_commit_sha=code_commit_sha,
        readiness_hash=readiness_hash,
        preflight=preflight,
        entry=entry,
        gate=gate,
    )
    request = _request(entry, context)
    _need(request.request_hash == preflight["request_hash"], "request reconstruction drift")

    ledger: dict[str, Any] = {
        "ledger_version": LEDGER_VERSION,
        "approval_hash": approval_hash,
        "entries": [
            {
                "dispatch_index": 1,
                "request_hash": request.request_hash,
                "state": "NOT_DISPATCHED",
                "automatic_retry_permitted": False,
            }
        ],
    }
    ledger["ledger_hash"] = canonical_sha256(
        ledger, exclude_fields=("ledger_hash",)
    )
    _write_exclusive(ledger_path, ledger)

    row = ledger["entries"][0]
    row.update(
        state="DISPATCH_STARTED_UNKNOWN",
        dispatch_started_at_utc=_utc(datetime.now(UTC)),
    )
    ledger["ledger_hash"] = canonical_sha256(
        ledger, exclude_fields=("ledger_hash",)
    )
    _replace_durable(ledger_path, ledger)

    try:
        raw = transport_factory()(preflight["request_payload"])
    except Exception as exc:
        row["stop_reason"] = f"AMBIGUOUS_PROVIDER_OUTCOME:{type(exc).__name__}"
        ledger["ledger_hash"] = canonical_sha256(
            ledger, exclude_fields=("ledger_hash",)
        )
        _replace_durable(ledger_path, ledger)
        raise CurrentJudgeV04Error("ambiguous provider outcome") from exc

    capture = build_raw_capture(
        request_hash=request.request_hash,
        raw=raw,
        started_at=row["dispatch_started_at_utc"],
        captured_at=_utc(datetime.now(UTC)),
    )
    _write_exclusive(raw_path, capture)
    raw_hash = verify_raw_capture(capture, request_hash=request.request_hash)
    row.update(raw_response_hash=raw_hash, raw_response_path=str(raw_path))
    ledger["ledger_hash"] = canonical_sha256(
        ledger, exclude_fields=("ledger_hash",)
    )
    _replace_durable(ledger_path, ledger)

    try:
        call, proposal = parse_council_responses_payload(
            raw, request=request, latency_ms=0
        )
        validate_proposal(proposal, context=context, gate=gate)
        frozen = FrozenJudgeDecisionProposal.from_draft(proposal)
        actual = actual_cost_usd(
            raw, model="gpt-5.6-terra", pricing=pricing
        )
        _need(
            actual <= Decimal(str(preflight["judge_max_cost_usd"])),
            "actual Judge cost exceeds approved ceiling",
        )
        record: dict[str, Any] = {
            "outcome": proposal.outcome.value,
            "next_directive": proposal.next_directive.value,
            "response_id": call.response_id,
            "frozen_judge_proposal": frozen.model_dump(
                mode="json", exclude_none=False
            ),
        }
        record["record_hash"] = canonical_sha256(
            record, exclude_fields=("record_hash",)
        )
    except Exception as exc:
        row["stop_reason"] = (
            f"RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:{type(exc).__name__}"
        )
        ledger["ledger_hash"] = canonical_sha256(
            ledger, exclude_fields=("ledger_hash",)
        )
        _replace_durable(ledger_path, ledger)
        raise CurrentJudgeV04Error(
            "captured Judge response failed v0.4 validation"
        ) from exc

    row.update(
        state="COMPLETED",
        processed_record_hash=record["record_hash"],
        actual_cost_usd=format(actual, "f"),
    )
    ledger["ledger_hash"] = canonical_sha256(
        ledger, exclude_fields=("ledger_hash",)
    )
    _replace_durable(ledger_path, ledger)

    invest = proposal.outcome == JudgeOutcome.INVEST
    result: dict[str, Any] = {
        "artifact_version": RESULT_VERSION,
        "status": "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_V04_FROZEN",
        "code_commit_sha": code_commit_sha,
        "positive_invest_policy_hash": gate["policy_hash"],
        "positive_invest_evaluation_hash": gate["artifact_hash"],
        "raw_response_hash": raw_hash,
        "processed_record": record,
        "decision_created_at_utc": capture["captured_at_utc"],
        "actual_cost_usd": format(actual, "f"),
        "final_b4_decision_created": True,
        "b5_handoff_eligible": invest,
        "b5_handoff_created": False,
        "research_reopen_created": False,
        "model_calls": 1,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "automatic_retries": 0,
        "live_money": "PROHIBITED",
        "ledger_hash": ledger["ledger_hash"],
    }
    result["artifact_hash"] = canonical_sha256(
        result, exclude_fields=("artifact_hash",)
    )
    _write_exclusive(result_path, result)
    return result
