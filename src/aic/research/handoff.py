from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Self

from pydantic import field_validator, model_validator

from aic.domain.canonical import canonical_sha256

from .model_policy import MODEL_POLICY_VERSION
from .models import B3Model
from .planner import PlannerContextItem, PlannerInputEnvelope
from .policy import RESEARCH_POLICY_VERSION


HANDOFF_VERSION = "B2_REAL_EVENT_HANDOFF_v0_1"
EXPECTED_TOP3 = ("NVDA", "MSFT", "META")
EXPECTED_METRICS = (
    "return_20s",
    "max_drawdown_20s",
    "adv_20s",
    "annual_revenue_growth",
    "annual_operating_margin",
)
_SEC_ARCHIVE_PREFIX = "https://www.sec.gov/Archives/edgar/data/"
_ACCESSION_RE = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_UTC_Z_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class B2HandoffMetric(B3Model):
    computed_value_id: str
    metric_id: str
    value: str
    unit: str

    @field_validator("computed_value_id", "metric_id", "value", "unit")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError("handoff metric string fields must be non-empty and trimmed")
        return value

    @field_validator("value")
    @classmethod
    def _decimal_string(cls, value: str) -> str:
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("handoff metric value must be a decimal string") from exc
        if not parsed.is_finite():
            raise ValueError("handoff metric value must be finite")
        return value


class B2HandoffCandidate(B3Model):
    symbol: str
    sec_accession: str
    sec_source_uri: str
    sec_evidence_id: str
    metrics: tuple[B2HandoffMetric, ...]

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        if not isinstance(value, str) or value != value.strip() or value != value.upper():
            raise ValueError("candidate symbol must be canonical uppercase")
        return value

    @field_validator("sec_accession")
    @classmethod
    def _accession(cls, value: str) -> str:
        if _ACCESSION_RE.fullmatch(value) is None:
            raise ValueError("sec_accession must use canonical SEC accession form")
        return value

    @field_validator("sec_source_uri")
    @classmethod
    def _sec_uri(cls, value: str) -> str:
        if not isinstance(value, str) or not value.startswith(_SEC_ARCHIVE_PREFIX):
            raise ValueError("sec_source_uri must be an official SEC archive URL")
        return value

    @model_validator(mode="after")
    def _metric_contract(self) -> Self:
        metric_ids = tuple(metric.metric_id for metric in self.metrics)
        if metric_ids != EXPECTED_METRICS:
            raise ValueError("candidate metrics must exactly match frozen B2 real-run dimension order")
        if len({metric.computed_value_id for metric in self.metrics}) != len(self.metrics):
            raise ValueError("computed_value_id values must be unique")
        expected_fragment = f"B2_{self.symbol}_"
        if any(not metric.computed_value_id.startswith(expected_fragment) for metric in self.metrics):
            raise ValueError("computed_value_id must bind the candidate symbol")
        return self


class B2RealEventHandoff(B3Model):
    handoff_version: str
    source_run_id: str
    source_evidence_index: str
    b2_decision_cutoff: str
    research_cutoff: str
    b2_snapshot_ref: str
    deep_comparison_ref: str
    top3: tuple[str, str, str]
    candidates: tuple[B2HandoffCandidate, B2HandoffCandidate, B2HandoffCandidate]
    handoff_hash: str

    @field_validator("b2_decision_cutoff", "research_cutoff")
    @classmethod
    def _utc_z_timestamp(cls, value: str) -> str:
        if _UTC_Z_RE.fullmatch(value) is None:
            raise ValueError("handoff cutoffs must use second-precision UTC Z form")
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @model_validator(mode="after")
    def _frozen_event_contract(self) -> Self:
        if self.handoff_version != HANDOFF_VERSION:
            raise ValueError("unexpected B2 event handoff version")
        if self.top3 != EXPECTED_TOP3:
            raise ValueError("B2 event handoff top3 drift")
        if tuple(candidate.symbol for candidate in self.candidates) != self.top3:
            raise ValueError("candidate order must exactly match top3")
        if len({candidate.sec_evidence_id for candidate in self.candidates}) != 3:
            raise ValueError("SEC evidence IDs must be unique")
        expected = canonical_sha256(self, exclude_fields=("handoff_hash",))
        if self.handoff_hash != expected:
            raise ValueError("handoff_hash does not bind the event handoff")
        return self

    def candidate(self, symbol: str) -> B2HandoffCandidate:
        for candidate in self.candidates:
            if candidate.symbol == symbol:
                return candidate
        raise KeyError(f"candidate not present in frozen B2 event handoff: {symbol}")


