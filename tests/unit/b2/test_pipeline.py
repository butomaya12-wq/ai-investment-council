from datetime import UTC, datetime
from decimal import Decimal

from aic.b2.models import InstrumentType, ProofStatus, SecurityTypeProof, SnapshotManifest, SnapshotStatus
from aic.b2.pipeline import B2RunStatus, run_b2_gate
from aic.b2.screening import CandidateScreenInput, MetricDirection, ScreeningPolicy


CUTOFF = datetime(2026, 8, 28, 15, 0, tzinfo=UTC)


def _snapshot(*, policy_version="screen-v1", status=SnapshotStatus.COMPLETE, market_as_of=CUTOFF):
    return SnapshotManifest.build(
        snapshot_id="snapshot-1",
        created_at=datetime(2026, 8, 28, 15, 1, tzinfo=UTC),
        decision_cutoff=CUTOFF,
        mandate_version="mandate-v1",
        screening_policy_version=policy_version,
        evidence_policy_version="evidence-v1",
        comparison_dimension_version="dimensions-v1",
        provider_receipt_ids=("receipt-1",),
        evidence_ids=("evidence-1",),
        computed_value_ids=("computed-1",),
        asset_master_as_of=CUTOFF,
        market_as_of=market_as_of,
        sec_filing_cutoff=CUTOFF,
        portfolio_snapshot_ref="portfolio-1",
        status=status,
    )


def _policy(*, weights=True):
    return ScreeningPolicy(
        policy_version="screen-v1",
        universe_ref="demo-v1",
        required_dimensions=("return", "drawdown"),
        metric_directions={
            "return": MetricDirection.HIGHER_IS_BETTER,
            "drawdown": MetricDirection.LOWER_IS_BETTER,
        },
        weights=(
            {"return": Decimal("0.5"), "drawdown": Decimal("0.5")}
            if weights
            else None
        ),
        shortlist_size=3,
        final_candidate_count=3,
    )


def _candidates():
    return (
        CandidateScreenInput(symbol="AAA", eligibility_proof_id="p1", dimensions={"return": Decimal("1"), "drawdown": Decimal("3")}),
        CandidateScreenInput(symbol="BBB", eligibility_proof_id="p2", dimensions={"return": Decimal("2"), "drawdown": Decimal("2")}),
        CandidateScreenInput(symbol="CCC", eligibility_proof_id="p3", dimensions={"return": Decimal("3"), "drawdown": Decimal("1")}),
    )


def _proof(proof_id, symbol, *, status=ProofStatus.PROVEN):
    return SecurityTypeProof(
        proof_id=proof_id,
        symbol=symbol,
        instrument_type=(InstrumentType.OPERATING_COMPANY_COMMON_STOCK if status is ProofStatus.PROVEN else InstrumentType.UNKNOWN),
        source_type="SEC_REGISTERED_SECURITY_12B",
        source_uri="https://www.sec.gov/example",
        source_record_ref="security-title",
        as_of=datetime(2026, 8, 1, tzinfo=UTC),
        retrieved_at=datetime(2026, 8, 28, 15, 2, tzinfo=UTC),
        snapshot_hash=(proof_id * 64)[:64],
        status=status,
    )


def _proofs():
    return (_proof("p1", "AAA"), _proof("p2", "BBB"), _proof("p3", "CCC"))


def _run(snapshot=None, policy=None, proofs=None):
    return run_b2_gate(
        snapshot=_snapshot() if snapshot is None else snapshot,
        policy=_policy() if policy is None else policy,
        candidates=_candidates(),
        security_type_proofs=_proofs() if proofs is None else proofs,
        comparison_id="comparison-1",
        mandate_version="mandate-v1",
        comparison_dimension_version="dimensions-v1",
        dimension_ids=("return", "drawdown"),
    )


def test_missing_weights_is_policy_stop_not_partial_success() -> None:
    result = _run(policy=_policy(weights=False))
    assert result.status is B2RunStatus.POLICY_STOP
    assert result.deep_comparison is None


def test_invalid_security_proof_blocks_before_screening() -> None:
    proofs = (_proof("p1", "AAA"), _proof("p2", "WRONG"), _proof("p3", "CCC"))
    result = _run(proofs=proofs)
    assert result.status is B2RunStatus.BLOCKED_SECURITY_PROOF
    assert result.reason_codes == ("INVALID_SECURITY_PROOF:BBB",)


def test_future_market_snapshot_blocks_run() -> None:
    result = _run(snapshot=_snapshot(market_as_of=datetime(2026, 8, 28, 15, 0, 1, tzinfo=UTC)))
    assert result.status is B2RunStatus.BLOCKED_SNAPSHOT


def test_policy_lineage_mismatch_blocks_run() -> None:
    result = _run(snapshot=_snapshot(policy_version="other"))
    assert result.status is B2RunStatus.BLOCKED_LINEAGE


def test_complete_gate_is_ready_for_b3_with_exact_three() -> None:
    result = _run()
    assert result.status is B2RunStatus.READY_FOR_B3
    assert result.deep_comparison is not None
    assert len(result.deep_comparison.candidate_symbols) == 3


def test_same_inputs_produce_same_input_hash_and_result() -> None:
    first = _run()
    second = _run()
    assert first == second
    assert first.input_hash == second.input_hash
