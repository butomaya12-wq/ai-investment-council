from __future__ import annotations

from pathlib import Path
import json

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from .product_presenter import build_product_state


PACKAGE = Path(__file__).resolve().parent

templates = Jinja2Templates(
    directory=str(PACKAGE / "templates")
)

app = FastAPI(
    title="AI Investment Council — Stock Intelligence",
    docs_url=None,
    redoc_url=None,
)

app.mount(
    "/static",
    StaticFiles(directory=str(PACKAGE / "static")),
    name="static",
)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.get("/api/state")
def state() -> JSONResponse:
    return JSONResponse(build_product_state())


@app.post("/api/market-refresh-preflight")
def market_refresh_preflight() -> JSONResponse:
    return JSONResponse(
        {
            "status": "READ_AUTHORITY_REQUIRED",
            "dispatched": False,
            "alpaca_reads": 0,
            "provider_calls": 0,
            "model_calls": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "message": (
                "Live Alpaca market data was not requested. "
                "A separate explicit read-only authorization "
                "is required before loading current price "
                "and candles."
            ),
        }
    )

BARS_PATH = (
    PACKAGE
    / "data"
    / "historical_bars_snapshot_v1.json"
)


@app.get("/api/bars/{symbol}")
def historical_bars(symbol: str) -> JSONResponse:
    symbol = symbol.upper().strip()

    payload = json.loads(
        BARS_PATH.read_text(encoding="utf-8")
    )

    bars_root = payload.get("bars")

    if not isinstance(bars_root, dict):
        raise HTTPException(
            status_code=500,
            detail="Persisted bars container invalid",
        )

    raw = bars_root.get(symbol)

    if not isinstance(raw, list):
        return JSONResponse(
            {
                "symbol": symbol,
                "available": False,
                "bars": [],
                "source": "PERSISTED_REAL_ALPACA_EVIDENCE",
                "provider_calls": 0,
            }
        )

    bars = []

    for item in raw:
        if not isinstance(item, dict):
            continue

        required = ("t", "o", "h", "l", "c", "v")

        if not all(key in item for key in required):
            continue

        bars.append(
            {
                "timestamp": item["t"],
                "open": item["o"],
                "high": item["h"],
                "low": item["l"],
                "close": item["c"],
                "volume": item["v"],
                "vwap": item.get("vw"),
                "trade_count": item.get("n"),
            }
        )

    return JSONResponse(
        {
            "symbol": symbol,
            "available": bool(bars),
            "bar_count": len(bars),
            "bars": bars,
            "source": "PERSISTED_REAL_ALPACA_EVIDENCE",
            "provider_calls": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
        }
    )


@app.get("/portfolio", response_class=HTMLResponse)
def portfolio_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="product_surface.html",
        context={
            "surface": "portfolio",
            "title": "Portfolio",
        },
    )


@app.get("/trades", response_class=HTMLResponse)
def trades_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="product_surface.html",
        context={
            "surface": "trades",
            "title": "Trades",
        },
    )


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="product_surface.html",
        context={
            "surface": "history",
            "title": "History",
        },
    )


BROKER_SNAPSHOT_PATH = (
    PACKAGE
    / "data"
    / "broker_audit_snapshot_v1.json"
)


@app.get("/api/broker-snapshot")
def broker_snapshot() -> JSONResponse:
    payload = json.loads(
        BROKER_SNAPSHOT_PATH.read_text(
            encoding="utf-8"
        )
    )

    return JSONResponse(payload)


# ============================================================
# MARKET JURY V5 — LIVE READ-ONLY CORE
# ============================================================

from fastapi import HTTPException as _LiveHTTPException

import http.client as _live_http_client
import threading as _live_threading
import time as _live_time

from datetime import (
    datetime as _live_datetime,
    timezone as _live_timezone,
)

from urllib.parse import urlencode as _live_urlencode


_LIVE_PAPER_HOST = "paper-api.alpaca.markets"
_LIVE_DATA_HOST = "data.alpaca.markets"

_LIVE_ALLOWED_HOSTS = {
    _LIVE_PAPER_HOST,
    _LIVE_DATA_HOST,
}

_LIVE_CREDENTIALS_PATH = (
    Path.home()
    / ".config"
    / "market-jury"
    / "alpaca-paper-credentials.json"
)

_LIVE_MARKET_TTL_SECONDS = 2.5
_LIVE_ACCOUNT_TTL_SECONDS = 25.0

_LIVE_MARKET_LOCK = _live_threading.Lock()
_LIVE_ACCOUNT_LOCK = _live_threading.Lock()

