from datetime import UTC, datetime

from aic.b2.eligibility import EligibilityReason, evaluate_asset_eligibility
from aic.b2.models import AssetRecord, InstrumentType, ProofStatus, SecurityTypeProof


def _asset(**overrides):
    data = dict(
        symbol="AAPL",
        asset_class="us_equity",
        status="active",
        tradable=True,
        exchange="NASDAQ",
    )
    data.update(overrides)
    return AssetRecord(**data)


def _proof(**overrides):
    data = dict(
        proof_id="p1",
        symbol="AAPL",
        instrument_type=InstrumentType.OPERATING_COMPANY_COMMON_STOCK,
        source_type="SEC",
        source_uri="https://example.invalid",
        source_record_ref="record-1",
        as_of=datetime(2026, 8, 28, 15, 0, tzinfo=UTC),
        retrieved_at=datetime(2026, 8, 28, 15, 1, tzinfo=UTC),
        snapshot_hash="a" * 64,
        status=ProofStatus.PROVEN,
    )
    data.update(overrides)
    return SecurityTypeProof(**data)


def test_eligible_common_stock_passes_all_hard_gates() -> None:
    result = evaluate_asset_eligibility(
        asset=_asset(),
        security_type_proof=_proof(),
        allowed_exchanges={"NASDAQ", "NYSE"},
        evidence_complete=True,
        mandate_allowed=True,
    )
    assert result.eligible
    assert result.reason_codes == ()


def test_us_equity_etf_is_not_accepted_as_common_stock() -> None:
    result = evaluate_asset_eligibility(
        asset=_asset(),
        security_type_proof=_proof(instrument_type=InstrumentType.ETF),
        allowed_exchanges={"NASDAQ"},
        evidence_complete=True,
        mandate_allowed=True,
    )
    assert not result.eligible
    assert EligibilityReason.NOT_OPERATING_COMPANY_COMMON_STOCK in result.reason_codes


def test_unproven_security_type_fails_closed() -> None:
    result = evaluate_asset_eligibility(
        asset=_asset(),
        security_type_proof=_proof(status=ProofStatus.UNKNOWN),
        allowed_exchanges={"NASDAQ"},
        evidence_complete=True,
        mandate_allowed=True,
    )
    assert not result.eligible
    assert EligibilityReason.SECURITY_TYPE_UNPROVEN in result.reason_codes
