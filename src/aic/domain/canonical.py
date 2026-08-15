from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Mapping

from pydantic import BaseModel

from .errors import CanonicalSerializationError

SERIALIZER_VERSION = "B1_CANONICAL_SERIALIZER_V1"


def canonical_decimal(value: Decimal) -> str:
    if not isinstance(value, Decimal):
        raise CanonicalSerializationError("authoritative decimal must be decimal.Decimal")
    if not value.is_finite():
        raise CanonicalSerializationError("NaN/Infinity are forbidden")
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"-0", "+0", ""}:
        return "0"
    return text


def canonical_datetime(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise CanonicalSerializationError("datetime value required")
    if value.tzinfo is None or value.utcoffset() is None:
        raise CanonicalSerializationError("naive datetime is forbidden")
    utc = value.astimezone(UTC)
    # One fixed form: UTC, seconds + six fractional digits, literal Z.
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _to_canonical(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _to_canonical(value.model_dump(mode="python", exclude_none=False))
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, datetime):
        return canonical_datetime(value)
    if isinstance(value, Enum):
        return _to_canonical(value.value)
    if isinstance(value, float):
        raise CanonicalSerializationError("binary float is forbidden in canonical payloads")
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalSerializationError("canonical JSON object keys must be strings")
            out[key] = _to_canonical(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_to_canonical(item) for item in value]
    raise CanonicalSerializationError(f"unsupported canonical value type: {type(value)!r}")


def canonical_data(value: Any, *, exclude_fields: Iterable[str] = ()) -> Any:
    data = _to_canonical(value)
    if exclude_fields:
        if not isinstance(data, dict):
            raise CanonicalSerializationError("exclude_fields requires object payload")
        data = dict(data)
        for field in exclude_fields:
            data.pop(field, None)
    return data


def canonical_bytes(value: Any, *, exclude_fields: Iterable[str] = ()) -> bytes:
    data = canonical_data(value, exclude_fields=exclude_fields)
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any, *, exclude_fields: Iterable[str] = ()) -> str:
    return hashlib.sha256(canonical_bytes(value, exclude_fields=exclude_fields)).hexdigest()