_LIVE_MARKET_CACHE = {
    "monotonic": 0.0,
    "payload": None,
}

_LIVE_ACCOUNT_CACHE = {
    "monotonic": 0.0,
    "payload": None,
}

_LIVE_READ_COUNTS = {
    "market_gets": 0,
    "account_gets": 0,
    "positions_gets": 0,
}


def _live_now_utc() -> str:
    return _live_datetime.now(
        _live_timezone.utc
    ).isoformat()


def _live_credentials() -> tuple[str, str]:
    path = _LIVE_CREDENTIALS_PATH

    if not path.is_file():
        raise _LiveHTTPException(
            status_code=503,
            detail="Paper credentials are not configured.",
        )

    mode = path.stat().st_mode & 0o777

    if mode != 0o600:
        raise _LiveHTTPException(
            status_code=503,
            detail="Paper credential permissions are unsafe.",
        )

    try:
        value = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        raise _LiveHTTPException(
            status_code=503,
            detail="Paper credential file is invalid.",
        )

    if value.get("provider") != "ALPACA":
        raise _LiveHTTPException(
            status_code=503,
            detail="Unexpected credential provider.",
        )

    if value.get("environment") != "PAPER":
        raise _LiveHTTPException(
            status_code=503,
            detail="Only Alpaca PAPER credentials are permitted.",
        )

    key_id = value.get("key_id")
    secret = value.get("secret_key")

    if not isinstance(key_id, str) or not key_id:
        raise _LiveHTTPException(
            status_code=503,
            detail="Paper key ID missing.",
        )

    if not isinstance(secret, str) or not secret:
        raise _LiveHTTPException(
            status_code=503,
            detail="Paper secret missing.",
        )

    return key_id, secret


def _live_get_json(
    host: str,
    target: str,
    counter: str,
):
    if host not in _LIVE_ALLOWED_HOSTS:
        raise _LiveHTTPException(
            status_code=500,
            detail="Forbidden Alpaca host.",
        )

    key_id, secret = _live_credentials()

    connection = _live_http_client.HTTPSConnection(
        host,
        timeout=12,
    )

    try:
        # Exactly one GET.
        # No retry loop exists.
        connection.request(
            "GET",
            target,
            body=None,
            headers={
                "Accept": "application/json",
                "APCA-API-KEY-ID": key_id,
                "APCA-API-SECRET-KEY": secret,
            },
        )

        response = connection.getresponse()
        raw = response.read()

        _LIVE_READ_COUNTS[counter] += 1

    except Exception as exc:
        raise _LiveHTTPException(
            status_code=502,
            detail=(
                "Alpaca read failed: "
                + type(exc).__name__
            ),
        )

    finally:
        try:
            connection.close()
        except Exception:
            pass

    if response.status != 200:
        raise _LiveHTTPException(
            status_code=502,
            detail=(
                "Alpaca returned HTTP "
                + str(response.status)
            ),
        )

    try:
        return json.loads(
            raw.decode("utf-8")
        )

    except Exception:
        raise _LiveHTTPException(
            status_code=502,
            detail="Alpaca returned invalid JSON.",
        )


def _live_pick(
    mapping,
    *keys,
):
    if not isinstance(mapping, dict):
        return None

    for key in keys:
        if key in mapping:
            value = mapping.get(key)

            if value is not None:
                return value

    return None


def _live_number(value):
    try:
        return float(value)
    except Exception:
        return None


