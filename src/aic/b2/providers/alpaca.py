from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256

from ..analytics import DailyBar
from ..models import AssetRecord, ProviderReadReceipt


class AlpacaNormalizationError(ValueError):
    pass


def _decimal_from_provider(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise AlpacaNormalizationError(f"{field} must be numeric")
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
            raise AlpacaNormalizationError(f"{field} is not a decimal value") from exc
    else:
        raise AlpacaNormalizationError(f"{field} has unsupported numeric type")
    if not decimal_value.is_finite():
        raise AlpacaNormalizationError(f"{field} must be finite")
    return decimal_value


def _require_provider_bool(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise AlpacaNormalizationError(f"{field} must be a JSON boolean")
    return value


def _parse_provider_datetime(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AlpacaNormalizationError(f"{field} must be an ISO timestamp")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AlpacaNormalizationError(f"{field} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AlpacaNormalizationError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def normalize_asset(payload: Mapping[str, Any]) -> AssetRecord:
    required = ("symbol", "asset_class", "status", "tradable", "exchange")
    missing = tuple(name for name in required if name not in payload)
    if missing:
        raise AlpacaNormalizationError(f"asset payload missing fields: {missing}")
    fractionable = payload.get("fractionable")
    return AssetRecord(
        symbol=str(payload["symbol"]),
        asset_class=str(payload["asset_class"]),
        status=str(payload["status"]),
        tradable=_require_provider_bool(payload["tradable"], field="tradable"),
        exchange=str(payload["exchange"]),
        name=None if payload.get("name") is None else str(payload["name"]),
        fractionable=(
            None
            if fractionable is None
            else _require_provider_bool(fractionable, field="fractionable")
        ),
    )


def normalize_stock_bars(payload: Mapping[str, Any]) -> dict[str, tuple[DailyBar, ...]]:
    bars_object = payload.get("bars")
    if not isinstance(bars_object, Mapping):
        raise AlpacaNormalizationError("stock bars payload requires a bars object")

    normalized: dict[str, tuple[DailyBar, ...]] = {}
    for symbol, raw_bars in bars_object.items():
        if not isinstance(symbol, str) or not isinstance(raw_bars, Sequence):
            raise AlpacaNormalizationError("invalid bars symbol/records container")

        symbol_bars: list[DailyBar] = []
        prior_timestamp: datetime | None = None
        for raw in raw_bars:
            if not isinstance(raw, Mapping):
                raise AlpacaNormalizationError("bar record must be an object")
            timestamp = _parse_provider_datetime(raw.get("timestamp"), field="timestamp")
            if prior_timestamp is not None and timestamp <= prior_timestamp:
                raise AlpacaNormalizationError("bars must be strictly chronological")
            prior_timestamp = timestamp
            symbol_bars.append(
                DailyBar(
                    session_date=timestamp.date(),
                    close=_decimal_from_provider(raw.get("close"), field="close"),
                    volume=_decimal_from_provider(raw.get("volume"), field="volume"),
                )
            )

        normalized[symbol] = tuple(symbol_bars)
    return normalized


def build_provider_read_receipt(
    *,
    receipt_id: str,
    provider: str,
    endpoint_class: str,
    request_started_at: datetime,
    response_received_at: datetime,
    request_parameters: Mapping[str, Any],
    provider_payload: Mapping[str, Any],
    record_count: int,
    pagination_complete: bool,
    http_status: int | None = None,
    error: str | None = None,
) -> ProviderReadReceipt:
    return ProviderReadReceipt(
        provider_read_receipt_id=receipt_id,
        provider=provider,
        endpoint_class=endpoint_class,
        request_start=request_started_at,
        response_received_at=response_received_at,
        request_parameters_hash=canonical_sha256(request_parameters),
        pagination_complete=pagination_complete,
        raw_payload_hash=canonical_sha256(provider_payload),
        record_count=record_count,
        http_status=http_status,
        error=error,
    )
