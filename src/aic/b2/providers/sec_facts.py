from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping, Sequence, Self

from pydantic import field_validator, model_validator

from aic.domain.canonical import canonical_sha256

from ..models import B2Model
from .sec import SecFilingRecord, SecNormalizationError


class SecFactSelectionStatus(StrEnum):
    SELECTED = "SELECTED"
    INCOMPLETE = "INCOMPLETE"
    CONFLICT = "CONFLICT"


class SecFactPeriodType(StrEnum):
    INSTANT = "INSTANT"
    DURATION = "DURATION"


class SecCompanyFact(B2Model):
    fact_id: str
    taxonomy: str
    concept: str
    unit: str
    value: Decimal
    period_start: date | None = None
    period_end: date
    filed_at: date
    accession_no: str
    form: str
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    frame: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _reject_binary_float(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise TypeError("binary float is forbidden inside authoritative SecCompanyFact")
        return value


class SecFactSelectionPolicy(B2Model):
    policy_version: str
    concept_precedence: tuple[str, ...]
    required_unit: str
    allowed_forms: tuple[str, ...]
    period_type: SecFactPeriodType
    allowed_fiscal_periods: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_policy(self) -> Self:
        if not self.concept_precedence:
            raise ValueError("concept_precedence must not be empty")
        if len(set(self.concept_precedence)) != len(self.concept_precedence):
            raise ValueError("concept_precedence must be unique")
        if any(":" not in item for item in self.concept_precedence):
            raise ValueError("concept_precedence entries must be taxonomy:concept")
        if not self.required_unit:
            raise ValueError("required_unit must not be empty")
        if not self.allowed_forms:
            raise ValueError("allowed_forms must not be empty")
        return self


class SecFactSelectionResult(B2Model):
    policy_version: str
    status: SecFactSelectionStatus
    selected_fact: SecCompanyFact | None = None
    conflict_fact_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


def _require_fact_string(value: Any, *, field: str) -> str:
    if type(value) is not str:
        raise SecNormalizationError(f"{field} must be a JSON string")
    if not value or value != value.strip():
        raise SecNormalizationError(f"{field} must be a non-empty trimmed string")
    return value


def _optional_fact_string(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    return _require_fact_string(value, field=field)


def _optional_fiscal_year(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise SecNormalizationError("fy must be a JSON integer or null")
    return value


def _decimal_from_sec(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise SecNormalizationError(f"{field} must be numeric")
    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, int):
        decimal_value = Decimal(value)
    elif isinstance(value, float):
        decimal_value = Decimal(str(value))
    elif isinstance(value, str):
        try:
            decimal_value = Decimal(value)
        except Exception as exc:
            raise SecNormalizationError(f"{field} is not a decimal value") from exc
    else:
        raise SecNormalizationError(f"{field} has unsupported numeric type")
    if not decimal_value.is_finite():
        raise SecNormalizationError(f"{field} must be finite")
    return decimal_value


def _parse_date(value: Any, *, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise SecNormalizationError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SecNormalizationError(f"{field} is not a valid ISO date") from exc


def _qualified(taxonomy: str, concept: str) -> str:
    return f"{taxonomy}:{concept}"


def normalize_companyfacts(
    payload: Mapping[str, Any],
    *,
    concept_refs: Sequence[str],
) -> tuple[SecCompanyFact, ...]:
    facts_root = payload.get("facts")
    if not isinstance(facts_root, Mapping):
        raise SecNormalizationError("SEC companyfacts payload requires facts")

    normalized: list[SecCompanyFact] = []
    for qualified in concept_refs:
        if type(qualified) is not str or ":" not in qualified:
            raise SecNormalizationError("concept_refs entries must be taxonomy:concept strings")
        taxonomy, concept = qualified.split(":", 1)
        taxonomy_node = facts_root.get(taxonomy)
        if taxonomy_node is None:
            continue
        if not isinstance(taxonomy_node, Mapping):
            raise SecNormalizationError(f"facts.{taxonomy} must be an object")
        concept_node = taxonomy_node.get(concept)
        if concept_node is None:
            continue
        if not isinstance(concept_node, Mapping):
            raise SecNormalizationError(f"facts.{taxonomy}.{concept} must be an object")
        units = concept_node.get("units")
        if not isinstance(units, Mapping):
            raise SecNormalizationError(f"facts.{taxonomy}.{concept}.units must be an object")

        for unit, rows in units.items():
            unit_name = _require_fact_string(unit, field="companyfacts unit")
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
                raise SecNormalizationError("companyfacts unit records must be an array")
            for raw in rows:
                if not isinstance(raw, Mapping):
                    raise SecNormalizationError("companyfacts fact record must be an object")
                required = ("val", "end", "filed", "accn", "form")
                missing = tuple(field for field in required if field not in raw)
                if missing:
                    raise SecNormalizationError(f"companyfacts fact missing fields: {missing}")

                start_raw = raw.get("start")
                period_start = (
                    None if start_raw in (None, "") else _parse_date(_require_fact_string(start_raw, field="start"), field="start")
                )
                period_end = _parse_date(_require_fact_string(raw["end"], field="end"), field="end")
                filed_at = _parse_date(_require_fact_string(raw["filed"], field="filed"), field="filed")
                value = _decimal_from_sec(raw["val"], field="val")
                accession_no = _require_fact_string(raw["accn"], field="accn")
                form = _require_fact_string(raw["form"], field="form")
                fiscal_year = _optional_fiscal_year(raw.get("fy"))
                fiscal_period = _optional_fact_string(raw.get("fp"), field="fp")
                frame = _optional_fact_string(raw.get("frame"), field="frame")
                fact_identity = {
                    "taxonomy": taxonomy,
                    "concept": concept,
                    "unit": unit_name,
                    "value": str(value),
                    "period_start": None if period_start is None else period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "filed_at": filed_at.isoformat(),
                    "accession_no": accession_no,
                    "form": form,
                    "fiscal_year": fiscal_year,
                    "fiscal_period": fiscal_period,
                    "frame": frame,
                }
                normalized.append(
                    SecCompanyFact(
                        fact_id=canonical_sha256(fact_identity),
                        taxonomy=taxonomy,
                        concept=concept,
                        unit=unit_name,
                        value=value,
                        period_start=period_start,
                        period_end=period_end,
                        filed_at=filed_at,
                        accession_no=accession_no,
                        form=form,
                        fiscal_year=fiscal_year,
                        fiscal_period=fiscal_period,
                        frame=frame,
                    )
                )
    return tuple(normalized)


def select_company_fact_at_cutoff(
    facts: Sequence[SecCompanyFact],
    filings: Sequence[SecFilingRecord],
    *,
    policy: SecFactSelectionPolicy,
    decision_cutoff: datetime,
) -> SecFactSelectionResult:
    if decision_cutoff.tzinfo is None or decision_cutoff.utcoffset() is None:
        raise ValueError("decision_cutoff must be timezone-aware")
    cutoff = decision_cutoff.astimezone(UTC)
    filing_by_accession = {filing.accession_number: filing for filing in filings}
    precedence = {name: index for index, name in enumerate(policy.concept_precedence)}

    eligible: list[SecCompanyFact] = []
    for fact in facts:
        filing = filing_by_accession.get(fact.accession_no)
        if filing is None:
            continue
        if filing.accepted_at > cutoff:
            continue
        if fact.period_end > cutoff.date():
            continue
        if fact.form != filing.form:
            continue
        qualified = _qualified(fact.taxonomy, fact.concept)
        if qualified not in precedence:
            continue
        if fact.unit != policy.required_unit or fact.form not in policy.allowed_forms:
            continue
        if policy.allowed_fiscal_periods and fact.fiscal_period not in policy.allowed_fiscal_periods:
            continue
        if policy.period_type is SecFactPeriodType.INSTANT and fact.period_start is not None:
            continue
        if policy.period_type is SecFactPeriodType.DURATION and fact.period_start is None:
            continue
        eligible.append(fact)

    if not eligible:
        return SecFactSelectionResult(
            policy_version=policy.policy_version,
            status=SecFactSelectionStatus.INCOMPLETE,
            reason_codes=("NO_ELIGIBLE_FACT_AT_CUTOFF",),
        )

    latest_period_end = max(fact.period_end for fact in eligible)
    same_period = [fact for fact in eligible if fact.period_end == latest_period_end]
    best_precedence = min(
        precedence[_qualified(fact.taxonomy, fact.concept)] for fact in same_period
    )
    top = [
        fact
        for fact in same_period
        if precedence[_qualified(fact.taxonomy, fact.concept)] == best_precedence
    ]

    distinct_values = {
        (fact.value, fact.unit, fact.period_start, fact.period_end)
        for fact in top
    }
    if len(distinct_values) > 1:
        return SecFactSelectionResult(
            policy_version=policy.policy_version,
            status=SecFactSelectionStatus.CONFLICT,
            conflict_fact_ids=tuple(sorted(fact.fact_id for fact in top)),
            reason_codes=("AMBIGUOUS_TOP_PRECEDENCE_FACT",),
        )

    selected = max(
        top,
        key=lambda fact: filing_by_accession[fact.accession_no].accepted_at,
    )
    return SecFactSelectionResult(
        policy_version=policy.policy_version,
        status=SecFactSelectionStatus.SELECTED,
        selected_fact=selected,
    )