def _live_stock_snapshot(
    symbol: str,
    item,
):
    if not isinstance(item, dict):
        return {
            "symbol": symbol,
            "available": False,
        }

    latest_trade = (
        item.get("latestTrade")
        or item.get("latest_trade")
        or {}
    )

    latest_quote = (
        item.get("latestQuote")
        or item.get("latest_quote")
        or {}
    )

    minute_bar = (
        item.get("minuteBar")
        or item.get("minute_bar")
        or {}
    )

    daily_bar = (
        item.get("dailyBar")
        or item.get("daily_bar")
        or {}
    )

    previous_bar = (
        item.get("prevDailyBar")
        or item.get("previousDailyBar")
        or item.get("previous_daily_bar")
        or {}
    )

    latest_price = _live_number(
        _live_pick(
            latest_trade,
            "p",
            "price",
        )
    )

    previous_close = _live_number(
        _live_pick(
            previous_bar,
            "c",
            "close",
        )
    )

    day_change_pct = None

    if (
        latest_price is not None
        and previous_close is not None
        and previous_close != 0
    ):
        day_change_pct = (
            latest_price / previous_close - 1
        ) * 100

    return {
        "symbol": symbol,
        "available": (
            latest_price is not None
        ),

        "latest_trade_price":
            latest_price,

        "latest_trade_timestamp":
            _live_pick(
                latest_trade,
                "t",
                "timestamp",
            ),

        "bid_price":
            _live_number(
                _live_pick(
                    latest_quote,
                    "bp",
                    "bid_price",
                )
            ),

        "ask_price":
            _live_number(
                _live_pick(
                    latest_quote,
                    "ap",
                    "ask_price",
                )
            ),

        "day_change_pct":
            day_change_pct,

        "previous_close":
            previous_close,

        "minute_bar": {
            "timestamp":
                _live_pick(
                    minute_bar,
                    "t",
                    "timestamp",
                ),

            "open":
                _live_number(
                    _live_pick(
                        minute_bar,
                        "o",
                        "open",
                    )
                ),

            "high":
                _live_number(
                    _live_pick(
                        minute_bar,
                        "h",
                        "high",
                    )
                ),

            "low":
                _live_number(
                    _live_pick(
                        minute_bar,
                        "l",
                        "low",
                    )
                ),

            "close":
                _live_number(
                    _live_pick(
                        minute_bar,
                        "c",
                        "close",
                    )
                ),

            "volume":
                _live_number(
                    _live_pick(
                        minute_bar,
                        "v",
                        "volume",
                    )
                ),
        },

        "daily_bar": {
            "open":
                _live_number(
                    _live_pick(
                        daily_bar,
                        "o",
                        "open",
                    )
                ),

            "high":
                _live_number(
                    _live_pick(
                        daily_bar,
                        "h",
                        "high",
                    )
                ),

            "low":
                _live_number(
                    _live_pick(
                        daily_bar,
                        "l",
                        "low",
                    )
                ),

            "close":
                _live_number(
                    _live_pick(
                        daily_bar,
                        "c",
                        "close",
                    )
                ),
        },
    }


@app.get("/api/live/market")
def live_market() -> JSONResponse:
    with _LIVE_MARKET_LOCK:
        now = _live_time.monotonic()

        cached = _LIVE_MARKET_CACHE[
            "payload"
        ]

        age = (
            now
            - _LIVE_MARKET_CACHE[
                "monotonic"
            ]
        )

        if (
            cached is not None
            and age
            < _LIVE_MARKET_TTL_SECONDS
        ):
            result = dict(cached)
            result["cache_hit"] = True

            return JSONResponse(
                result
            )

        query = _live_urlencode(
            {
                "symbols":
                    "NVDA,MSFT,META",

                "feed":
                    "iex",
            }
        )

        payload = _live_get_json(
            _LIVE_DATA_HOST,
            "/v2/stocks/snapshots?"
            + query,
            "market_gets",
        )

        if not isinstance(
            payload,
            dict,
        ):
            raise _LiveHTTPException(
                status_code=502,
                detail="Invalid market snapshot payload.",
            )

        symbols = {}

        for symbol in (
            "NVDA",
            "MSFT",
            "META",
        ):
            symbols[symbol] = (
                _live_stock_snapshot(
                    symbol,
                    payload.get(symbol),
                )
            )

        result = {
            "mode":
                "LIVE_READ_ONLY",

            "provider":
                "ALPACA",

            "feed":
                "IEX",

            "received_at_utc":
                _live_now_utc(),

            "symbols":
                symbols,

            "cache_hit":
                False,

            "read_counts":
                dict(
                    _LIVE_READ_COUNTS
                ),

            "broker_writes":
                0,

            "orders_created":
                0,

            "model_calls":
                0,

            "live_money":
                "PROHIBITED",
        }

        _LIVE_MARKET_CACHE[
            "payload"
        ] = result

        _LIVE_MARKET_CACHE[
            "monotonic"
        ] = now

        return JSONResponse(
            result
        )


