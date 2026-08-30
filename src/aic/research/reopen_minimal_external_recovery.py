from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B3_REOPEN_MINIMAL_EXTERNAL_READ_RECOVERY_v0_1"
PASS_STATUS = "B3_REOPEN_MINIMAL_EXTERNAL_READ_RECOVERY_ZERO_CALL_PASS"
NEXT_GATE = "B3_REOPEN_REMAINING_GAPS_CLOSURE_ZERO_CALL"

EXPECTED_BLOCKED_RESULT_HASH = "5e8efe6fbff767930fdeb38ab9d76d87cf10903116546f60d7a2c858f277a90c"
EXPECTED_AUTHORIZATION_HASH = "acece41f6a9d6e254be54a341a0ce0c9242d038af6100bfc8d15d80e244a22c3"
EXPECTED_PREFLIGHT_HASH = "a37ed5891f760c5959177c515d64e078b392b45e9dd70f1f1870e79d7b601067"
EXPECTED_RECEIPT_MANIFEST_HASH = "72f95b7f19408ab0ec1a7188beb0be50003a6498258f7d48a50199bd8b83a5ab"
EXPECTED_OWNER_APPROVAL_ID = "OWNER-B3-REOPEN-MINIMAL-EXTERNAL-READ-V01"
EXPECTED_BLOCKED_STATUS = "B3_REOPEN_MINIMAL_EXTERNAL_READ_CAPTURE_BLOCKED"
EXPECTED_STOP_REASON = "MinimalExternalReadError: market multi-bars next_page_token malformed"
EXPECTED_CAPTURE_CODE_SHA = "46a9c39228a55a96a5e893aff65e3d641fd130cf"

READ_IDS = (
    "R1_CURRENT_POSITIONS_ANCHOR",
    "R2_POST_CUTOFF_ACCOUNT_ACTIVITIES_FIRST_PAGE",
    "R3_B2_CUTOFF_PORTFOLIO_EQUITY",
    "R4_MSFT_META_POINT_IN_TIME_BARS",
)
EXPECTED_RAW_HASHES = {
    READ_IDS[0]: "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570",
    READ_IDS[1]: "3af30aff3449021972a46789c7b7f513afd1098ae79231ba34a91a0f6c211384",
    READ_IDS[2]: "725272b34d623d71770817efcf04585ceef2ad228df40d4852b448225108aed6",
    READ_IDS[3]: "74a808a5a5d9a66aca9585ffe05d120ab6227fdef4937e73e8629ee4a88e8638",
}
EXPECTED_RAW_BYTES = {
    READ_IDS[0]: 3,
    READ_IDS[1]: 284,
    READ_IDS[2]: 456,
    READ_IDS[3]: 99862,
}

B2_CUTOFF = "2026-08-27T20:00:00Z"
META_B2_PRICE_CUTOFF = "2026-08-27T19:59:00Z"
RESEARCH_PRICE_CUTOFF = "2026-08-28T17:33:00Z"
MSFT_EPS = Decimal("17.95")
META_EPS = Decimal("23.49")


class MinimalExternalRecoveryError(RuntimeError):
    pass


