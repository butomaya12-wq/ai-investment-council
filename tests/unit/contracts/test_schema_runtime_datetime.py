from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from aic.domain.canonical import Rfc3339DateTime, canonical_bytes, canonical_rfc3339_datetime, canonical_sha256
from aic.domain.contracts import OWNER_ACCOUNT_V1, SCHEMA_REGISTRY_ENTRY_V1
from aic.domain.schema_runtime import validate_resource

_ORDINARY_BASELINE = json.loads(
    Path(__file__).with_name("b1_2_datetime_ordinary_preimplementation_baseline_v0_2.json").read_text(
        encoding="utf-8"
    )
)


def _registry_payload(value: str) -> dict[str, str]:
    return {
        "schema_name": "datetime-fixture",
        "schema_version": "v1",
        "schema_hash": "0" * 64,
        "generated_at": value,
    }


@pytest.mark.parametrize(
    "wire",
    tuple(_ORDINARY_BASELINE["ordinary_cases"]),
)
def test_ordinary_datetime_pairwise_baseline_binding_dump_and_canonical_bytes(wire: str) -> None:
    """Compare candidate outputs with literal pre-date-time baseline facts."""
    payload = _registry_payload(wire)
    baseline = _ORDINARY_BASELINE["ordinary_cases"][wire]
    expected_datetime = datetime.fromisoformat(baseline["typed_binding_iso8601"])
    candidate = SCHEMA_REGISTRY_ENTRY_V1.model_validate(payload)
    candidate_json = SCHEMA_REGISTRY_ENTRY_V1.model_validate_json(json.dumps(payload))

    assert type(candidate.generated_at) is datetime
    assert candidate.generated_at == candidate_json.generated_at == expected_datetime
    assert type(candidate.model_dump(mode="python")["generated_at"]) is datetime
    assert candidate.model_dump(mode="python")["generated_at"].isoformat() == baseline["typed_binding_iso8601"]
    assert candidate.model_dump(mode="json")["generated_at"] == baseline["model_dump_json_generated_at"]
    assert canonical_bytes(candidate) == base64.b64decode(baseline["canonical_bytes_base64"])
    assert canonical_sha256(candidate) == baseline["canonical_sha256"]


def test_lowercase_t_z_normalizes_to_uppercase_ordinary_semantic_identity() -> None:
    uppercase = SCHEMA_REGISTRY_ENTRY_V1.model_validate(_registry_payload("2024-01-01T00:00:00.1Z"))
    lowercase = SCHEMA_REGISTRY_ENTRY_V1.model_validate_json(
        json.dumps(_registry_payload("2024-01-01t00:00:00.1z"))
    )
    assert lowercase.generated_at == uppercase.generated_at
    assert canonical_bytes(lowercase) == canonical_bytes(uppercase)
    assert canonical_sha256(lowercase) == canonical_sha256(uppercase)


def test_terminal_newline_is_rejected_by_schema_and_binding() -> None:
    payload = _registry_payload("2024-01-01T00:00:00Z\n")
    with pytest.raises(ValidationError, match="date-time"):
        SCHEMA_REGISTRY_ENTRY_V1.model_validate(payload)
    with pytest.raises(ValidationError, match="date-time"):
        SCHEMA_REGISTRY_ENTRY_V1.model_validate_json(json.dumps(payload))


def test_exceptional_submicrosecond_identity_precision_and_model_dumps() -> None:
    first = SCHEMA_REGISTRY_ENTRY_V1.model_validate(_registry_payload("2024-01-01T00:00:00.1234567Z"))
    same = SCHEMA_REGISTRY_ENTRY_V1.model_validate_json(
        json.dumps(_registry_payload("2023-12-31T19:00:00.123456700-05:00"))
    )
    different = SCHEMA_REGISTRY_ENTRY_V1.model_validate(_registry_payload("2024-01-01T00:00:00.1234568Z"))
    tiny = SCHEMA_REGISTRY_ENTRY_V1.model_validate(_registry_payload("2024-01-01T00:00:00.0000001Z"))

    assert isinstance(first.generated_at, Rfc3339DateTime)
    assert first.generated_at == same.generated_at
    assert first.generated_at != different.generated_at
    assert canonical_rfc3339_datetime(first.generated_at) == "2024-01-01T00:00:00.1234567Z"
    assert canonical_rfc3339_datetime(tiny.generated_at) == "2024-01-01T00:00:00.0000001Z"
    assert first.model_dump(mode="python")["generated_at"] == first.generated_at
    assert first.model_dump(mode="json")["generated_at"] == "2024-01-01T00:00:00.1234567Z"
    assert canonical_bytes(first) != canonical_bytes(different)
    assert canonical_sha256(first) != canonical_sha256(different)


def test_exceptional_leap_second_stays_distinct_from_next_ordinary_second() -> None:
    leap = SCHEMA_REGISTRY_ENTRY_V1.model_validate(_registry_payload("1998-12-31T15:59:60.123-08:00"))
    ordinary = SCHEMA_REGISTRY_ENTRY_V1.model_validate(_registry_payload("1999-01-01T00:00:00Z"))

    assert isinstance(leap.generated_at, Rfc3339DateTime)
    assert canonical_rfc3339_datetime(leap.generated_at) == "1998-12-31T23:59:60.123Z"
    assert leap.generated_at != ordinary.generated_at
    assert canonical_bytes(leap) != canonical_bytes(ordinary)
    assert leap.model_dump(mode="json")["generated_at"] == "1998-12-31T23:59:60.123Z"


def test_exceptional_value_has_no_datetime_arithmetic_order_or_timezone_conversion() -> None:
    value = SCHEMA_REGISTRY_ENTRY_V1.model_validate(
        _registry_payload("2024-01-01T00:00:00.1234567Z")
    ).generated_at
    assert isinstance(value, Rfc3339DateTime)
    with pytest.raises(TypeError):
        _ = value + timedelta(seconds=1)
    with pytest.raises(TypeError):
        _ = value < value
    with pytest.raises(AttributeError):
        value.astimezone(UTC)


def test_schema_and_binding_accept_same_authorized_datetime_classes() -> None:
    for wire in (
        "2024-01-01T00:00:00Z",
        "2024-01-01t00:00:00z",
        "2024-01-01T00:00:00.1234567Z",
        "1998-12-31T23:59:60Z",
    ):
        validate_resource("UtcDateTime", wire)
        assert SCHEMA_REGISTRY_ENTRY_V1.model_validate(_registry_payload(wire)).generated_at is not None


def test_ordinary_owner_account_self_hash_matches_pre_candidate_datetime_canonical_bytes() -> None:
    baseline = _ORDINARY_BASELINE["owner_account_self_hash_case"]
    candidate = OWNER_ACCOUNT_V1.from_unhashed(**baseline["input"])

    assert type(candidate.created_at) is datetime
    assert candidate.created_at.isoformat() == baseline["typed_created_at_iso8601"]
    assert type(candidate.model_dump(mode="python")["created_at"]) is datetime
    assert candidate.model_dump(mode="json")["created_at"] == baseline["model_dump_json_created_at"]
    assert candidate.owner_account_hash == baseline["owner_account_hash"]
    assert canonical_bytes(candidate, exclude_fields=("owner_account_hash",)) == base64.b64decode(
        baseline["canonical_bytes_excluding_owner_account_hash_base64"]
    )
