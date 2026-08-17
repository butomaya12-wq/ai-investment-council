from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Mapping

from pydantic import BaseModel

from .errors import CanonicalSerializationError

SERIALIZER_VERSION = "B1_CANONICAL_SERIALIZER_V1"


_RFC3339_DATETIME_RE = re.compile(
    r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"[Tt](?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]+))?"
    r"(?:(?P<z>[Zz])|(?P<offset_sign>[+-])(?P<offset_hour>[0-9]{2}):(?P<offset_minute>[0-9]{2}))"
)


class Rfc3339DateTime:
    """Immutable lossless representation for authorized RFC3339 exceptions.

    ``_utc_second`` is the ordinary UTC second for non-leap values and the
    UTC preceding ``:59`` second for leap values.  The normalized fields make
    dataclass equality semantic: input case, offset spelling, and insignificant
    fractional zeroes cannot affect identity.
    """

    __slots__ = ("_utc_second", "_fraction", "_leap_second")

    def __init__(self, utc_second: datetime, fraction: str, leap_second: bool = False) -> None:
        if utc_second.tzinfo is None or utc_second.utcoffset() is None:
            raise ValueError("Rfc3339DateTime requires an aware UTC second")
        utc_second = utc_second.astimezone(UTC)
        if utc_second.microsecond:
            raise ValueError("Rfc3339DateTime UTC second must not contain microseconds")
        if fraction != fraction.rstrip("0"):
            raise ValueError("Rfc3339DateTime fraction must not have trailing zeroes")
        if fraction and (not fraction.isascii() or not fraction.isdecimal()):
            raise ValueError("Rfc3339DateTime fraction must contain decimal digits")
        if leap_second and utc_second.second != 59:
            raise ValueError("leap Rfc3339DateTime must retain the preceding UTC :59 second")
        if not leap_second and len(fraction) <= 6:
            raise ValueError("non-leap Rfc3339DateTime requires sub-microsecond precision")
        object.__setattr__(self, "_utc_second", utc_second)
        object.__setattr__(self, "_fraction", fraction)
        object.__setattr__(self, "_leap_second", leap_second)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Rfc3339DateTime is immutable")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Rfc3339DateTime):
            return NotImplemented
        return (
            self._utc_second,
            self._fraction,
            self._leap_second,
        ) == (
            other._utc_second,
            other._fraction,
            other._leap_second,
        )

    def __hash__(self) -> int:
        return hash((self._utc_second, self._fraction, self._leap_second))

    def __repr__(self) -> str:
        return (
            "Rfc3339DateTime("
            f"_utc_second={self._utc_second!r}, "
            f"_fraction={self._fraction!r}, "
            f"_leap_second={self._leap_second!r})"
        )

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        from pydantic_core import core_schema

        return core_schema.is_instance_schema(cls)

    @property
    def is_leap_second(self) -> bool:
        return self._leap_second

    @property
    def fractional_digits(self) -> str:
        return self._fraction


def canonical_rfc3339_datetime(value: Rfc3339DateTime) -> str:
    """Render the one authorized UTC form for an exceptional temporal value."""
    if not isinstance(value, Rfc3339DateTime):
        raise CanonicalSerializationError("Rfc3339DateTime value required")
    second = 60 if value.is_leap_second else value._utc_second.second
    fraction = f".{value.fractional_digits}" if value.fractional_digits else ""
    return value._utc_second.strftime("%Y-%m-%dT%H:%M:") + f"{second:02d}{fraction}Z"


def parse_rfc3339_datetime(value: Any) -> datetime | Rfc3339DateTime:
    """Parse the activated strict RFC3339 date-time profile without precision loss."""
    if isinstance(value, Rfc3339DateTime):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime is forbidden")
        return value.astimezone(UTC)
    if not isinstance(value, str):
        raise ValueError("datetime must be aware datetime or RFC3339 string")

    match = _RFC3339_DATETIME_RE.fullmatch(value)
    if match is None:
        raise ValueError("invalid RFC3339 datetime")
    parts = match.groupdict()
    year = int(parts["year"])
    month = int(parts["month"])
    day = int(parts["day"])
    hour = int(parts["hour"])
    minute = int(parts["minute"])
    second = int(parts["second"])
    if hour > 23 or minute > 59 or second > 60:
        raise ValueError("invalid RFC3339 clock value")

    if parts["z"]:
        offset = UTC
    else:
        offset_hour = int(parts["offset_hour"])
        offset_minute = int(parts["offset_minute"])
        if offset_hour > 23 or offset_minute > 59:
            raise ValueError("invalid RFC3339 offset")
        offset_minutes = offset_hour * 60 + offset_minute
        if parts["offset_sign"] == "-":
            offset_minutes = -offset_minutes
        offset = timezone(timedelta(minutes=offset_minutes))

    fraction = (parts["fraction"] or "").rstrip("0")
    try:
        preceding_second = datetime(
            year,
            month,
            day,
            hour,
            minute,
            59 if second == 60 else second,
            tzinfo=offset,
        ).astimezone(UTC)
    except ValueError as exc:
        raise ValueError("invalid RFC3339 calendar date") from exc

    if second == 60:
        return Rfc3339DateTime(preceding_second, fraction, True)
    if len(fraction) > 6:
        return Rfc3339DateTime(preceding_second, fraction)
    return preceding_second.replace(microsecond=int(fraction.ljust(6, "0") or "0"))


def is_strict_rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True
    try:
        parse_rfc3339_datetime(value)
    except ValueError:
        return False
    return True


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
    if isinstance(value, Rfc3339DateTime):
        return canonical_rfc3339_datetime(value)
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
