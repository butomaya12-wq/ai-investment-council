from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from aic.risk.b5_competition_artifacts_v1 import (
    B5CompetitionArtifactError,
    materialize_b5_competition_artifacts,
)
from aic.risk.b5_competition_pipeline_v1 import (
    B5ReadOnlyRiskSnapshot,
    run_b5_competition_options,
)
from aic.risk.options_competition_v1 import (
    OptionContractCandidate,
    load_competition_options_policy,
)

NOW = datetime(2026, 8, 31, 14, 30, 0, tzinfo=timezone.utc)
POLICY = load_competition_options_policy(Path("config/event/competition_v1_options_policy.json"))


def final_decision(**overrides):
    payload = {
        "decision_id": "decision:artifact",
        "outcome": "INVEST",
        "primary_candidate_id": "candidate:NVDA",
        "evidence_status": "COMPLETE",
        "blocking_reason_codes": [],
        "final_decision_hash": "c" * 64,
        "mandate_version": "ALPACA_COMPETITION_V1_2026_08_29",
        "risk_result_id": None,
        "policy_refs": {
            "evidence_policy": {"policy_id": "EVIDENCE", "version": "v1", "policy_hash": "1" * 64},
            "council_policy": {"policy_id": "COUNCIL", "version": "v1", "policy_hash": "2" * 64},
        },
        "decision_lifecycle_policy_ref": {
            "policy_id": "ALPACA_2026_COMPETITION_DECISION_LIFECYCLE",
            "version": "ALPACA_COMPETITION_V1_2026_08_29",
            "policy_hash": "3" * 64,
        },
    }
    payload.update(overrides)
    return payload


def snapshot(**overrides):
    values = {
        "observed_at": NOW,
        "paper_account_id": "paper-account-1",
        "equity": Decimal("100000"),
        "same_underlying_committed_premium_at_risk": Decimal("900"),
        "aggregate_committed_long_option_premium_at_risk": Decimal("1900"),
        "remaining_after_equity_safety_reserve": Decimal("30000"),
        "options_buying_power_after_open_orders": Decimal("80000"),
        "account_trading_eligible": True,
        "unsupported_short_option_position": False,
        "conflicting_open_option_sell_order": False,
        "unvalued_open_option_exposure": False,
        "account_receipt_id": "receipt:account",
        "positions_receipt_id": "receipt:positions",
        "open_orders_receipt_id": "receipt:orders",
        "option_contracts_receipt_id": "receipt:contracts",
        "option_chain_receipt_id": "receipt:chain",
    }
    values.update(overrides)
    return B5ReadOnlyRiskSnapshot(**values)


def option():
    return OptionContractCandidate(
        symbol="NVDA261005C00220000",
        underlying_symbol="NVDA",
        contract_type="CALL",
        expiration_date=NOW.date() + timedelta(days=35),
        strike=Decimal("220"),
        exercise_style="AMERICAN",
        contract_size=100,
        delta=Decimal("0.50"),
        bid=Decimal("9.80"),
        ask=Decimal("10.00"),
        open_interest=500,
        open_interest_current=True,
        quote_timestamp=NOW - timedelta(seconds=5),
        status="ACTIVE",
        tradable=True,
        source_receipt_id="receipt:contracts::receipt:chain",
    )


def pass_proposal():
    return run_b5_competition_options(
        final_decision=final_decision(), underlying_symbol="NVDA",
        option_contracts=[option()], snapshot=snapshot(), policy=POLICY,
    )


def test_pass_materializes_exact_hash_bound_options_handoff():
    bundle = materialize_b5_competition_artifacts(
        final_decision=final_decision(), raw_snapshot=snapshot(),
        proposal_result=pass_proposal(), policy=POLICY, calculated_at=NOW,
    )
    assert bundle.risk_result.status == "PASS"
    accepted = bundle.accepted_proposal
    assert accepted is not None
    assert accepted.option_symbol == "NVDA261005C00220000"
    assert accepted.quantity == 2
    assert accepted.action == "BUY_TO_OPEN"
    assert accepted.order_type == "LIMIT"
    assert accepted.time_in_force == "DAY"
    assert accepted.environment == "PAPER"
    assert accepted.limit_price == Decimal("10.00")
    assert accepted.max_loss_usd == Decimal("2000.00")
    assert accepted.execution_authority is False
    assert accepted.approval_authority is False
    assert bundle.risk_result.execution_authority is False
    assert bundle.proposal.snapshot_hash == bundle.snapshot.snapshot_hash
    assert bundle.risk_result.proposal_hash == bundle.proposal.proposal_hash
    assert bundle.risk_result.portfolio_impact_hash == bundle.portfolio_impact.impact_hash
    assert accepted.risk_result_hash == bundle.risk_result.risk_result_hash
    assert accepted.policy_lineage_hash == bundle.snapshot.policy_lineage_hash
    impact = bundle.portfolio_impact
    assert impact.pre_same_underlying_premium_at_risk == Decimal("900")
    assert impact.post_same_underlying_premium_at_risk == Decimal("2900.00")
    assert impact.pre_aggregate_long_option_premium_at_risk == Decimal("1900")
    assert impact.post_aggregate_long_option_premium_at_risk == Decimal("3900.00")
    assert impact.remaining_safety_reserve_capacity_after_proposal == Decimal("28000.00")


