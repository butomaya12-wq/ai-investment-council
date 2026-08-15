from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aic.domain.canonical import canonical_bytes, canonical_datetime, canonical_decimal, canonical_sha256
from aic.domain.errors import CanonicalSerializationError


def test_b1_vc_008_key_order_does_not_change_bytes_or_hash() -> None:
    a = {"z": "x", "a": 1, "nested": {"b": 2, "a": 1}}
    b = {"nested": {"a": 1, "b": 2}, "a": 1, "z": "x"}
    assert canonical_bytes(a) == canonical_bytes(b)
    assert canonical_sha256(a) == canonical_sha256(b)


def test_b1_vc_009_decimal_and_datetime_canonicalization_is_stable() -> None:
    assert canonical_decimal(Decimal("1.2300")) == "1.23"
    assert canonical_decimal(Decimal("1E+3")) == "1000"
    assert canonical_decimal(Decimal("-0.00")) == "0"
    dt_a = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
    dt_b = datetime(2026, 8, 15, 17, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    assert canonical_datetime(dt_a) == canonical_datetime(dt_b) == "2026-08-15T12:00:00.000000Z"


def test_b1_vc_007_binary_float_and_nonfinite_decimal_are_rejected() -> None:
    with pytest.raises(CanonicalSerializationError):
        canonical_bytes({"value": 1.25})
    with pytest.raises(CanonicalSerializationError):
        canonical_decimal(Decimal("NaN"))


def test_b1_vc_010_same_payload_hashes_identically_in_fresh_process() -> None:
    code = "from aic.domain.canonical import canonical_sha256; print(canonical_sha256({'b':2,'a':'x'}))"
    env = dict(os.environ)
    outputs = [
        subprocess.check_output([sys.executable, "-c", code], env=env, text=True).strip()
        for _ in range(2)
    ]
    assert outputs[0] == outputs[1] == canonical_sha256({"a": "x", "b": 2})
