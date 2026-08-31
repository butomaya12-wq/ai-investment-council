from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from .alpaca_b5_normalization_v1 import (
    B5NormalizedAlpacaInputs,
    normalize_b5_alpaca_inputs,
)
from .b5_competition_artifacts_v1 import (
    B5CompetitionArtifactBundle,
    materialize_b5_competition_artifacts,
)
from .b5_competition_pipeline_v1 import (
    B5CompetitionProposal,
    run_b5_competition_options,
)
from .options_competition_v1 import CompetitionOptionsPolicy


@dataclass(frozen=True)
class B5RawAlpacaReadBundle:
    account_payload: Mapping[str, Any]
    positions_payload: Any
    open_orders_payload: Any
    option_contracts_payload: Any
    option_chain_payload: Any
    observed_at: datetime
    latest_completed_session_date: date
    account_receipt_id: str
    positions_receipt_id: str
    open_orders_receipt_id: str
    option_contracts_receipt_id: str
    option_chain_receipt_id: str


@dataclass(frozen=True)
class B5CompetitionRunResult:
    normalized_inputs: B5NormalizedAlpacaInputs
    proposal_result: B5CompetitionProposal
    artifacts: B5CompetitionArtifactBundle
    broker_writes: int = 0
    model_calls: int = 0
    approval_authority: bool = False
    execution_authority: bool = False


def run_b5_from_alpaca_reads(
    *,
    final_decision: Mapping[str, Any],
    underlying_symbol: str,
    raw_reads: B5RawAlpacaReadBundle,
    policy: CompetitionOptionsPolicy,
) -> B5CompetitionRunResult:
    """Execute the complete deterministic B5 path from five read-only Alpaca payloads.

    This orchestration function performs no network calls itself and has no broker-write,
    approval, or model authority. It binds the already captured provider reads to the
    current B4 FinalDecision, normalizes them, runs deterministic option selection and
    premium-risk sizing, then materializes the immutable B5 artifacts for B6.
    """

    normalized = normalize_b5_alpaca_inputs(
        account_payload=raw_reads.account_payload,
        positions_payload=raw_reads.positions_payload,
        open_orders_payload=raw_reads.open_orders_payload,
        option_contracts_payload=raw_reads.option_contracts_payload,
        option_chain_payload=raw_reads.option_chain_payload,
        underlying_symbol=underlying_symbol,
        observed_at=raw_reads.observed_at,
        latest_completed_session_date=raw_reads.latest_completed_session_date,
        policy=policy,
        account_receipt_id=raw_reads.account_receipt_id,
        positions_receipt_id=raw_reads.positions_receipt_id,
        open_orders_receipt_id=raw_reads.open_orders_receipt_id,
        option_contracts_receipt_id=raw_reads.option_contracts_receipt_id,
        option_chain_receipt_id=raw_reads.option_chain_receipt_id,
    )

    proposal = run_b5_competition_options(
        final_decision=final_decision,
        underlying_symbol=underlying_symbol,
        option_contracts=normalized.option_contracts,
        snapshot=normalized.snapshot,
        policy=policy,
    )

    artifacts = materialize_b5_competition_artifacts(
        final_decision=final_decision,
        raw_snapshot=normalized.snapshot,
        proposal_result=proposal,
        policy=policy,
        calculated_at=raw_reads.observed_at,
    )

    return B5CompetitionRunResult(
        normalized_inputs=normalized,
        proposal_result=proposal,
        artifacts=artifacts,
    )