def _read_json(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MinimalExternalRecoveryError(f"unable to read {label}") from exc
    if not isinstance(payload, dict):
        raise MinimalExternalRecoveryError(f"{label} root must be an object")
    return payload


def _read_raw_decimal(path: str | Path, *, label: str) -> tuple[bytes, Any]:
    try:
        raw = Path(path).read_bytes()
        payload = json.loads(raw.decode("utf-8"), parse_float=Decimal, parse_int=Decimal)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MinimalExternalRecoveryError(f"unable to read {label}") from exc
    return raw, payload


def _verify_self_hash(payload: Mapping[str, Any], *, label: str) -> str:
    observed = payload.get("artifact_hash")
    if not isinstance(observed, str) or len(observed) != 64:
        raise MinimalExternalRecoveryError(f"{label} artifact_hash missing")
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    if observed != expected:
        raise MinimalExternalRecoveryError(f"{label} self-hash mismatch")
    return observed


def _decimal(value: Any, *, label: str) -> Decimal:
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except (InvalidOperation, AttributeError) as exc:
            raise MinimalExternalRecoveryError(f"{label} must be decimal-compatible") from exc
    else:
        raise MinimalExternalRecoveryError(f"{label} must be decimal-compatible")
    if not result.is_finite():
        raise MinimalExternalRecoveryError(f"{label} must be finite")
    return result


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _ratio_text(numerator: Decimal, denominator: Decimal) -> str:
    if denominator == 0:
        raise MinimalExternalRecoveryError("ratio denominator must be nonzero")
    with localcontext() as ctx:
        ctx.prec = 34
        value = numerator / denominator
    return format(value.quantize(Decimal("0.000000000001")), "f")


def _parse_datetime(value: Any, *, label: str) -> datetime:
    if isinstance(value, Decimal):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise MinimalExternalRecoveryError(f"{label} timestamp invalid") from exc
    if isinstance(value, int):
        return datetime.fromtimestamp(value, tz=UTC)
    if not isinstance(value, str) or not value.strip():
        raise MinimalExternalRecoveryError(f"{label} timestamp missing")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MinimalExternalRecoveryError(f"{label} timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MinimalExternalRecoveryError(f"{label} timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def validate_capture_lineage(
    *,
    result: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    if _verify_self_hash(result, label="blocked result") != EXPECTED_BLOCKED_RESULT_HASH:
        raise MinimalExternalRecoveryError("blocked result hash drift")
    if _verify_self_hash(authorization, label="authorization") != EXPECTED_AUTHORIZATION_HASH:
        raise MinimalExternalRecoveryError("authorization hash drift")
    if result.get("status") != EXPECTED_BLOCKED_STATUS:
        raise MinimalExternalRecoveryError("blocked result status drift")
    if result.get("stop_reason") != EXPECTED_STOP_REASON:
        raise MinimalExternalRecoveryError("blocked result stop reason drift")
    if result.get("code_commit_sha") != EXPECTED_CAPTURE_CODE_SHA:
        raise MinimalExternalRecoveryError("capture code SHA drift")
    if result.get("source_authorization_hash") != EXPECTED_AUTHORIZATION_HASH:
        raise MinimalExternalRecoveryError("result authorization lineage drift")
    if result.get("source_preflight_hash") != EXPECTED_PREFLIGHT_HASH:
        raise MinimalExternalRecoveryError("result preflight lineage drift")
    if result.get("receipt_manifest_hash") != EXPECTED_RECEIPT_MANIFEST_HASH:
        raise MinimalExternalRecoveryError("receipt manifest drift")
    if result.get("provider_dispatch_attempts") != 4 or result.get("provider_reads") != 4:
        raise MinimalExternalRecoveryError("four-response capture contract drift")
    if result.get("authorization_consumed") is not True or result.get("rerun_authorized") is not False:
        raise MinimalExternalRecoveryError("authorization consumption boundary drift")
    if result.get("model_calls") != 0 or result.get("broker_writes") != 0 or result.get("alpaca_orders") != 0:
        raise MinimalExternalRecoveryError("side-effect boundary drift")
    if result.get("live_money") != "PROHIBITED":
        raise MinimalExternalRecoveryError("live-money boundary drift")
    if authorization.get("owner_approval_id") != EXPECTED_OWNER_APPROVAL_ID:
        raise MinimalExternalRecoveryError("owner approval id drift")
    if authorization.get("approved_preflight_hash") != EXPECTED_PREFLIGHT_HASH:
        raise MinimalExternalRecoveryError("approved preflight drift")
    if authorization.get("approved_provider_dispatch_attempts_max") != 4:
        raise MinimalExternalRecoveryError("approved dispatch ceiling drift")
    if authorization.get("automatic_retries") != 0 or authorization.get("rerun_authorized") is not False:
        raise MinimalExternalRecoveryError("authorization retry boundary drift")

    captures = result.get("captures")
    if not isinstance(captures, list) or len(captures) != 4:
        raise MinimalExternalRecoveryError("capture manifest must contain four entries")
    by_id: dict[str, dict[str, Any]] = {}
    for row in captures:
        if not isinstance(row, dict):
            raise MinimalExternalRecoveryError("capture row malformed")
        read_id = row.get("read_id")
        if read_id not in READ_IDS or read_id in by_id:
            raise MinimalExternalRecoveryError("capture read id drift")
        if row.get("stdout_sha256") != EXPECTED_RAW_HASHES[read_id]:
            raise MinimalExternalRecoveryError(f"capture hash drift for {read_id}")
        if row.get("stdout_bytes") != EXPECTED_RAW_BYTES[read_id]:
            raise MinimalExternalRecoveryError(f"capture size drift for {read_id}")
        if not isinstance(row.get("response_received_at_utc"), str):
            raise MinimalExternalRecoveryError(f"capture timestamp missing for {read_id}")
        by_id[read_id] = row
    if tuple(by_id) != READ_IDS:
        raise MinimalExternalRecoveryError("capture order drift")
    return by_id


def verify_receipts(path: str | Path, *, result: Mapping[str, Any]) -> dict[str, Any]:
    try:
        lines = [line for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError as exc:
        raise MinimalExternalRecoveryError("unable to read receipt manifest") from exc
    if len(lines) != 8:
        raise MinimalExternalRecoveryError("receipt event count must be exactly eight")
    events: list[dict[str, Any]] = []
    hashes: list[str] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MinimalExternalRecoveryError("receipt JSONL malformed") from exc
        if not isinstance(event, dict):
            raise MinimalExternalRecoveryError("receipt event must be object")
        observed = event.get("receipt_hash")
        if not isinstance(observed, str) or len(observed) != 64:
            raise MinimalExternalRecoveryError("receipt hash missing")
        expected = canonical_sha256(event, exclude_fields=("receipt_hash",))
        if observed != expected:
            raise MinimalExternalRecoveryError("receipt self-hash mismatch")
        events.append(event)
        hashes.append(observed)
    if hashes != result.get("receipt_hashes"):
        raise MinimalExternalRecoveryError("receipt hashes do not match blocked result")
    if canonical_sha256({"receipt_hashes": hashes}) != EXPECTED_RECEIPT_MANIFEST_HASH:
        raise MinimalExternalRecoveryError("receipt manifest hash mismatch")
    for index, read_id in enumerate(READ_IDS):
        attempt = events[index * 2]
        response = events[index * 2 + 1]
        if attempt.get("event") != "PROVIDER_DISPATCH_ATTEMPT" or attempt.get("read_id") != read_id:
            raise MinimalExternalRecoveryError(f"receipt attempt order drift for {read_id}")
        if response.get("event") != "PROVIDER_RESPONSE_RECEIVED" or response.get("read_id") != read_id:
            raise MinimalExternalRecoveryError(f"receipt response order drift for {read_id}")
        if response.get("stdout_sha256") != EXPECTED_RAW_HASHES[read_id]:
            raise MinimalExternalRecoveryError(f"receipt response hash drift for {read_id}")
    return {"receipt_event_count": 8, "provider_attempt_events": 4, "provider_response_events": 4}


def load_and_verify_raw(
    *,
    raw_dir: str | Path,
    captures: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    root = Path(raw_dir)
    payloads: dict[str, Any] = {}
    for read_id in READ_IDS:
        expected_path = root / f"{read_id}.json"
        capture_path = Path(str(captures[read_id].get("raw_path") or ""))
        if capture_path.name != expected_path.name:
            raise MinimalExternalRecoveryError(f"raw capture path drift for {read_id}")
        raw, payload = _read_raw_decimal(expected_path, label=read_id)
        if len(raw) != EXPECTED_RAW_BYTES[read_id]:
            raise MinimalExternalRecoveryError(f"raw byte-size mismatch for {read_id}")
        if hashlib.sha256(raw).hexdigest() != EXPECTED_RAW_HASHES[read_id]:
            raise MinimalExternalRecoveryError(f"raw SHA256 mismatch for {read_id}")
        payloads[read_id] = payload
    return payloads


def recover_terminal_market_page(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise MinimalExternalRecoveryError("market multi-bars root must be object")
    if "next_page_token" not in payload:
        raise MinimalExternalRecoveryError("market multi-bars missing next_page_token")
    token = payload.get("next_page_token")
    if token != "":
        if token is None:
            raise MinimalExternalRecoveryError("blocked historical parser would not have rejected null token")
        if isinstance(token, str) and token.strip():
            raise MinimalExternalRecoveryError("market multi-bars has a real next page token")
        raise MinimalExternalRecoveryError("market multi-bars token is not the CLI empty-string zero value")
    return {
        "historical_parser_failure": EXPECTED_STOP_REASON,
        "observed_next_page_token_representation": "EMPTY_STRING",
        "cli_contract_interpretation": "GO_STRING_ZERO_VALUE_MEANS_NO_NEXT_PAGE_TOKEN",
        "terminal_page_recovered": True,
        "pagination_continuation_required": False,
        "provider_rerun_required": False,
    }


def _position_rows(payload: Any) -> list[Mapping[str, Any]]:
    rows: Any = payload
    if isinstance(payload, Mapping):
        rows = payload.get("positions", payload.get("data"))
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise MinimalExternalRecoveryError("current positions response shape unsupported")
    return list(rows)


def _activity_rows(payload: Any) -> list[Mapping[str, Any]]:
    rows: Any = payload
    if isinstance(payload, Mapping):
        rows = payload.get("activities", payload.get("data"))
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise MinimalExternalRecoveryError("activity response shape unsupported")
    if len(rows) != 1:
        raise MinimalExternalRecoveryError("historical activity capture must contain exactly one row")
    return list(rows)


def _symbol(row: Mapping[str, Any]) -> str | None:
    value = row.get("symbol")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise MinimalExternalRecoveryError("symbol must be a non-empty string when present")
    return value.strip().upper()


def reconstruct_meta_quantity(*, positions_payload: Any, activities_payload: Any, anchor_utc: str) -> dict[str, Any]:
    positions = _position_rows(positions_payload)
    current_meta = Decimal("0")
    seen_meta_position = False
    for row in positions:
        if _symbol(row) != "META":
            continue
        if seen_meta_position:
            raise MinimalExternalRecoveryError("duplicate META current position")
        seen_meta_position = True
        current_meta = _decimal(row.get("qty", row.get("quantity")), label="current META quantity")

    anchor = _parse_datetime(anchor_utc, label="R1 anchor")
    cutoff = _parse_datetime(B2_CUTOFF, label="B2 cutoff")
    cutoff_meta = current_meta
    meta_fill_count = 0
    rows = _activity_rows(activities_payload)
    unsupported = 0
    activity_types: list[str] = []
    for row in rows:
        activity_type_raw = row.get("activity_type", row.get("type"))
        activity_type = activity_type_raw.strip().upper() if isinstance(activity_type_raw, str) else ""
        activity_types.append(activity_type or "UNKNOWN")
        symbol = _symbol(row)
        qty_raw = row.get("qty", row.get("quantity"))
        qty_present = qty_raw is not None and str(qty_raw).strip() not in ("", "0", "0.0")
        if activity_type != "FILL" and (symbol is not None or qty_present):
            unsupported += 1
            continue
        if activity_type != "FILL" or symbol != "META":
            continue
        qty = _decimal(qty_raw, label="META fill quantity")
        if qty <= 0:
            raise MinimalExternalRecoveryError("META fill quantity must be positive")
        side_raw = row.get("side")
        if not isinstance(side_raw, str) or side_raw.strip().lower() not in {"buy", "sell"}:
            raise MinimalExternalRecoveryError("META fill side must be buy or sell")
        time_raw = row.get("transaction_time", row.get("timestamp", row.get("date")))
        tx_time = _parse_datetime(time_raw, label="META fill transaction_time")
        if not (cutoff < tx_time <= anchor):
            raise MinimalExternalRecoveryError("META fill timestamp outside approved reconstruction window")
        if side_raw.strip().lower() == "buy":
            cutoff_meta -= qty
        else:
            cutoff_meta += qty
        meta_fill_count += 1
    if unsupported:
        raise MinimalExternalRecoveryError("security-affecting non-FILL activity present in raw capture")
    return {
        "current_meta_quantity": _decimal_text(current_meta),
        "post_cutoff_activity_record_count": len(rows),
        "post_cutoff_activity_types": activity_types,
        "post_cutoff_meta_fill_count": meta_fill_count,
        "reconstructed_meta_quantity_at_b2_cutoff": _decimal_text(cutoff_meta),
        "reconstruction_anchor_utc": _iso(anchor),
        "b2_cutoff_utc": B2_CUTOFF,
        "quantity_reconstruction_complete": True,
    }


def select_portfolio_equity(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise MinimalExternalRecoveryError("portfolio history root must be object")
    timestamps = payload.get("timestamp", payload.get("timestamps"))
    equities = payload.get("equity")
    if not isinstance(timestamps, list) or not isinstance(equities, list) or len(timestamps) != len(equities) or not timestamps:
        raise MinimalExternalRecoveryError("portfolio history timestamp/equity arrays malformed")
    cutoff = _parse_datetime(B2_CUTOFF, label="B2 cutoff")
    candidates: list[tuple[datetime, Decimal]] = []
    for index, (ts_raw, equity_raw) in enumerate(zip(timestamps, equities, strict=True)):
        ts = _parse_datetime(ts_raw, label=f"portfolio timestamp[{index}]")
        equity = _decimal(equity_raw, label=f"portfolio equity[{index}]")
        if ts <= cutoff:
            candidates.append((ts, equity))
    if not candidates:
        raise MinimalExternalRecoveryError("no portfolio equity datapoint at or before B2 cutoff")
    ts, equity = max(candidates, key=lambda item: item[0])
    if equity <= 0:
        raise MinimalExternalRecoveryError("selected B2 portfolio equity must be positive")
    return {
        "selected_equity": _decimal_text(equity),
        "selected_equity_timestamp_utc": _iso(ts),
        "selection_rule": "LATEST_EQUITY_DATAPOINT_AT_OR_BEFORE_B2_CUTOFF",
    }


def _bars_for_symbol(payload: Mapping[str, Any], symbol: str) -> list[Mapping[str, Any]]:
    bars = payload.get("bars")
    if not isinstance(bars, Mapping):
        raise MinimalExternalRecoveryError("market bars object missing")
    rows = bars.get(symbol)
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise MinimalExternalRecoveryError(f"market bars missing for {symbol}")
    if not rows:
        raise MinimalExternalRecoveryError(f"market bars empty for {symbol}")
    return list(rows)


def select_market_close(payload: Any, *, symbol: str, cutoff_utc: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise MinimalExternalRecoveryError("market multi-bars root must be object")
    cutoff = _parse_datetime(cutoff_utc, label=f"{symbol} price cutoff")
    candidates: list[tuple[datetime, Decimal]] = []
    for index, row in enumerate(_bars_for_symbol(payload, symbol)):
        ts = _parse_datetime(row.get("t", row.get("timestamp")), label=f"{symbol} bar[{index}] timestamp")
        close = _decimal(row.get("c", row.get("close")), label=f"{symbol} bar[{index}] close")
        if close <= 0:
            raise MinimalExternalRecoveryError(f"{symbol} close must be positive")
        if ts <= cutoff:
            candidates.append((ts, close))
    if not candidates:
        raise MinimalExternalRecoveryError(f"no {symbol} completed bar at or before cutoff")
    ts, close = max(candidates, key=lambda item: item[0])
    return {
        "symbol": symbol,
        "close": _decimal_text(close),
        "bar_timestamp_utc": _iso(ts),
        "price_cutoff_utc": cutoff_utc,
        "feed": "iex",
        "timeframe": "1Min",
        "selection_rule": "LATEST_COMPLETED_BAR_AT_OR_BEFORE_CUTOFF",
    }


def build_recovery_artifact(
    *,
    code_commit_sha: str,
    blocked_result_path: str | Path,
    authorization_path: str | Path,
    receipts_path: str | Path,
    raw_dir: str | Path,
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise MinimalExternalRecoveryError("code_commit_sha must be lowercase 40-char SHA")
    result = _read_json(blocked_result_path, label="blocked result")
    authorization = _read_json(authorization_path, label="authorization")
    captures = validate_capture_lineage(result=result, authorization=authorization)
    receipt_summary = verify_receipts(receipts_path, result=result)
    raw = load_and_verify_raw(raw_dir=raw_dir, captures=captures)

    market_payload = raw[READ_IDS[3]]
    token_recovery = recover_terminal_market_page(market_payload)
    quantity = reconstruct_meta_quantity(
        positions_payload=raw[READ_IDS[0]],
        activities_payload=raw[READ_IDS[1]],
        anchor_utc=str(captures[READ_IDS[0]]["response_received_at_utc"]),
    )
    equity = select_portfolio_equity(raw[READ_IDS[2]])
    meta_b2_price = select_market_close(market_payload, symbol="META", cutoff_utc=META_B2_PRICE_CUTOFF)
    msft_research_price = select_market_close(market_payload, symbol="MSFT", cutoff_utc=RESEARCH_PRICE_CUTOFF)
    meta_research_price = select_market_close(market_payload, symbol="META", cutoff_utc=RESEARCH_PRICE_CUTOFF)

    msft_price = Decimal(msft_research_price["close"])
    meta_price = Decimal(meta_research_price["close"])
    meta_b2_close = Decimal(meta_b2_price["close"])
    meta_qty = Decimal(quantity["reconstructed_meta_quantity_at_b2_cutoff"])
    b2_equity = Decimal(equity["selected_equity"])
    meta_market_value = meta_qty * meta_b2_close

    valuation = {
        "MSFT": {
            "metric": "PRICE_TO_LATEST_REPORTED_ANNUAL_GAAP_DILUTED_EPS",
            "price": msft_research_price,
            "annual_gaap_diluted_eps": "17.95",
            "eps_period": "FY2026",
            "price_to_eps": _ratio_text(msft_price, MSFT_EPS),
            "valuation_evidence_complete": True,
        },
        "META": {
            "metric": "PRICE_TO_LATEST_REPORTED_ANNUAL_GAAP_DILUTED_EPS",
            "price": meta_research_price,
            "annual_gaap_diluted_eps": "23.49",
            "eps_period": "FY2025",
            "price_to_eps": _ratio_text(meta_price, META_EPS),
            "valuation_evidence_complete": True,
        },
    }
    portfolio = {
        **quantity,
        "b2_cutoff_portfolio_equity": equity,
        "meta_b2_cutoff_price": meta_b2_price,
        "reconstructed_meta_market_value_at_b2_cutoff": _decimal_text(meta_market_value),
        "reconstructed_meta_portfolio_weight": _ratio_text(meta_market_value, b2_equity),
        "portfolio_interaction_evidence_complete": True,
        "current_positions_used_only_as_reverse_reconstruction_anchor": True,
    }

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_blocked_result_hash": EXPECTED_BLOCKED_RESULT_HASH,
        "source_authorization_hash": EXPECTED_AUTHORIZATION_HASH,
        "source_preflight_hash": EXPECTED_PREFLIGHT_HASH,
        "source_receipt_manifest_hash": EXPECTED_RECEIPT_MANIFEST_HASH,
        "owner_approval_id": EXPECTED_OWNER_APPROVAL_ID,
        "historical_capture_status_preserved": EXPECTED_BLOCKED_STATUS,
        "historical_capture_stop_reason_preserved": EXPECTED_STOP_REASON,
        "authorization_consumed": True,
        "provider_rerun_authorized": False,
        "new_provider_dispatch_attempts": 0,
        "new_provider_reads": 0,
        "reused_provider_responses": 4,
        "model_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "receipt_verification": receipt_summary,
        "raw_capture_integrity": [
            {
                "read_id": read_id,
                "stdout_sha256": EXPECTED_RAW_HASHES[read_id],
                "stdout_bytes": EXPECTED_RAW_BYTES[read_id],
                "integrity_verified": True,
            }
            for read_id in READ_IDS
        ],
        "pagination_recovery": token_recovery,
        "valuation_recovery": valuation,
        "portfolio_recovery": portfolio,
        "valuation_specific_evidence_ready": True,
        "portfolio_interaction_evidence_ready": True,
        "gap_closed_by_this_recovery": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def write_recovery_artifact_exclusive(path: str | Path, payload: Mapping[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
