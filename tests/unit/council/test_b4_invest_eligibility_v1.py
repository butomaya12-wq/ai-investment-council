from __future__ import annotations

from copy import deepcopy

from aic.council.invest_eligibility_v1 import (
    BLOCK_MATERIAL_CONFLICT,
    BLOCK_NO_BASIS,
    BLOCK_OPEN_UNKNOWN,
    BLOCK_RESEARCH_REOPEN,
    BLOCK_UNRESOLVED_INTEGRITY,
    INVEST_BLOCKED,
    INVEST_ELIGIBLE,
    POLICY_VERSION,
    evaluate_positive_invest_eligibility,
)
from aic.council.judge_eval_preflight import build_judge_eval_cases


CANDIDATES = ["A", "B", "C"]


def claim(
    candidate: str,
    suffix: str,
    *,
    support: str = "SUPPORTED",
    conflicts: list[str] | None = None,
) -> dict:
    return {
        "claim_id": f"{candidate}_{suffix}",
        "candidate_id": candidate,
        "materiality": "MATERIAL",
        "support_status": support,
        "conflict_ids": list(conflicts or []),
        "evidence_ids": [f"EVIDENCE_{candidate}_{suffix}"],
        "computed_value_ids": [],
    }


def source_entry() -> dict:
    return {
        "canonical_open_research_requirements_after_b3": [],
        "additional_provider_read_required": False,
        "candidate_aware_reopen_provenance": "PASS",
    }


def model_input() -> dict:
    claims = [
        claim("A", "BASIS"),
        claim("B", "BASIS"),
        claim("C", "BASIS"),
    ]
    return {
        "candidate_order": list(CANDIDATES),
        "candidate_packets": [{"candidate_id": x} for x in CANDIDATES],
        "material_claims": claims,
        "rebuttal_bundles": [
            {
                "candidate_id": x,
                "items": [],
                "research_reopen_required": False,
                "research_reopen_reason_codes": [],
            }
            for x in CANDIDATES
        ],
        "decision_context_uncertainties": [],
        "material_conflict_refs": [],
        "unresolved_dispute_refs": [],
        "event_outcome_constraints": {"canonical_b3_reopen_closed": True},
    }


def by_candidate(result: dict) -> dict[str, dict]:
    return {row["candidate_id"]: row for row in result["candidate_results"]}


def test_clean_supported_candidates_open_invest_surface_without_selecting_invest():
    result = evaluate_positive_invest_eligibility(
        source_entry=source_entry(),
        model_input=model_input(),
    )
    assert result["policy_version"] == POLICY_VERSION
    assert result["invest_eligible_candidates"] == CANDIDATES
    assert result["allowed_judge_outcomes"] == ["INVEST", "WATCH", "ABSTAIN"]
    assert result["eligibility_is_necessary_not_sufficient_for_invest"] is True
    assert result["judge_retains_terminal_outcome_authority"] is True
    assert result["risk_authority"] is False
    assert result["approval_authority"] is False
    assert result["execution_authority"] is False
    assert result["broker_writes"] == 0
    assert result["alpaca_orders"] == 0


def test_e3_semantics_no_supported_basis_blocks_invest():
    data = model_input()
    for row in data["material_claims"]:
        row["support_status"] = "INSUFFICIENT"
    result = evaluate_positive_invest_eligibility(
        source_entry=source_entry(),
        model_input=data,
    )
    assert result["invest_eligible_candidates"] == []
    assert result["allowed_judge_outcomes"] == ["WATCH", "ABSTAIN"]
    assert all(
        BLOCK_NO_BASIS in row["block_reason_codes"]
        for row in result["candidate_results"]
    )


def test_e4_semantics_material_conflict_blocks_affected_candidate():
    data = model_input()
    data["material_claims"][0]["support_status"] = "CONFLICTED"
    data["material_claims"][0]["conflict_ids"] = ["BLOCKING_SOURCE_CONFLICT"]
    result = evaluate_positive_invest_eligibility(
        source_entry=source_entry(),
        model_input=data,
    )
    rows = by_candidate(result)
    assert rows["A"]["status"] == INVEST_BLOCKED
    assert BLOCK_MATERIAL_CONFLICT in rows["A"]["block_reason_codes"]
    assert rows["B"]["status"] == INVEST_ELIGIBLE
    assert rows["C"]["status"] == INVEST_ELIGIBLE


