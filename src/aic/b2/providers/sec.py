from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Mapping, Sequence

from pydantic import field_validator

from aic.domain.canonical import canonical_sha256

from ..models import B2Model, InstrumentType, ProofStatus, SecurityTypeProof


class SecNormalizationError(ValueError):
    pass


class SecSecurityTypeReason(StrEnum):
    FUTURE_FILING = "FUTURE_FILING"
    FORM_NOT_OPERATING_COMPANY = "FORM_NOT_OPERATING_COMPANY"
    SHELL_STATUS_NOT_FALSE = "SHELL_STATUS_NOT_FALSE"
    TRADING_SYMBOL_MISMATCH = "TRADING_SYMBOL_MISMATCH"
    SECURITY_TITLE_NOT_COMMON_STOCK = "SECURITY_TITLE_NOT_COMMON_STOCK"
    EXCHANGE_MISSING = "EXCHANGE_MISSING"


class SecFilingRecord(B2Model):
    accession_number: str
    form: str
    accepted_at: datetime
    filing_date: date
    report_date: date | None = None
    primary_document: str

    @field_validator("accepted_at")
    @classmethod
    def _aware_accepted_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("accepted_at must be timezone-aware")
        return value.astimezone(UTC)


class SecRegisteredSecurity(B2Model):
    symbol: str
    security_title: str
    exchange_name: str
    accession_number: str
    form: str
    accepted_at: datetime
    source_uri: str
    source_record_ref: str = "dei:Security12bTitle|dei:TradingSymbol|dei:SecurityExchangeName"
    entity_shell_company: bool | None = None

    @field_validator("accepted_at")
    @classmethod
    def _aware_accepted_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("accepted_at must be timezone-aware")
        return value.astimezone(UTC)


class SecSecurityTypeResolution(B2Model):
    proof: SecurityTypeProof
    reason_codes: tuple[SecSecurityTypeReason, ...] = ()


_COMMON_STOCK_RE = re.compile(r"\bcommon\s+stock\b", re.IGNORECASE)
_OPERATING_COMPANY_FORMS = frozenset({"10-K", "10-Q"})


def _require_sec_string(value: Any, *, field: str, uppercase: bool = False) -> str:
    if type(value) is not str:
        raise SecNormalizationError(f"{field} must be a JSON string")
    if not value or value != value.strip():
        raise SecNormalizationError(f"{field} must be a non-empty trimmed string")
    if uppercase and value != value.upper():
        raise SecNormalizationError(f"{field} must be canonical uppercase")
    return value