def load_real_event_handoff(path: str | Path) -> B2RealEventHandoff:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("unable to read B2 real event handoff JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("B2 real event handoff root must be a JSON object")
    return B2RealEventHandoff.model_validate(payload)


def _parse_utc_z(value: str) -> datetime:
    if _UTC_Z_RE.fullmatch(value) is None:
        raise ValueError("UTC Z timestamp required")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


_METRIC_CONTEXT: dict[str, tuple[str, str]] = {
    "return_20s": (
        "market_context",
        "Deterministic B2 trailing-return metric is already available; request its existing computed-value detail only if material.",
    ),
    "max_drawdown_20s": (
        "risk",
        "Deterministic B2 drawdown metric is already available; request its existing computed-value detail only if material.",
    ),
    "adv_20s": (
        "market_context",
        "Deterministic B2 average-dollar-volume metric is already available; request its existing computed-value detail only if material.",
    ),
    "annual_revenue_growth": (
        "growth_quality",
        "Deterministic B2 annual revenue-growth metric is already available; request its existing computed-value detail only if material.",
    ),
    "annual_operating_margin": (
        "financial_quality",
        "Deterministic B2 annual operating-margin metric is already available; request its existing computed-value detail only if material.",
    ),
}


def build_planner_input_from_handoff(
    handoff: B2RealEventHandoff,
    *,
    symbol: str,
) -> PlannerInputEnvelope:
    candidate = handoff.candidate(symbol)
    context_items: list[PlannerContextItem] = []
    for metric in candidate.metrics:
        category, description = _METRIC_CONTEXT[metric.metric_id]
        context_items.append(
            PlannerContextItem(
                item_id=metric.computed_value_id,
                category=category,
                evidence_status="ENOUGH",
                description=description,
                computed_value_refs=(metric.computed_value_id,),
            )
        )
    context_items.extend(
        (
            PlannerContextItem(
                item_id=candidate.sec_evidence_id,
                category="business_model",
                evidence_status="ENOUGH",
                description=(
                    "Official SEC filing evidence already establishes registered common-stock identity "
                    "and non-shell status before the B2 cutoff."
                ),
                evidence_refs=(candidate.sec_evidence_id,),
            ),
            PlannerContextItem(
                item_id=f"B3_GAP_{candidate.symbol}_QUALITATIVE",
                category="risk",
                evidence_status="MISSING",
                description=(
                    "B2 has no filing-section qualitative research for business context, risk factors, "
                    "management discussion, material filing developments, or bounded current-news context."
                ),
            ),
        )
    )
    return PlannerInputEnvelope(
        candidate_id=candidate.symbol,
        b2_snapshot_id=handoff.b2_snapshot_ref,
        deep_comparison_id=handoff.deep_comparison_ref,
        research_policy_version=RESEARCH_POLICY_VERSION,
        model_policy_version=MODEL_POLICY_VERSION,
        research_cutoff=_parse_utc_z(handoff.research_cutoff),
        context_items=tuple(context_items),
        allowed_source_handles=(
            handoff.source_run_id,
            candidate.sec_accession,
            candidate.sec_evidence_id,
            f"ALPACA_NEWS_WINDOW_{candidate.symbol}",
        ),
    )