def test_e10_semantics_unattributed_material_unknown_fails_closed():
    data = model_input()
    data["material_unknown_refs"] = ["BLOCKING_UNKNOWN"]
    result = evaluate_positive_invest_eligibility(
        source_entry=source_entry(),
        model_input=data,
    )
    assert result["invest_eligible_candidates"] == []
    assert all(
        BLOCK_OPEN_UNKNOWN in row["block_reason_codes"]
        for row in result["candidate_results"]
    )


def test_closed_decision_context_uncertainty_stays_visible_but_does_not_block():
    data = model_input()
    data["decision_context_uncertainties"] = [
        {
            "candidate_id": "A",
            "uncertainty_ref": "A:CLOSED_NEWS_GAP",
            "global_reason_closed": True,
        }
    ]
    result = evaluate_positive_invest_eligibility(
        source_entry=source_entry(),
        model_input=data,
    )
    assert by_candidate(result)["A"]["status"] == INVEST_ELIGIBLE


def test_candidate_research_reopen_blocks_only_that_candidate():
    data = model_input()
    data["rebuttal_bundles"][1]["research_reopen_required"] = True
    data["rebuttal_bundles"][1]["research_reopen_reason_codes"] = ["B_REOPEN"]
    result = evaluate_positive_invest_eligibility(
        source_entry=source_entry(),
        model_input=data,
    )
    rows = by_candidate(result)
    assert rows["A"]["status"] == INVEST_ELIGIBLE
    assert rows["B"]["status"] == INVEST_BLOCKED
    assert BLOCK_RESEARCH_REOPEN in rows["B"]["block_reason_codes"]
    assert rows["C"]["status"] == INVEST_ELIGIBLE


def test_unresolved_rebuttal_integrity_finding_blocks_only_that_candidate():
    data = model_input()
    data["rebuttal_bundles"][2]["items"] = [
        {
            "response_type": "UNRESOLVED",
            "opposing_finding_ids": ["C_BLOCKING_FINDING"],
        }
    ]
    data["unresolved_dispute_refs"] = ["C_BLOCKING_FINDING"]
    result = evaluate_positive_invest_eligibility(
        source_entry=source_entry(),
        model_input=data,
    )
    rows = by_candidate(result)
    assert rows["C"]["status"] == INVEST_BLOCKED
    assert BLOCK_UNRESOLVED_INTEGRITY in rows["C"]["block_reason_codes"]
    assert rows["A"]["status"] == INVEST_ELIGIBLE
    assert rows["B"]["status"] == INVEST_ELIGIBLE


def test_candidate_order_does_not_change_per_candidate_eligibility():
    data = model_input()
    data["material_claims"][0]["support_status"] = "CONFLICTED"
    data["material_claims"][0]["conflict_ids"] = ["A_CONFLICT"]
    first = evaluate_positive_invest_eligibility(
        source_entry=source_entry(),
        model_input=data,
    )

    permuted = deepcopy(data)
    permuted["candidate_order"] = ["C", "A", "B"]
    permuted["candidate_packets"] = [
        {"candidate_id": x} for x in permuted["candidate_order"]
    ]
    permuted["rebuttal_bundles"] = [
        next(
            row
            for row in data["rebuttal_bundles"]
            if row["candidate_id"] == candidate
        )
        for candidate in permuted["candidate_order"]
    ]
    second = evaluate_positive_invest_eligibility(
        source_entry=source_entry(),
        model_input=permuted,
    )

    first_status = {
        row["candidate_id"]: row["status"] for row in first["candidate_results"]
    }
    second_status = {
        row["candidate_id"]: row["status"] for row in second["candidate_results"]
    }
    assert first_status == second_status


def test_existing_frozen_judge_eval_outcome_oracle_is_not_rewritten():
    cases = {case.case_id: case for case in build_judge_eval_cases()}
    assert [out.value for out in cases["E11"].expected_outcomes] == ["INVEST"]
    assert [out.value for out in cases["E12"].expected_outcomes] == ["INVEST"]
    assert [out.value for out in cases["E14"].expected_outcomes] == ["INVEST"]
    assert "INVEST" not in [out.value for out in cases["E3"].expected_outcomes]
    assert "INVEST" not in [out.value for out in cases["E4"].expected_outcomes]
    assert "INVEST" not in [out.value for out in cases["E10"].expected_outcomes]
    assert [out.value for out in cases["E15"].expected_outcomes] == ["ABSTAIN"]
