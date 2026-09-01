from aic.council.invest_eligibility_v1 import (
    BLOCK_RESEARCH_REOPEN,
    INVEST_ELIGIBLE,
    evaluate_positive_invest_eligibility,
)


def test_historical_reopen_flag_does_not_override_closed_canonical_lifecycle():
    source_entry = {
        "canonical_open_research_requirements_after_b3": [],
        "additional_provider_read_required": False,
        "candidate_aware_reopen_provenance": "PASS",
    }
    model_input = {
        "candidate_order": ["NVDA"],
        "candidate_packets": [{"candidate_id": "NVDA"}],
        "material_claims": [
            {
                "claim_id": "NVDA_SUPPORTED_BASIS",
                "candidate_id": "NVDA",
                "materiality": "MATERIAL",
                "support_status": "SUPPORTED",
                "conflict_ids": [],
                "evidence_ids": ["NVDA_EVIDENCE"],
                "computed_value_ids": [],
            }
        ],
        "rebuttal_bundles": [
            {
                "candidate_id": "NVDA",
                "items": [],
                "research_reopen_required": True,
                "research_reopen_reason_codes": [
                    "ALPACA_NEWS_PAGINATION_INCOMPLETE"
                ],
            }
        ],
        "decision_context_uncertainties": [
            {
                "candidate_id": "NVDA",
                "uncertainty_ref": "NVDA:ALPACA_NEWS_PAGINATION_INCOMPLETE",
                "raw_reason_or_ref": "ALPACA_NEWS_PAGINATION_INCOMPLETE",
                "global_reason_closed": True,
                "may_independently_force_new_research_reopen": False,
            }
        ],
        "material_conflict_refs": [],
        "unresolved_dispute_refs": [],
        "event_outcome_constraints": {"canonical_b3_reopen_closed": True},
    }

    result = evaluate_positive_invest_eligibility(
        source_entry=source_entry,
        model_input=model_input,
    )
    row = result["candidate_results"][0]
    assert row["status"] == INVEST_ELIGIBLE
    assert BLOCK_RESEARCH_REOPEN not in row["block_reason_codes"]
    assert result["allowed_judge_outcomes"] == ["INVEST", "WATCH", "ABSTAIN"]


def test_unclosed_reopen_reason_still_fails_closed():
    source_entry = {
        "canonical_open_research_requirements_after_b3": [],
        "additional_provider_read_required": False,
        "candidate_aware_reopen_provenance": "PASS",
    }
    model_input = {
        "candidate_order": ["NVDA"],
        "candidate_packets": [{"candidate_id": "NVDA"}],
        "material_claims": [
            {
                "claim_id": "NVDA_SUPPORTED_BASIS",
                "candidate_id": "NVDA",
                "materiality": "MATERIAL",
                "support_status": "SUPPORTED",
                "conflict_ids": [],
                "evidence_ids": ["NVDA_EVIDENCE"],
                "computed_value_ids": [],
            }
        ],
        "rebuttal_bundles": [
            {
                "candidate_id": "NVDA",
                "items": [],
                "research_reopen_required": True,
                "research_reopen_reason_codes": ["NEW_ACTIVE_REOPEN_REASON"],
            }
        ],
        "decision_context_uncertainties": [],
        "material_conflict_refs": [],
        "unresolved_dispute_refs": [],
        "event_outcome_constraints": {"canonical_b3_reopen_closed": True},
    }

    result = evaluate_positive_invest_eligibility(
        source_entry=source_entry,
        model_input=model_input,
    )
    row = result["candidate_results"][0]
    assert row["status"] != INVEST_ELIGIBLE
    assert BLOCK_RESEARCH_REOPEN in row["block_reason_codes"]
    assert result["allowed_judge_outcomes"] == ["WATCH", "ABSTAIN"]
