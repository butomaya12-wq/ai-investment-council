from datetime import UTC, datetime

import pytest

from aic.b2.models import InstrumentType, ProofStatus
from aic.b2.providers.sec import (
    SecNormalizationError,
    SecSecurityTypeReason,
    normalize_registered_security,
    normalize_submissions_recent,
    resolve_sec_registered_security,
    select_latest_operating_filing_at_cutoff,
)


def _submissions_payload():
    return {
        "filings": {
            "recent": {
                "accessionNumber": ["0001", "0002"],
                "form": ["10-K", "10-Q"],
                "acceptanceDateTime": ["20251031060126", "2026-07-30T20:00:00Z"],
                "filingDate": ["2025-10-31", "2026-07-30"],
                "reportDate": ["2025-09-27", "2026-06-27"],
                "primaryDocument": ["annual.htm", "quarterly.htm"],
            }
        }
    }


def test_normalize_submissions_and_select_latest_at_cutoff() -> None:
    records = normalize_submissions_recent(_submissions_payload())
    selected = select_latest_operating_filing_at_cutoff(
        records,
        decision_cutoff=datetime(2026, 8, 28, 15, 0, tzinfo=UTC),
    )
    assert selected is not None
    assert selected.form == "10-Q"
    assert selected.accession_number == "0002"


def test_submissions_parallel_array_mismatch_fails_closed() -> None:
    payload = _submissions_payload()
    payload["filings"]["recent"]["form"].append("8-K")
    with pytest.raises(SecNormalizationError, match="different lengths"):
        normalize_submissions_recent(payload)


def test_submissions_rejects_non_string_accession_provider_drift() -> None:
    payload = _submissions_payload()
    payload["filings"]["recent"]["accessionNumber"][0] = 1
    with pytest.raises(SecNormalizationError, match="JSON string"):
        normalize_submissions_recent(payload)


@pytest.mark.parametrize(
    ("symbol", "security_title"),
    [
        ("AAPL", "Common Stock, $0.00001 par value per share"),
        ("MSFT", "Common stock, $0.00000625 par value per share"),
        ("NVDA", "Common Stock, $0.001 par value per share"),
    ],
)
def test_official_sec_registered_common_stock_shape_is_proven(symbol: str, security_title: str) -> None:
    row = normalize_registered_security(
        {
            "symbol": symbol,
            "security_title": security_title,
            "exchange_name": "NASDAQ",
            "accession_number": "fixture-accession",
            "form": "10-K",
            "accepted_at": "2026-01-01T12:00:00Z",
            "source_uri": "https://www.sec.gov/Archives/edgar/data/example",
            "entity_shell_company": False,
        }
    )
    resolution = resolve_sec_registered_security(
        row=row,
        expected_symbol=symbol,
        proof_id=f"proof-{symbol}",
        retrieved_at=datetime(2026, 8, 28, 15, 55, tzinfo=UTC),
        decision_cutoff=datetime(2026, 8, 28, 15, 0, tzinfo=UTC),
    )
    assert resolution.reason_codes == ()
    assert resolution.proof.status is ProofStatus.PROVEN
    assert resolution.proof.instrument_type is InstrumentType.OPERATING_COMPANY_COMMON_STOCK


def test_future_filing_cannot_prove_security_type() -> None:
    row = normalize_registered_security(
        {
            "symbol": "AAPL",
            "security_title": "Common Stock",
            "exchange_name": "NASDAQ",
            "accession_number": "future",
            "form": "10-K",
            "accepted_at": "2026-09-01T12:00:00Z",
            "source_uri": "https://www.sec.gov/future",
            "entity_shell_company": False,
        }
    )
    resolution = resolve_sec_registered_security(
        row=row,
        expected_symbol="AAPL",
        proof_id="future-proof",
        retrieved_at=datetime(2026, 9, 2, tzinfo=UTC),
        decision_cutoff=datetime(2026, 8, 28, 15, 0, tzinfo=UTC),
    )
    assert SecSecurityTypeReason.FUTURE_FILING in resolution.reason_codes
    assert resolution.proof.status is ProofStatus.UNKNOWN


def test_shell_or_non_common_security_cannot_be_proven() -> None:
    row = normalize_registered_security(
        {
            "symbol": "TEST",
            "security_title": "Preferred Stock",
            "exchange_name": "NASDAQ",
            "accession_number": "test",
            "form": "10-K",
            "accepted_at": "2026-08-01T12:00:00Z",
            "source_uri": "https://www.sec.gov/test",
            "entity_shell_company": True,
        }
    )
    resolution = resolve_sec_registered_security(
        row=row,
        expected_symbol="TEST",
        proof_id="test-proof",
        retrieved_at=datetime(2026, 8, 28, 15, 55, tzinfo=UTC),
        decision_cutoff=datetime(2026, 8, 28, 15, 0, tzinfo=UTC),
    )
    assert SecSecurityTypeReason.SHELL_STATUS_NOT_FALSE in resolution.reason_codes
    assert SecSecurityTypeReason.SECURITY_TITLE_NOT_COMMON_STOCK in resolution.reason_codes
    assert resolution.proof.status is ProofStatus.UNKNOWN


def test_sec_shell_status_rejects_string_boolean_drift() -> None:
    with pytest.raises(SecNormalizationError, match="JSON boolean"):
        normalize_registered_security(
            {
                "symbol": "AAPL",
                "security_title": "Common Stock",
                "exchange_name": "NASDAQ",
                "accession_number": "test",
                "form": "10-K",
                "accepted_at": "2026-08-01T12:00:00Z",
                "source_uri": "https://www.sec.gov/test",
                "entity_shell_company": "false",
            }
        )


@pytest.mark.parametrize("bad_symbol", [123, None, " aapl ", "aapl"])
def test_registered_security_rejects_noncanonical_symbol_drift(bad_symbol) -> None:
    with pytest.raises(SecNormalizationError):
        normalize_registered_security(
            {
                "symbol": bad_symbol,
                "security_title": "Common Stock",
                "exchange_name": "NASDAQ",
                "accession_number": "test",
                "form": "10-K",
                "accepted_at": "2026-08-01T12:00:00Z",
                "source_uri": "https://www.sec.gov/test",
                "entity_shell_company": False,
            }
        )