@app.get("/api/live/account")
def live_account() -> JSONResponse:
    with _LIVE_ACCOUNT_LOCK:
        now = _live_time.monotonic()

        cached = _LIVE_ACCOUNT_CACHE[
            "payload"
        ]

        age = (
            now
            - _LIVE_ACCOUNT_CACHE[
                "monotonic"
            ]
        )

        if (
            cached is not None
            and age
            < _LIVE_ACCOUNT_TTL_SECONDS
        ):
            result = dict(cached)
            result["cache_hit"] = True

            return JSONResponse(
                result
            )

        account = _live_get_json(
            _LIVE_PAPER_HOST,
            "/v2/account",
            "account_gets",
        )

        positions = _live_get_json(
            _LIVE_PAPER_HOST,
            "/v2/positions",
            "positions_gets",
        )

        if not isinstance(
            account,
            dict,
        ):
            raise _LiveHTTPException(
                status_code=502,
                detail="Invalid account payload.",
            )

        if not isinstance(
            positions,
            list,
        ):
            raise _LiveHTTPException(
                status_code=502,
                detail="Invalid positions payload.",
            )

        account_keys = (
            "status",
            "currency",
            "cash",
            "equity",
            "portfolio_value",
            "buying_power",
            "regt_buying_power",
            "daytrading_buying_power",
            "options_buying_power",
            "long_market_value",
            "short_market_value",
            "initial_margin",
            "maintenance_margin",
            "pattern_day_trader",
            "trading_blocked",
            "account_blocked",
        )

        safe_account = {
            key: account.get(key)
            for key in account_keys
            if key in account
        }

        position_keys = (
            "symbol",
            "asset_class",
            "side",
            "qty",
            "avg_entry_price",
            "market_value",
            "cost_basis",
            "unrealized_pl",
            "unrealized_plpc",
            "unrealized_intraday_pl",
            "unrealized_intraday_plpc",
            "current_price",
            "lastday_price",
            "change_today",
        )

        safe_positions = []

        for position in positions:
            if not isinstance(
                position,
                dict,
            ):
                continue

            safe_positions.append(
                {
                    key:
                        position.get(key)

                    for key
                    in position_keys

                    if key in position
                }
            )

        result = {
            "mode":
                "LIVE_READ_ONLY",

            "provider":
                "ALPACA",

            "environment":
                "PAPER",

            "received_at_utc":
                _live_now_utc(),

            "account":
                safe_account,

            "positions":
                safe_positions,

            "position_count":
                len(safe_positions),

            "cache_hit":
                False,

            "read_counts":
                dict(
                    _LIVE_READ_COUNTS
                ),

            "broker_writes":
                0,

            "orders_created":
                0,

            "model_calls":
                0,

            "live_money":
                "PROHIBITED",
        }

        _LIVE_ACCOUNT_CACHE[
            "payload"
        ] = result

        _LIVE_ACCOUNT_CACHE[
            "monotonic"
        ] = now

        return JSONResponse(
            result
        )


@app.get("/api/live/health")
def live_health() -> JSONResponse:
    now = _live_time.monotonic()

    market_age = None
    account_age = None

    if _LIVE_MARKET_CACHE[
        "payload"
    ] is not None:
        market_age = max(
            0.0,
            now
            - _LIVE_MARKET_CACHE[
                "monotonic"
            ],
        )

    if _LIVE_ACCOUNT_CACHE[
        "payload"
    ] is not None:
        account_age = max(
            0.0,
            now
            - _LIVE_ACCOUNT_CACHE[
                "monotonic"
            ],
        )

    return JSONResponse(
        {
            "mode":
                "LIVE_READ_ONLY",

            "credentials":
                "CONFIGURED",

            "paper_only":
                True,

            "market_cache_age_seconds":
                market_age,

            "account_cache_age_seconds":
                account_age,

            "read_counts":
                dict(
                    _LIVE_READ_COUNTS
                ),

            "broker_writes":
                0,

            "orders_created":
                0,

            "model_calls":
                0,

            "live_money":
                "PROHIBITED",
        }
    )


# ============================================================
# Market Jury V6.1
# Persistent local Investment Session.
#
# This is PRODUCT STATE only.
# It has no broker/model/provider transport authority.
# ============================================================

from fastapi import HTTPException as _V6HTTPException
from pydantic import BaseModel as _V6BaseModel

from .product_state import (
    get_active_session as _v6_get_active_session,
    select_active_symbol as _v6_select_active_symbol,
)


class _V6ProductSymbolSelection(
    _V6BaseModel
):
    symbol: str


@app.get("/api/product/session")
def v6_product_session():
    return _v6_get_active_session()


@app.post("/api/product/session/select")
def v6_product_session_select(
    payload: _V6ProductSymbolSelection,
):
    try:
        return _v6_select_active_symbol(
            payload.symbol
        )

    except ValueError as exc:
        raise _V6HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


# ============================================================
# Market Jury V6.2A
# Product Council orchestration — fake transport only.
#
# These endpoints have zero OpenAI/provider/broker authority.
# ============================================================

from .analysis_state import (
    advance_fake_analysis as _v62_advance_fake_analysis,
    analysis_preflight as _v62_analysis_preflight,
    latest_analysis as _v62_latest_analysis,
    start_fake_analysis as _v62_start_fake_analysis,
)


