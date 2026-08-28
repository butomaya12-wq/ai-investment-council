from __future__ import annotations

from datetime import UTC, datetime
from typing import Sequence

from pydantic import field_validator

from .models import B2Model, EvidenceItem, SnapshotManifest


class PointInTimeEvidenceSet(B2Model):
    decision_cutoff: datetime
    included_evidence_ids: tuple[str, ...]
    excluded_evidence_ids: tuple[str, ...]

    @field_validator("decision_cutoff")
    @classmethod
    def _aware_cutoff(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision_cutoff must be timezone-aware")
        return value.astimezone(UTC)


def authoritative_available_at(item: EvidenceItem) -> datetime:
    if item.published_at is not None:
        return item.published_at
    if item.observed_at is not None:
        return item.observed_at
    return item.as_of


def partition_evidence_at_cutoff(
    items: Sequence[EvidenceItem],
    *,
    decision_cutoff: datetime,
) -> PointInTimeEvidenceSet:
    if decision_cutoff.tzinfo is None or decision_cutoff.utcoffset() is None:
        raise ValueError("decision_cutoff must be timezone-aware")
    cutoff = decision_cutoff.astimezone(UTC)

    included: list[str] = []
    excluded: list[str] = []
    for item in items:
        computed_knowable = (
            authoritative_available_at(item) <= cutoff and item.as_of <= cutoff
        )
        if item.knowable_at_cutoff is not computed_knowable:
            raise ValueError(
                f"EvidenceItem {item.evidence_id} knowable_at_cutoff "
                "does not match its authoritative timestamps"
            )
        if computed_knowable:
            included.append(item.evidence_id)
        else:
            excluded.append(item.evidence_id)

    return PointInTimeEvidenceSet(
        decision_cutoff=cutoff,
        included_evidence_ids=tuple(included),
        excluded_evidence_ids=tuple(excluded),
    )


def assert_snapshot_point_in_time(manifest: SnapshotManifest) -> None:
    if manifest.created_at < manifest.decision_cutoff:
        raise ValueError("snapshot cannot be created before its decision_cutoff")

    bounded_times = {
        "asset_master_as_of": manifest.asset_master_as_of,
        "market_as_of": manifest.market_as_of,
        "sec_filing_cutoff": manifest.sec_filing_cutoff,
    }
    future = tuple(
        field
        for field, timestamp in bounded_times.items()
        if timestamp > manifest.decision_cutoff
    )
    if future:
        raise ValueError(
            "snapshot contains provider/as-of state after decision_cutoff: "
            + ", ".join(future)
        )
