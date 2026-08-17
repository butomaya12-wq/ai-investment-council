from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from aic.domain.canonical import Rfc3339DateTime, canonical_rfc3339_datetime
from aic.domain.errors import ContractValidationError
from aic.domain.schema_runtime import PROFILE_FORMAT_CHECKER, validate_resource

_PINNED_DATETIME_CORPUS_SHA256 = "e351b8ca0e97f7ee415fabea7a2b1f3dbf68eb369acf59dfce515b947d08820a"


def _official_datetime_corpus() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = Path(os.environ["AIC_OFFICIAL_DATETIME_CORPUS"])
    contents = source.read_bytes()
    assert sha256(contents).hexdigest() == _PINNED_DATETIME_CORPUS_SHA256
    groups = json.loads(contents)
    assert len(groups) == 1
    schema = groups[0]["schema"]
    vectors = groups[0]["tests"]
    assert len(vectors) == 33
    return schema, vectors


def _assert_rejected(value: str) -> None:
    assert not PROFILE_FORMAT_CHECKER.conforms(value, "date-time")
    with pytest.raises(ContractValidationError):
        validate_resource("UtcDateTime", value)


def _assert_accepted(value: str) -> None:
    assert PROFILE_FORMAT_CHECKER.conforms(value, "date-time")
    validate_resource("UtcDateTime", value)


def test_exact_pinned_official_datetime_corpus_matches_profile() -> None:
    schema, vectors = _official_datetime_corpus()
    validator = Draft202012Validator(schema, format_checker=PROFILE_FORMAT_CHECKER)
    actual = [not list(validator.iter_errors(vector["data"])) for vector in vectors]
    expected = [vector["valid"] for vector in vectors]
    assert actual == expected


def test_official_wrong_minute_and_hour_leap_seconds_are_rejected() -> None:
    _, vectors = _official_datetime_corpus()
    f1, f2 = vectors[13], vectors[14]
    assert (f1["data"], f1["valid"]) == ("1998-12-31T23:58:60Z", False)
    assert (f2["data"], f2["valid"]) == ("1998-12-31T22:59:60Z", False)
    _assert_rejected(f1["data"])
    _assert_rejected(f2["data"])


def test_official_utc_and_offset_leap_seconds_are_accepted() -> None:
    _, vectors = _official_datetime_corpus()
    utc_leap, minus_offset_leap = vectors[10], vectors[11]
    assert (utc_leap["data"], utc_leap["valid"]) == ("1998-12-31T23:59:60Z", True)
    assert (minus_offset_leap["data"], minus_offset_leap["valid"]) == (
        "1998-12-31T15:59:60.123-08:00",
        True,
    )
    _assert_accepted(utc_leap["data"])
    _assert_accepted(minus_offset_leap["data"])


def test_leap_position_is_validated_after_utc_normalization() -> None:
    utc_preceding_second = datetime(1998, 12, 31, 23, 59, 59, tzinfo=UTC)
    offset = timezone(timedelta(hours=5, minutes=45))
    local_preceding_second = utc_preceding_second.astimezone(offset)
    equivalent = local_preceding_second.strftime("%Y-%m-%dT%H:%M:") + "60+05:45"

    assert equivalent == "1999-01-01T05:44:60+05:45"
    _assert_accepted(equivalent)

    from aic.domain.schema_runtime import _parse_authorized_rfc3339_datetime

    parsed = _parse_authorized_rfc3339_datetime(equivalent)
    assert isinstance(parsed, Rfc3339DateTime)
    assert canonical_rfc3339_datetime(parsed) == "1998-12-31T23:59:60Z"


def test_non_month_end_utc_leap_position_is_rejected() -> None:
    _assert_rejected("1998-12-30T23:59:60Z")
