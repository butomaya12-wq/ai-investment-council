from datetime import UTC, datetime

import pytest

from aic.b2.models import EvidenceItem, SnapshotManifest, SnapshotStatus
from aic.b2.point_in_time import assert_snapshot_point_in_time, partition_evidence_at_cutoff


def _evidence(
    *,
    evidence_id: str,
    published_at: datetime,
    as_of: datetime,
    knowable_at_cutoff: bool,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        provider="SEC",
        source_type="SEC_FILING",
        source_uri="https://www.sec.gov/example",
        request_parameters_ref="req-1",
        entity_id="issuer-1",
        field_or_claim="registered_security",
        raw_value_or_record_ref="record-1",
        published_at=published_at,
        retrieved_at=datetime(2026, 8, 30, tzinfo=UTC),
        as_of=as_of,
        freshness_rule_id="SEC_ACCEPTANCE_TIME",
        knowable_at_cutoff=knowable_at_cutoff,
        authoritative_for=("security_type",),
        provider_read_receipt_id="receipt-1",
        raw_content_hash="a" * 64,
        normalization_version="SEC_V1",
    )


def test_historical_evidence_retrieved_later_remains_knowable() -> None:
    cutoff = datetime(2026, 8, 28, 15, 0, tzinfo=UTC)
    old = _evidence(
        evidence_id="old",
        published_at=datetime(2026, 8, 27, tzinfo=UTC),
        as_of=datetime(2026, 8, 27, tzinfo=UTC),
        knowable_at_cutoff=True,
    )
    future = _evidence(
        evidence_id="future",
        published_at=datetime(2026, 8, 29, tzinfo=UTC),
        as_of=datetime(2026, 8, 29, tzinfo=UTC),
        knowable_at_cutoff=False,
    )
    result = partition_evidence_at_cutoff((old, future), decision_cutoff=cutoff)
    assert result.included_evidence_ids == ("old",)
    assert result.excluded_evidence_ids == ("future",)


def test_incorrect_knowable_flag_fails_closed() -> None:
    item = _evidence(
        evidence_id="bad",
        published_at=datetime(2026, 8, 27, tzinfo=UTC),
        as_of=datetime(2026, 8, 27, tzinfo=UTC),
        knowable_at_cutoff=False,
    )
    with pytest.raises(ValueError, match="does not match"):
        partition_evidence_at_cutoff(
            (item,),
            decision_cutoff=datetime(2026, 8, 28, 15, 0, tzinfo=UTC),
        )


def test_snapshot_rejects_market_state_after_decision_cutoff() -> None:
    cutoff = datetime(2026, 8, 28, 15, 0, tzinfo=UTC)
    manifest = SnapshotManifest.build(
        snapshot_id="snapshot-1",
        created_at=datetime(2026, 8, 28, 15, 1, tzinfo=UTC),
        decision_cutoff=cutoff,
        mandate_version="mandate-v1",
        screening_policy_version="screen-v1",
        evidence_policy_version="evidence-v1",
        comparison_dimension_version="dimensions-v1",
        provider_receipt_ids=("receipt-1",),
        evidence_ids=("evidence-1",),
        computed_value_ids=("computed-1",),
        asset_master_as_of=cutoff,
        market_as_of=datetime(2026, 8, 28, 15, 0, 1, tzinfo=UTC),
        sec_filing_cutoff=cutoff,
        portfolio_snapshot_ref="portfolio-1",
        status=SnapshotStatus.COMPLETE,
    )
    with pytest.raises(ValueError, match="market_as_of"):
        assert_snapshot_point_in_time(manifest)
