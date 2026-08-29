from __future__ import annotations

import json
from copy import deepcopy

import pytest

from aic.council.claim_promotion_authority import (
    DEFAULT_NORMALIZATION_PATH,
    DRIVE_AMENDMENT_ID,
    NORMALIZATION_HASH,
    NORMALIZATION_VERSION,
    ClaimPromotionAuthorityError,
    load_claim_promotion_normalization,
    validate_claim_promotion_normalization_payload,
)


def _payload() -> dict[str, object]:
    return json.loads(DEFAULT_NORMALIZATION_PATH.read_text(encoding="utf-8"))


def test_frozen_claim_promotion_authority_loads_exact_hash_and_amendment() -> None:
    authority = load_claim_promotion_normalization()
    assert authority.normalization_version == NORMALIZATION_VERSION
    assert authority.normalization_hash == NORMALIZATION_HASH
    assert authority.drive_amendment_id == DRIVE_AMENDMENT_ID
    assert authority.claim_kind_mapping == {
        "FACT_RESTATEMENT": "FACT",
        "INFERENCE": "INFERENCE",
        "PROCESS_FINDING": "INFERENCE",
    }


def test_claim_kind_mapping_drift_fails_closed() -> None:
    payload = deepcopy(_payload())
    payload["claim_kind_mapping"]["PROCESS_FINDING"] = "FACT"  # type: ignore[index]
    with pytest.raises(ClaimPromotionAuthorityError, match="claim_kind_mapping"):
        validate_claim_promotion_normalization_payload(payload)


def test_category_mapping_drift_fails_closed() -> None:
    payload = deepcopy(_payload())
    payload["category_mapping"] = {"source_field": "lane", "mode": "EXACT_VALUE"}
    with pytest.raises(ClaimPromotionAuthorityError, match="category_mapping"):
        validate_claim_promotion_normalization_payload(payload)


def test_normalization_hash_tamper_fails_closed() -> None:
    payload = deepcopy(_payload())
    payload["normalization_hash"] = "0" * 64
    with pytest.raises(ClaimPromotionAuthorityError, match="normalization_hash"):
        validate_claim_promotion_normalization_payload(payload)


def test_authority_surface_cannot_enable_local_or_legacy_claim_identity() -> None:
    for key in ("local_ref_persistence_allowed", "legacy_council_claim_id_allowed"):
        payload = deepcopy(_payload())
        payload[key] = True
        with pytest.raises(ClaimPromotionAuthorityError, match=key):
            validate_claim_promotion_normalization_payload(payload)