def test_same_inputs_replay_to_same_ids_and_hashes():
    first = materialize_b5_competition_artifacts(
        final_decision=final_decision(), raw_snapshot=snapshot(),
        proposal_result=pass_proposal(), policy=POLICY, calculated_at=NOW,
    )
    second = materialize_b5_competition_artifacts(
        final_decision=final_decision(), raw_snapshot=snapshot(),
        proposal_result=pass_proposal(), policy=POLICY, calculated_at=NOW,
    )
    assert first == second


def test_non_pass_risk_never_creates_accepted_proposal():
    blocked_snapshot = snapshot(
        remaining_after_equity_safety_reserve=Decimal("999"),
        options_buying_power_after_open_orders=Decimal("999"),
    )
    result = run_b5_competition_options(
        final_decision=final_decision(), underlying_symbol="NVDA",
        option_contracts=[option()], snapshot=blocked_snapshot, policy=POLICY,
    )
    assert result.status == "BLOCK"
    bundle = materialize_b5_competition_artifacts(
        final_decision=final_decision(), raw_snapshot=blocked_snapshot,
        proposal_result=result, policy=POLICY, calculated_at=NOW,
    )
    assert bundle.risk_result.status == "BLOCK"
    assert bundle.accepted_proposal is None
    assert bundle.portfolio_impact.post_same_underlying_premium_at_risk is None


def test_proposal_policy_hash_mismatch_fails_before_handoff():
    mutated = replace(pass_proposal(), policy_hash="f" * 64)
    with pytest.raises(B5CompetitionArtifactError, match="policy hash mismatch"):
        materialize_b5_competition_artifacts(
            final_decision=final_decision(), raw_snapshot=snapshot(),
            proposal_result=mutated, policy=POLICY, calculated_at=NOW,
        )


def test_policy_lineage_mutation_changes_all_downstream_identity():
    baseline = materialize_b5_competition_artifacts(
        final_decision=final_decision(), raw_snapshot=snapshot(),
        proposal_result=pass_proposal(), policy=POLICY, calculated_at=NOW,
    )
    changed_decision = final_decision(policy_refs={
        "evidence_policy": {"policy_id": "EVIDENCE", "version": "v1", "policy_hash": "9" * 64},
        "council_policy": {"policy_id": "COUNCIL", "version": "v1", "policy_hash": "2" * 64},
    })
    changed = materialize_b5_competition_artifacts(
        final_decision=changed_decision, raw_snapshot=snapshot(),
        proposal_result=pass_proposal(), policy=POLICY, calculated_at=NOW,
    )
    assert changed.snapshot.policy_lineage_hash != baseline.snapshot.policy_lineage_hash
    assert changed.snapshot.snapshot_hash != baseline.snapshot.snapshot_hash
    assert changed.proposal.proposal_hash != baseline.proposal.proposal_hash
    assert changed.risk_result.risk_result_hash != baseline.risk_result.risk_result_hash
    assert changed.accepted_proposal is not None
    assert baseline.accepted_proposal is not None
    assert changed.accepted_proposal.accepted_hash != baseline.accepted_proposal.accepted_hash


def test_missing_final_decision_policy_lineage_fails_closed():
    decision = final_decision()
    decision.pop("policy_refs")
    with pytest.raises(B5CompetitionArtifactError, match="policy_refs missing"):
        materialize_b5_competition_artifacts(
            final_decision=decision, raw_snapshot=snapshot(),
            proposal_result=pass_proposal(), policy=POLICY, calculated_at=NOW,
        )
