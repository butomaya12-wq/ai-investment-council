from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aic.b2.providers.alpaca import (
    AlpacaNormalizationError,
    build_provider_read_receipt,
    normalize_asset,
    normalize_stock_bars,
)


def test_normalize_real_shape_asset_fixture() -> None:
    asset = normalize_asset(
        {
            "symbol": "AAPL",
            "asset_class": "us_equity",
            "exchange": "NASDAQ",
            "name": "Apple Inc. Common Stock",
            "status": "active",
            "tradable": True,
            "fractionable": True,
            "attributes": ["has_options"],
        }
    )
    assert asset.symbol == "AAPL"
    assert asset.tradable is True
    assert asset.exchange == "NASDAQ"


def test_normalize_asset_rejects_string_boolean_provider_drift() -> None:
    with pytest.raises(AlpacaNormalizationError, match="JSON boolean"):
        normalize_asset(
            {
                "symbol": "AAPL",
                "asset_class": "us_equity",
                "exchange": "NASDAQ",
                "status": "active",
                "tradable": "false",
                "fractionable": True,
            }
        )


@pytest.mark.parametrize("bad_symbol", [123, None, " aapl ", "aapl"])
def test_normalize_asset_rejects_noncanonical_symbol_provider_drift(bad_symbol) -> None:
    with pytest.raises(AlpacaNormalizationError):
        normalize_asset(
            {
                "symbol": bad_symbol,
                "asset_class": "us_equity",
                "exchange": "NASDAQ",
                "status": "active",
                "tradable": True,
                "fractionable": True,
            }
        )


def test_normalize_asset_rejects_non_string_exchange_provider_drift() -> None:
    with pytest.raises(AlpacaNormalizationError, match="JSON string"):
        normalize_asset(
            {
                "symbol": "AAPL",
                "asset_class": "us_equity",
                "exchange": 123,
                "status": "active",
                "tradable": True,
                "fractionable": True,
            }
        )


def test_normalize_stock_bars_converts_provider_float_to_decimal_boundary() -> None:
    normalized = normalize_stock_bars(
        {
            "bars": {
                "AAPL": [
                    {
                        "timestamp": "2026-08-27T04:00:00+00:00",
                        "close": 314.54,
                        "volume": 1062638.0,
                    },
                    {
                        "timestamp": "2026-08-28T04:00:00+00:00",
                        "close": 321.45,
                        "volume": 495703.0,
                    },
                ]
            }
        }
    )
    assert normalized["AAPL"][0].close == Decimal("314.54")
    assert normalized["AAPL"][1].volume == Decimal("495703")


def test_normalize_stock_bars_rejects_non_chronological_records() -> None:
    with pytest.raises(AlpacaNormalizationError, match="chronological"):
        normalize_stock_bars(
            {
                "bars": {
                    "AAPL": [
                        {"timestamp": "2026-08-28T04:00:00+00:00", "close": "321", "volume": "10"},
                        {"timestamp": "2026-08-27T04:00:00+00:00", "close": "314", "volume": "10"},
                    ]
                }
            }
        )


def test_provider_receipt_hashes_request_and_payload() -> None:
    receipt = build_provider_read_receipt(
        receipt_id="receipt-1",
        provider="ALPACA",
        endpoint_class="STOCK_BARS",
        request_started_at=datetime(2026, 8, 28, 15, 43, tzinfo=UTC),
        response_received_at=datetime(2026, 8, 28, 15, 44, tzinfo=UTC),
        request_parameters={"symbols": ["AAPL", "MSFT", "NVDA"], "timeframe": "1Day"},
        provider_payload={"bars": {"AAPL": []}},
        record_count=21,
        pagination_complete=True,
        http_status=200,
    )
    assert len(receipt.request_parameters_hash) == 64
    assert len(receipt.raw_payload_hash) == 64
    assert receipt.record_count == 21
