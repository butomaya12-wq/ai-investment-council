"""Candidate-independent positive INVEST eligibility gate for B4.

This module does not choose an investment, option, size, price, risk result,
approval, or execution. It only determines whether a candidate may appear on
the Judge's INVEST outcome surface.

Historical B4 artifacts remain immutable; this is an additive policy version.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256


POLICY_ARTIFACT_VERSION = "B4_POSITIVE_INVEST_ELIGIBILITY_POLICY_v1"
POLICY_VERSION = "B4_POSITIVE_INVEST_ELIGIBILITY_v1"
POLICY_STATUS = "ACTIVE_CANDIDATE_INDEPENDENT_POSITIVE_INVEST_GATE"
EVALUATION_VERSION = "B4_POSITIVE_INVEST_ELIGIBILITY_EVALUATION_v1"

INVEST_ELIGIBLE = "INVEST_ELIGIBLE"
INVEST_BLOCKED = "INVEST_BLOCKED"

BLOCK_RESEARCH_NOT_CLOSED = "CANONICAL_RESEARCH_NOT_CLOSED"
BLOCK_PROVIDER_READ_REQUIRED = "ADDITIONAL_PROVIDER_READ_REQUIRED"
BLOCK_RESEARCH_REOPEN = "CANDIDATE_RESEARCH_REOPEN_REQUIRED"
BLOCK_MATERIAL_CONFLICT = "BLOCKING_MATERIAL_CONFLICT"
BLOCK_OPEN_UNKNOWN = "BLOCKING_OPEN_MATERIAL_UNKNOWN"
BLOCK_UNRESOLVED_INTEGRITY = "UNRESOLVED_BLOCKING_INTEGRITY_FINDING"
BLOCK_NO_BASIS = "NO_SUPPORTED_CANONICAL_DECISION_BASIS"
BLOCK_LINEAGE = "CANDIDATE_LINEAGE_INCOMPLETE"

ALLOWED_OUTCOMES_WITH_INVEST = ("INVEST", "WATCH", "ABSTAIN")
ALLOWED_OUTCOMES_FAIL_CLOSED = ("WATCH", "ABSTAIN")


class InvestEligibilityError(ValueError):
    """Raised when the deterministic eligibility input surface is malformed."""


def _need(ok: bool, message: str) -> None:
    if not ok:
        raise InvestEligibilityError(message)


def build_policy_artifact() -> dict[str, Any]:
    """Return the frozen, candidate-independent positive eligibility policy."""
    value: dict[str, Any] = {
        "artifact_version": POLICY_ARTIFACT_VERSION,
        "policy_version": POLICY_VERSION,
        "status": POLICY_STATUS,
        "purpose": "DETERMINE_IF_CANDIDATE_MAY_BE_PRESENTED_TO_JUDGE_AS_INVEST_ELIGIBLE",
        "decision_rule": "ALL_HARD_GATES_PASS_AND_AT_LEAST_ONE_SUPPORTED_CANONICAL_DECISION_BASIS",
        "supported_basis_rule": {
            "materiality": "MATERIAL",
            "support_status": "SUPPORTED",
            "conflict_ids": "EMPTY",
            "requires_evidence_or_computed_value": True,
        },
        "hard_gates": [
            "B3_CANONICAL_RESEARCH_LIFECYCLE_CLOSED",
            "NO_ADDITIONAL_PROVIDER_READ_REQUIRED",
            "NO_CANDIDATE_RESEARCH_REOPEN_REQUIRED",
            "NO_BLOCKING_MATERIAL_CONFLICT",
            "NO_BLOCKING_OPEN_MATERIAL_UNKNOWN",
            "NO_UNRESOLVED_BLOCKING_INTEGRITY_FINDING",
            "CANDIDATE_LINEAGE_PRESENT",
            "SUPPORTED_CANONICAL_DECISION_BASIS_PRESENT",
        ],
        "non_rules": [
            "NO_CANDIDATE_NAME_OR_SYMBOL_TUNING",
            "NO_TEXT_SENTIMENT_THRESHOLD",
            "NO_OPTION_SELECTION",
            "NO_SIZING",
            "NO_PRICE_SELECTION",
            "NO_RISK_COMPUTATION",
            "NO_APPROVAL",
            "NO_EXECUTION",
        ],
        "semantics": {
            "eligibility_is_necessary_not_sufficient_for_invest": True,
            "judge_retains_relative_merit_and_terminal_outcome_authority": True,
            "supported_but_unattractive_candidates_may_still_be_abstained_by_judge": True,
            "closed_decision_context_uncertainty_remains_visible_but_is_not_a_reopen_blocker": True,
            "any_unattributed_open_material_unknown_fails_closed_globally": True,
            "any_unattributed_material_conflict_fails_closed_globally": True,
            "any_unattributed_unresolved_dispute_fails_closed_globally": True,
        },
        "authority_boundaries": {
            "risk_authority": False,
            "approval_authority": False,
            "execution_authority": False,
            "option_contract_authority": False,
            "quantity_authority": False,
            "price_authority": False,
        },
    }
    value["policy_hash"] = canonical_sha256(value)
    return value


POLICY = build_policy_artifact()


def verify_policy_artifact(payload: Mapping[str, Any]) -> str:
    expected = build_policy_artifact()
    _need(dict(payload) == expected, "positive INVEST eligibility policy drift")
    return str(expected["policy_hash"])


def _as_rows(value: Any, *, field: str) -> list[Mapping[str, Any]]:
    _need(isinstance(value, list), f"{field} must be a list")
    _need(all(isinstance(row, Mapping) for row in value), f"{field} rows must be objects")
    return list(value)


def _supported_basis_claim_ids(
    claims: Sequence[Mapping[str, Any]],
    *,
    candidate_id: str,
) -> list[str]:
    result: list[str] = []
    for claim in claims:
        if claim.get("candidate_id") != candidate_id:
            continue
        if claim.get("materiality") != "MATERIAL":
            continue
        if claim.get("support_status") != "SUPPORTED":
            continue
        conflict_ids = claim.get("conflict_ids", [])
        if not isinstance(conflict_ids, list) or conflict_ids:
            continue
        evidence_ids = claim.get("evidence_ids", [])
        computed_value_ids = claim.get("computed_value_ids", [])
        if not isinstance(evidence_ids, list) or not isinstance(computed_value_ids, list):
            continue
        if not evidence_ids and not computed_value_ids:
            continue
        claim_id = claim.get("claim_id")
        if isinstance(claim_id, str) and claim_id:
            result.append(claim_id)
    return list(dict.fromkeys(result))


def _candidate_conflict_refs(
    claims: Sequence[Mapping[str, Any]],
    *,
    candidate_id: str,
) -> list[str]:
    refs: list[str] = []
    for claim in claims:
        if claim.get("candidate_id") != candidate_id:
            continue
        if claim.get("materiality") != "MATERIAL":
            continue
        conflict_ids = claim.get("conflict_ids", [])
        if isinstance(conflict_ids, list):
            refs.extend(str(ref) for ref in conflict_ids if isinstance(ref, str) and ref)
        if claim.get("support_status") == "CONFLICTED":
            claim_id = claim.get("claim_id")
            if isinstance(claim_id, str) and claim_id:
                refs.append(f"CONFLICTED_CLAIM:{claim_id}")
    return list(dict.fromkeys(refs))


def _candidate_rebuttal(
    rebuttals: Sequence[Mapping[str, Any]],
    *,
    candidate_id: str,
) -> Mapping[str, Any] | None:
    matches = [row for row in rebuttals if row.get("candidate_id") == candidate_id]
    _need(len(matches) <= 1, f"duplicate rebuttal bundle for {candidate_id}")
    return matches[0] if matches else None


def _candidate_unresolved_disputes(
    rebuttal: Mapping[str, Any] | None,
) -> list[str]:
    if rebuttal is None:
        return []
    refs: list[str] = []
    for item in _as_rows(rebuttal.get("items", []), field="rebuttal.items"):
        if item.get("response_type") != "UNRESOLVED":
            continue
        opposing = item.get("opposing_finding_ids", [])
        if isinstance(opposing, list):
            refs.extend(str(ref) for ref in opposing if isinstance(ref, str) and ref)
    return list(dict.fromkeys(refs))


def _candidate_open_unknowns(
    model_input: Mapping[str, Any],
    *,
    candidate_id: str,
) -> list[str]:
    rows = model_input.get("decision_context_uncertainties", [])
    if rows is None:
        rows = []
    rows = _as_rows(rows, field="decision_context_uncertainties")
    result: list[str] = []
    for row in rows:
        if row.get("candidate_id") != candidate_id:
            continue
        if row.get("global_reason_closed") is True:
            continue
        ref = row.get("uncertainty_ref") or row.get("raw_reason_or_ref")
        if isinstance(ref, str) and ref:
            result.append(ref)
    return list(dict.fromkeys(result))


def evaluate_positive_invest_eligibility(
    *,
    source_entry: Mapping[str, Any],
    model_input: Mapping[str, Any],
    policy: Mapping[str, Any] = POLICY,
) -> dict[str, Any]:
    """Evaluate the positive B4 INVEST eligibility surface deterministically."""
    policy_hash = verify_policy_artifact(policy)

    candidate_order = model_input.get("candidate_order")
    _need(
        isinstance(candidate_order, list)
        and candidate_order
        and all(isinstance(x, str) and x for x in candidate_order)
        and len(set(candidate_order)) == len(candidate_order),
        "candidate_order must contain unique candidate IDs",
    )
    candidates = tuple(candidate_order)

    packets = _as_rows(model_input.get("candidate_packets", []), field="candidate_packets")
    packet_ids = [row.get("candidate_id") for row in packets]
    _need(
        len(packet_ids) == len(candidates) and set(packet_ids) == set(candidates),
        "candidate packet lineage does not exactly cover candidate_order",
    )

    claims = _as_rows(model_input.get("material_claims", []), field="material_claims")
    _need(bool(claims), "positive eligibility requires canonical material claims")
    claim_ids = [row.get("claim_id") for row in claims]
    _need(
        all(isinstance(x, str) and x for x in claim_ids)
        and len(set(claim_ids)) == len(claim_ids),
        "material_claims must have unique claim IDs",
    )
    _need(
        all(row.get("candidate_id") in candidates for row in claims),
        "material claim candidate lineage outside candidate_order",
    )

    rebuttals = _as_rows(model_input.get("rebuttal_bundles", []), field="rebuttal_bundles")
    if rebuttals:
        _need(
            set(row.get("candidate_id") for row in rebuttals).issubset(set(candidates)),
            "rebuttal candidate outside candidate_order",
        )

    global_research_closed = (
        source_entry.get("canonical_open_research_requirements_after_b3") == []
        and source_entry.get("candidate_aware_reopen_provenance") == "PASS"
        and model_input.get("event_outcome_constraints", {}).get("canonical_b3_reopen_closed") is True
    )
    additional_provider_read_required = source_entry.get("additional_provider_read_required")
    _need(
        additional_provider_read_required in {True, False},
        "additional_provider_read_required must be boolean",
    )

    all_claim_conflicts = {
        str(ref)
        for claim in claims
        for ref in claim.get("conflict_ids", [])
        if isinstance(ref, str) and ref
    }
    global_conflict_refs = model_input.get("material_conflict_refs", [])
    _need(isinstance(global_conflict_refs, list), "material_conflict_refs must be a list")
    unattributed_global_conflicts = [
        str(ref)
        for ref in global_conflict_refs
        if isinstance(ref, str) and ref and ref not in all_claim_conflicts
    ]

    attributed_disputes = {
        ref
        for candidate in candidates
        for ref in _candidate_unresolved_disputes(
            _candidate_rebuttal(rebuttals, candidate_id=candidate)
        )
    }
    global_disputes = model_input.get("unresolved_dispute_refs", [])
    _need(isinstance(global_disputes, list), "unresolved_dispute_refs must be a list")
    unattributed_global_disputes = [
        str(ref)
        for ref in global_disputes
        if isinstance(ref, str) and ref and ref not in attributed_disputes
    ]

    top_level_unknowns = model_input.get("material_unknown_refs", [])
    if top_level_unknowns is None:
        top_level_unknowns = []
    _need(isinstance(top_level_unknowns, list), "material_unknown_refs must be a list")
    global_open_unknowns = [
        str(ref) for ref in top_level_unknowns if isinstance(ref, str) and ref
    ]

    candidate_results: list[dict[str, Any]] = []
    for candidate_id in candidates:
        blockers: list[str] = []
        blocker_refs: dict[str, list[str]] = {}
        basis_ids = _supported_basis_claim_ids(claims, candidate_id=candidate_id)

        if not global_research_closed:
            blockers.append(BLOCK_RESEARCH_NOT_CLOSED)
        if additional_provider_read_required is True:
            blockers.append(BLOCK_PROVIDER_READ_REQUIRED)

        rebuttal = _candidate_rebuttal(rebuttals, candidate_id=candidate_id)
        if rebuttal is None and rebuttals:
            blockers.append(BLOCK_LINEAGE)
        if rebuttal is not None:
            reason_codes = rebuttal.get("research_reopen_reason_codes", [])
            reopen = rebuttal.get("research_reopen_required")
            if reopen is True or (isinstance(reason_codes, list) and reason_codes):
                blockers.append(BLOCK_RESEARCH_REOPEN)
                if isinstance(reason_codes, list):
                    blocker_refs[BLOCK_RESEARCH_REOPEN] = [
                        str(x) for x in reason_codes if isinstance(x, str) and x
                    ]

        conflicts = _candidate_conflict_refs(claims, candidate_id=candidate_id)
        conflicts.extend(unattributed_global_conflicts)
        conflicts = list(dict.fromkeys(conflicts))
        if conflicts:
            blockers.append(BLOCK_MATERIAL_CONFLICT)
            blocker_refs[BLOCK_MATERIAL_CONFLICT] = conflicts

        open_unknowns = _candidate_open_unknowns(model_input, candidate_id=candidate_id)
        open_unknowns.extend(global_open_unknowns)
        open_unknowns = list(dict.fromkeys(open_unknowns))
        if open_unknowns:
            blockers.append(BLOCK_OPEN_UNKNOWN)
            blocker_refs[BLOCK_OPEN_UNKNOWN] = open_unknowns

        disputes = _candidate_unresolved_disputes(rebuttal)
        disputes.extend(unattributed_global_disputes)
        disputes = list(dict.fromkeys(disputes))
        if disputes:
            blockers.append(BLOCK_UNRESOLVED_INTEGRITY)
            blocker_refs[BLOCK_UNRESOLVED_INTEGRITY] = disputes

        if not basis_ids:
            blockers.append(BLOCK_NO_BASIS)

        blockers = list(dict.fromkeys(blockers))
        candidate_results.append(
            {
                "candidate_id": candidate_id,
                "status": INVEST_ELIGIBLE if not blockers else INVEST_BLOCKED,
                "supported_basis_claim_ids": basis_ids,
                "block_reason_codes": blockers,
                "block_refs": blocker_refs,
            }
        )

    eligible = [
        row["candidate_id"] for row in candidate_results if row["status"] == INVEST_ELIGIBLE
    ]
    blocked = [
        row["candidate_id"] for row in candidate_results if row["status"] == INVEST_BLOCKED
    ]
    allowed = (
        list(ALLOWED_OUTCOMES_WITH_INVEST)
        if eligible
        else list(ALLOWED_OUTCOMES_FAIL_CLOSED)
    )

    value: dict[str, Any] = {
        "artifact_version": EVALUATION_VERSION,
        "status": "PASS_POSITIVE_INVEST_ELIGIBILITY_EVALUATED",
        "policy_version": POLICY_VERSION,
        "policy_hash": policy_hash,
        "candidate_order": list(candidates),
        "candidate_results": candidate_results,
        "invest_eligible_candidates": eligible,
        "invest_blocked_candidates": blocked,
        "allowed_judge_outcomes": allowed,
        "invest_outcome_present": bool(eligible),
        "eligibility_is_necessary_not_sufficient_for_invest": True,
        "judge_retains_terminal_outcome_authority": True,
        "risk_authority": False,
        "approval_authority": False,
        "execution_authority": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    value["artifact_hash"] = canonical_sha256(value)
    return value


def verify_positive_invest_eligibility(
    payload: Mapping[str, Any],
    *,
    source_entry: Mapping[str, Any],
    model_input: Mapping[str, Any],
    policy: Mapping[str, Any] = POLICY,
) -> str:
    expected = evaluate_positive_invest_eligibility(
        source_entry=source_entry,
        model_input=model_input,
        policy=policy,
    )
    _need(dict(payload) == expected, "positive INVEST eligibility evaluation drift")
    return str(expected["artifact_hash"])
