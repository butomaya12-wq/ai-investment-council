from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aic.b2.eligibility import EligibilityProof, EligibilityReason, evaluate_asset_eligibility
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
        proof_id="sec-p1",
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


def test_eligibility_proof_binds_asset_sec_proof_and_policy_flags() -> None:
    proof = EligibilityProof.build(
        eligibility_proof_id="elig-p1",
        asset=_asset(),
        security_type_proof=_proof(),
        allowed_exchanges={"NYSE", "NASDAQ"},
        evidence_complete=True,
        mandate_allowed=True,
    )
    assert proof.eligible is True
    assert proof.reason_codes == ()
    assert proof.allowed_exchanges == ("NASDAQ", "NYSE")
    assert len(proof.eligibility_proof_hash) == 64


def test_inactive_asset_builds_explicit_ineligible_proof() -> None:
    proof = EligibilityProof.build(
        eligibility_proof_id="elig-p1",
        asset=_asset(status="inactive"),
        security_type_proof=_proof(),
        allowed_exchanges={"NASDAQ"},
        evidence_complete=True,
        mandate_allowed=True,
    )
    assert proof.eligible is False
    assert EligibilityReason.INACTIVE in proof.reason_codes


def test_tampered_eligibility_hash_is_rejected() -> None:
    proof = EligibilityProof.build(
        eligibility_proof_id="elig-p1",
        asset=_asset(),
        security_type_proof=_proof(),
        allowed_exchanges={"NASDAQ"},
        evidence_complete=True,
        mandate_allowed=True,
    )
    payload = proof.model_dump(mode="python")
    payload["eligibility_proof_hash"] = "0" * 64
    with pytest.raises(ValidationError, match="eligibility_proof_hash"):
        EligibilityProof.model_validate(payload)


def test_tampered_eligibility_decision_is_rejected() -> None:
    proof = EligibilityProof.build(
        eligibility_proof_id="elig-p1",
        asset=_asset(),
        security_type_proof=_proof(),
        allowed_exchanges={"NASDAQ"},
        evidence_complete=True,
        mandate_allowed=True,
    )
    payload = proof.model_dump(mode="python")
    payload["eligible"] = False
    payload["reason_codes"] = (EligibilityReason.INACTIVE,)
    payload["eligibility_proof_hash"] = "0" * 64
    with pytest.raises(ValidationError, match="decision"):
        EligibilityProof.model_validate(payload)