@app.get("/api/product/analysis/preflight")
def v62_analysis_preflight():
    return _v62_analysis_preflight()


@app.get("/api/product/analysis/current")
def v62_analysis_current():
    return _v62_latest_analysis()


@app.post("/api/product/analysis/fake/start")
def v62_analysis_fake_start():
    return _v62_start_fake_analysis()


@app.post("/api/product/analysis/fake/{run_id}/step")
def v62_analysis_fake_step(
    run_id: str,
):
    try:
        return _v62_advance_fake_analysis(
            run_id
        )

    except ValueError as exc:
        raise _V6HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


# ============================================================
# Market Jury V6.2B
# Durable Initial cost preflight.
#
# Capture may use existing read-only Alpaca endpoints.
# No OpenAI transport and no broker-write authority exist here.
# ============================================================

from .initial_preflight import (
    create_initial_preflight as _v62b_create_initial_preflight,
    latest_initial_preflight as _v62b_latest_initial_preflight,
    resolve_code_commit_sha as _v62b_resolve_code_commit_sha,
    tracked_worktree_clean as _v62b_tracked_worktree_clean,
)


def _v62b_response_object(
    response,
):
    value = json.loads(
        response.body.decode("utf-8")
    )

    if not isinstance(value, dict):
        raise _V6HTTPException(
            status_code=500,
            detail="Internal live payload malformed.",
        )

    return value


@app.get(
    "/api/product/analysis/initial/preflight"
)
def v62b_initial_preflight_current():
    return _v62b_latest_initial_preflight()


@app.post(
    "/api/product/analysis/initial/preflight/capture"
)
def v62b_initial_preflight_capture():
    if not _v62b_tracked_worktree_clean():
        raise _V6HTTPException(
            status_code=409,
            detail=(
                "Initial paid preflight requires "
                "a clean tracked worktree."
            ),
        )

    code_sha = (
        _v62b_resolve_code_commit_sha()
    )

    before = dict(
        _LIVE_READ_COUNTS
    )

    market = _v62b_response_object(
        live_market()
    )

    account = _v62b_response_object(
        live_account()
    )

    after = dict(
        _LIVE_READ_COUNTS
    )

    read_delta = {
        key:
            int(
                after.get(key, 0)
            )
            - int(
                before.get(key, 0)
            )

        for key in (
            "market_gets",
            "account_gets",
            "positions_gets",
        )
    }

    try:
        return _v62b_create_initial_preflight(
            market_payload=
                market,

            account_payload=
                account,

            code_commit_sha=
                code_sha,

            provider_read_delta=
                read_delta,
        )

    except (
        ValueError,
        RuntimeError,
    ) as exc:
        raise _V6HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


# ============================================================
# Market Jury V6.2B.2
# Hash-bound owner approval.
#
# Approval is durable but DOES NOT authorize or execute
# any model/provider/broker call.
# ============================================================

from .initial_approval import (
    APPROVAL_CONFIRMATION as _v62b_approval_confirmation,
    create_initial_owner_approval as _v62b_create_initial_owner_approval,
    latest_initial_owner_approval as _v62b_latest_initial_owner_approval,
)


class _V62BInitialApprovalRequest(
    _V6BaseModel
):
    preflight_id: str
    preflight_artifact_hash: str
    request_set_hash: str
    approved_max_cost_usd: str
    confirmation: str


@app.get(
    "/api/product/analysis/initial/approval"
)
def v62b_initial_owner_approval_current():
    return _v62b_latest_initial_owner_approval()


@app.post(
    "/api/product/analysis/initial/approval"
)
def v62b_initial_owner_approval_create(
    payload: _V62BInitialApprovalRequest,
):
    if (
        payload.confirmation
        != _v62b_approval_confirmation
    ):
        raise _V6HTTPException(
            status_code=422,
            detail=(
                "Explicit Initial owner "
                "confirmation is required."
            ),
        )

    if not _v62b_tracked_worktree_clean():
        raise _V6HTTPException(
            status_code=409,
            detail=(
                "Owner approval requires "
                "a clean tracked worktree."
            ),
        )

    code_sha = (
        _v62b_resolve_code_commit_sha()
    )

    try:
        return _v62b_create_initial_owner_approval(
            current_code_sha=
                code_sha,

            preflight_id=
                payload.preflight_id,

            preflight_artifact_hash=
                payload.preflight_artifact_hash,

            request_set_hash=
                payload.request_set_hash,

            approved_max_cost_usd=
                payload.approved_max_cost_usd,

            confirmation=
                payload.confirmation,
        )

    except (
        ValueError,
        RuntimeError,
    ) as exc:
        raise _V6HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