def _parse_sec_datetime(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d{14}", text):
            parsed = datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        else:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError as exc:
                raise SecNormalizationError(f"{field} is not a valid SEC timestamp") from exc
    else:
        raise SecNormalizationError(f"{field} must be a timestamp")

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SecNormalizationError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _parse_sec_date(value: Any, *, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise SecNormalizationError(f"{field} must be a date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SecNormalizationError(f"{field} is not a valid ISO date") from exc


def normalize_submissions_recent(payload: Mapping[str, Any]) -> tuple[SecFilingRecord, ...]:
    filings = payload.get("filings")
    if not isinstance(filings, Mapping):
        raise SecNormalizationError("SEC submissions payload requires filings")
    recent = filings.get("recent")
    if not isinstance(recent, Mapping):
        raise SecNormalizationError("SEC submissions payload requires filings.recent")

    fields = (
        "accessionNumber",
        "form",
        "acceptanceDateTime",
        "filingDate",
        "reportDate",
        "primaryDocument",
    )
    arrays: dict[str, Sequence[Any]] = {}
    for field in fields:
        value = recent.get(field)
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise SecNormalizationError(f"filings.recent.{field} must be an array")
        arrays[field] = value

    lengths = {len(value) for value in arrays.values()}
    if len(lengths) != 1:
        raise SecNormalizationError("SEC submissions parallel arrays have different lengths")

    count = next(iter(lengths), 0)
    records: list[SecFilingRecord] = []
    for index in range(count):
        report_date = arrays["reportDate"][index]
        accepted_raw = _require_sec_string(arrays["acceptanceDateTime"][index], field="acceptanceDateTime")
        filing_date_raw = _require_sec_string(arrays["filingDate"][index], field="filingDate")
        records.append(
            SecFilingRecord(
                accession_number=_require_sec_string(arrays["accessionNumber"][index], field="accessionNumber"),
                form=_require_sec_string(arrays["form"][index], field="form"),
                accepted_at=_parse_sec_datetime(accepted_raw, field="acceptanceDateTime"),
                filing_date=_parse_sec_date(filing_date_raw, field="filingDate"),
                report_date=(
                    None
                    if report_date in (None, "")
                    else _parse_sec_date(_require_sec_string(report_date, field="reportDate"), field="reportDate")
                ),
                primary_document=_require_sec_string(arrays["primaryDocument"][index], field="primaryDocument"),
            )
        )
    return tuple(records)


def select_latest_operating_filing_at_cutoff(
    records: Sequence[SecFilingRecord],
    *,
    decision_cutoff: datetime,
) -> SecFilingRecord | None:
    cutoff = _parse_sec_datetime(decision_cutoff, field="decision_cutoff")
    eligible = [
        record
        for record in records
        if record.form in _OPERATING_COMPANY_FORMS and record.accepted_at <= cutoff
    ]
    return max(eligible, key=lambda record: record.accepted_at, default=None)


def normalize_registered_security(payload: Mapping[str, Any]) -> SecRegisteredSecurity:
    required = (
        "symbol",
        "security_title",
        "exchange_name",
        "accession_number",
        "form",
        "accepted_at",
        "source_uri",
    )
    missing = tuple(field for field in required if field not in payload)
    if missing:
        raise SecNormalizationError(f"registered-security payload missing fields: {missing}")

    shell_status = payload.get("entity_shell_company")
    if shell_status is not None and type(shell_status) is not bool:
        raise SecNormalizationError("entity_shell_company must be a JSON boolean or null")
    source_record_ref = payload.get(
        "source_record_ref",
        "dei:Security12bTitle|dei:TradingSymbol|dei:SecurityExchangeName",
    )

    return SecRegisteredSecurity(
        symbol=_require_sec_string(payload["symbol"], field="symbol", uppercase=True),
        security_title=_require_sec_string(payload["security_title"], field="security_title"),
        exchange_name=_require_sec_string(payload["exchange_name"], field="exchange_name"),
        accession_number=_require_sec_string(payload["accession_number"], field="accession_number"),
        form=_require_sec_string(payload["form"], field="form"),
        accepted_at=_parse_sec_datetime(
            _require_sec_string(payload["accepted_at"], field="accepted_at"),
            field="accepted_at",
        ),
        source_uri=_require_sec_string(payload["source_uri"], field="source_uri"),
        source_record_ref=_require_sec_string(source_record_ref, field="source_record_ref"),
        entity_shell_company=shell_status,
    )


def resolve_sec_registered_security(
    *,
    row: SecRegisteredSecurity,
    expected_symbol: str,
    proof_id: str,
    retrieved_at: datetime,
    decision_cutoff: datetime,
) -> SecSecurityTypeResolution:
    cutoff = _parse_sec_datetime(decision_cutoff, field="decision_cutoff")
    retrieved = _parse_sec_datetime(retrieved_at, field="retrieved_at")
    expected = _require_sec_string(expected_symbol, field="expected_symbol", uppercase=True)
    reasons: list[SecSecurityTypeReason] = []

    if row.accepted_at > cutoff:
        reasons.append(SecSecurityTypeReason.FUTURE_FILING)
    if row.form not in _OPERATING_COMPANY_FORMS:
        reasons.append(SecSecurityTypeReason.FORM_NOT_OPERATING_COMPANY)
    if row.entity_shell_company is not False:
        reasons.append(SecSecurityTypeReason.SHELL_STATUS_NOT_FALSE)
    if row.symbol != expected:
        reasons.append(SecSecurityTypeReason.TRADING_SYMBOL_MISMATCH)
    if _COMMON_STOCK_RE.search(row.security_title) is None:
        reasons.append(SecSecurityTypeReason.SECURITY_TITLE_NOT_COMMON_STOCK)
    if not row.exchange_name:
        reasons.append(SecSecurityTypeReason.EXCHANGE_MISSING)

    proven = not reasons
    proof = SecurityTypeProof(
        proof_id=proof_id,
        symbol=expected,
        instrument_type=(
            InstrumentType.OPERATING_COMPANY_COMMON_STOCK
            if proven
            else InstrumentType.UNKNOWN
        ),
        source_type="SEC_REGISTERED_SECURITY_12B",
        source_uri=row.source_uri,
        source_record_ref=row.source_record_ref,
        as_of=row.accepted_at,
        retrieved_at=retrieved,
        snapshot_hash=canonical_sha256(row),
        status=ProofStatus.PROVEN if proven else ProofStatus.UNKNOWN,
    )
    return SecSecurityTypeResolution(proof=proof, reason_codes=tuple(reasons))
