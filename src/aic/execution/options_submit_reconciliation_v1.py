from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from aic.domain.canonical import canonical_sha256
from aic.risk.alpaca_b5_runtime_v1 import AlpacaReadOnlyCredentials

from .options_submit_authority_v1 import (
    AlpacaPaperOptionOrderRequest,
    B6BrokerWriteLease,
    B6SubmittingMarker,
    PAPER_ORDER_URL,
    consume_b6_broker_write_lease,
)


RECONCILIATION_BASE_URL = "https://paper-api.alpaca.markets/v2/orders:by_client_order_id"


class B6SubmitRuntimeError(RuntimeError):
    """Fail-closed B6 submit/reconciliation runtime contract error."""


@dataclass(frozen=True)
class B6SubmitAttemptReceipt:
    receipt_id: str
    intent_id: str
    lease_id: str
    client_order_id: str
    request_payload_hash: str
    started_at: datetime
    completed_at: datetime
    outcome: str
    http_status: int | None
    response_sha256: str | None
    broker_order_id: str | None
    broker_state: str | None
    broker_post_count: int = 1
    automatic_retry: bool = False
    blind_retry: bool = False


@dataclass(frozen=True)
class B6ReconciliationReceipt:
    receipt_id: str
    intent_id: str
    client_order_id: str
    url: str
    checked_at: datetime
    outcome: str
    http_status: int | None
    response_sha256: str | None
    broker_order_id: str | None
    broker_state: str | None
    reconciliation_get_count: int = 1
    broker_post_count: int = 0


@dataclass(frozen=True)
class B6SubmitRunResult:
    consumed_lease: B6BrokerWriteLease
    submit_receipt: B6SubmitAttemptReceipt
    reconciliation_receipt: B6ReconciliationReceipt | None
    final_state: str
    broker_post_count: int
    reconciliation_get_count: int
    automatic_retry: bool = False
    blind_retry: bool = False
    live_execution: bool = False
    model_calls: int = 0


PostTransport = Callable[[str, Mapping[str, str], bytes, float], tuple[int, bytes]]
GetTransport = Callable[[str, Mapping[str, str], float], tuple[int, bytes]]
DurabilityHook = Callable[[B6BrokerWriteLease, B6SubmittingMarker, AlpacaPaperOptionOrderRequest], None]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise B6SubmitRuntimeError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _response_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _decode_object(body: bytes) -> Mapping[str, Any] | None:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _order_identity(payload: Mapping[str, Any] | None, *, expected_client_order_id: str) -> tuple[bool, str | None, str | None]:
    if payload is None:
        return False, None, None
    client_order_id = payload.get("client_order_id")
    if client_order_id != expected_client_order_id:
        return False, None, None
    order_id = payload.get("id")
    status = payload.get("status")
    broker_order_id = order_id if isinstance(order_id, str) and order_id.strip() else None
    broker_state = status if isinstance(status, str) and status.strip() else None
    return True, broker_order_id, broker_state


def _validate_submit_binding(
    *,
    lease: B6BrokerWriteLease,
    submitting: B6SubmittingMarker,
    request: AlpacaPaperOptionOrderRequest,
) -> None:
    if lease.status != "ISSUED" or lease.consumed_at is not None or lease.execution_authority is not True:
        raise B6SubmitRuntimeError("broker-write lease is not current and executable")
    if lease.max_broker_calls != 1:
        raise B6SubmitRuntimeError("broker-write lease call bound drift")
    if lease.method != "POST" or lease.url != PAPER_ORDER_URL:
        raise B6SubmitRuntimeError("broker-write lease endpoint drift")
    if submitting.state != "SUBMITTING" or submitting.submit_attempt_count != 1:
        raise B6SubmitRuntimeError("durable SUBMITTING marker is not valid")
    if submitting.broker_writes_observed != 0:
        raise B6SubmitRuntimeError("SUBMITTING marker already records a broker write")
    if submitting.lease_id != lease.lease_id or submitting.intent_id != lease.intent_id:
        raise B6SubmitRuntimeError("SUBMITTING marker lease/intent mismatch")
    if submitting.client_order_id != lease.client_order_id:
        raise B6SubmitRuntimeError("SUBMITTING marker client_order_id mismatch")
    if request.method != "POST" or request.url != PAPER_ORDER_URL:
        raise B6SubmitRuntimeError("order request is not exact PAPER POST endpoint")
    if request.broker_write_authority_lease_id != lease.lease_id:
        raise B6SubmitRuntimeError("order request lease binding mismatch")
    if request.client_order_id != lease.client_order_id:
        raise B6SubmitRuntimeError("order request client_order_id mismatch")
    if request.max_send_attempts != 1 or request.automatic_price_chase or request.blind_retry or request.live_execution:
        raise B6SubmitRuntimeError("order request retry/live safety contract drift")
    if canonical_sha256(request.payload) != request.payload_hash:
        raise B6SubmitRuntimeError("order request payload hash drift")
    if request.payload.get("client_order_id") != lease.client_order_id:
        raise B6SubmitRuntimeError("order payload client_order_id mismatch")


def _stdlib_post(url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float) -> tuple[int, bytes]:
    request = Request(url=url, headers=dict(headers), data=body, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310 - exact PAPER host is validated before dispatch
            return int(response.status), response.read()
    except HTTPError as exc:
        return int(exc.code), exc.read()
    except (URLError, TimeoutError, OSError) as exc:
        raise B6SubmitRuntimeError("Alpaca PAPER submit transport outcome unknown") from exc


def _stdlib_get(url: str, headers: Mapping[str, str], timeout_seconds: float) -> tuple[int, bytes]:
    request = Request(url=url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310 - exact PAPER host is constructed locally
            return int(response.status), response.read()
    except HTTPError as exc:
        return int(exc.code), exc.read()
    except (URLError, TimeoutError, OSError) as exc:
        raise B6SubmitRuntimeError("Alpaca reconciliation transport failed") from exc


def _submit_receipt(
    *,
    lease: B6BrokerWriteLease,
    request: AlpacaPaperOptionOrderRequest,
    started_at: datetime,
    completed_at: datetime,
    outcome: str,
    http_status: int | None,
    body: bytes | None,
    broker_order_id: str | None,
    broker_state: str | None,
) -> B6SubmitAttemptReceipt:
    response_sha = None if body is None else _response_hash(body)
    identity = {
        "intent_id": lease.intent_id,
        "lease_id": lease.lease_id,
        "client_order_id": lease.client_order_id,
        "request_payload_hash": request.payload_hash,
        "started_at": started_at,
        "completed_at": completed_at,
        "outcome": outcome,
        "http_status": http_status,
        "response_sha256": response_sha,
        "broker_order_id": broker_order_id,
        "broker_state": broker_state,
        "broker_post_count": 1,
    }
    digest = canonical_sha256(identity)
    return B6SubmitAttemptReceipt(
        receipt_id=f"B6SUBMIT:{digest[:24]}",
        intent_id=lease.intent_id,
        lease_id=lease.lease_id,
        client_order_id=lease.client_order_id,
        request_payload_hash=request.payload_hash,
        started_at=started_at,
        completed_at=completed_at,
        outcome=outcome,
        http_status=http_status,
        response_sha256=response_sha,
        broker_order_id=broker_order_id,
        broker_state=broker_state,
    )


def _reconcile(
    *,
    intent_id: str,
    client_order_id: str,
    headers: Mapping[str, str],
    transport: GetTransport,
    timeout_seconds: float,
    clock: Clock,
) -> B6ReconciliationReceipt:
    url = RECONCILIATION_BASE_URL + "?" + urlencode({"client_order_id": client_order_id})
    checked_at = _aware(clock(), field="reconciliation clock")
    http_status: int | None = None
    body: bytes | None = None
    broker_order_id: str | None = None
    broker_state: str | None = None
    outcome = "UNKNOWN"
    try:
        http_status, body = transport(url, headers, timeout_seconds)
        if http_status == 200:
            matched, broker_order_id, broker_state = _order_identity(
                _decode_object(body), expected_client_order_id=client_order_id
            )
            outcome = "FOUND" if matched else "UNKNOWN"
        elif http_status == 404:
            outcome = "NOT_FOUND"
        else:
            outcome = "UNKNOWN"
    except (B6SubmitRuntimeError, TimeoutError, OSError, URLError):
        outcome = "UNKNOWN"

    response_sha = None if body is None else _response_hash(body)
    identity = {
        "intent_id": intent_id,
        "client_order_id": client_order_id,
        "url": url,
        "checked_at": checked_at,
        "outcome": outcome,
        "http_status": http_status,
        "response_sha256": response_sha,
        "broker_order_id": broker_order_id,
        "broker_state": broker_state,
        "reconciliation_get_count": 1,
    }
    digest = canonical_sha256(identity)
    return B6ReconciliationReceipt(
        receipt_id=f"B6RECON:{digest[:24]}",
        intent_id=intent_id,
        client_order_id=client_order_id,
        url=url,
        checked_at=checked_at,
        outcome=outcome,
        http_status=http_status,
        response_sha256=response_sha,
        broker_order_id=broker_order_id,
        broker_state=broker_state,
    )


def execute_b6_single_paper_submit(
    *,
    credentials: AlpacaReadOnlyCredentials,
    lease: B6BrokerWriteLease,
    submitting: B6SubmittingMarker,
    request: AlpacaPaperOptionOrderRequest,
    persist_before_post: DurabilityHook,
    post_transport: PostTransport | None = None,
    reconciliation_transport: GetTransport | None = None,
    clock: Clock = _utc_now,
    timeout_seconds: float = 10.0,
    execute_broker_write: bool = False,
) -> B6SubmitRunResult:
    """Execute exactly one authorized PAPER order POST and reconcile ambiguity by GET.

    The write lease is consumed and the SUBMITTING state must be durably persisted
    before the network dispatch. No code path issues a second POST. Ambiguous submit
    outcomes reconcile only through Alpaca's GET-by-client-order-id endpoint.
    """

    if not execute_broker_write:
        raise B6SubmitRuntimeError("explicit broker-write execution flag is required")
    if timeout_seconds <= 0:
        raise B6SubmitRuntimeError("timeout_seconds must be positive")
    _validate_submit_binding(lease=lease, submitting=submitting, request=request)
    headers = credentials.headers()
    headers = {**headers, "Content-Type": "application/json"}

    started_at = _aware(clock(), field="submit clock")
    if started_at < submitting.broker_call_started_at.astimezone(timezone.utc):
        raise B6SubmitRuntimeError("submit runtime clock precedes durable SUBMITTING marker")
    consumed_lease = consume_b6_broker_write_lease(lease, consumed_at=started_at)

    # Persistence is intentionally mandatory and happens before the only POST.
    persist_before_post(consumed_lease, submitting, request)

    post = post_transport or _stdlib_post
    reconcile_get = reconciliation_transport or _stdlib_get
    body_bytes = json.dumps(request.payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    http_status: int | None = None
    response_body: bytes | None = None
    broker_order_id: str | None = None
    broker_state: str | None = None
    outcome = "UNKNOWN"
    try:
        http_status, response_body = post(request.url, headers, body_bytes, timeout_seconds)
        if 200 <= http_status < 300:
            matched, broker_order_id, broker_state = _order_identity(
                _decode_object(response_body), expected_client_order_id=lease.client_order_id
            )
            outcome = "KNOWN_ACCEPTED" if matched else "UNKNOWN"
        elif 400 <= http_status < 500:
            outcome = "KNOWN_REJECTED"
        else:
            outcome = "UNKNOWN"
    except (B6SubmitRuntimeError, TimeoutError, OSError, URLError):
        outcome = "UNKNOWN"

    completed_at = _aware(clock(), field="submit completion clock")
    submit_receipt = _submit_receipt(
        lease=lease,
        request=request,
        started_at=started_at,
        completed_at=completed_at,
        outcome=outcome,
        http_status=http_status,
        body=response_body,
        broker_order_id=broker_order_id,
        broker_state=broker_state,
    )

    if outcome == "KNOWN_ACCEPTED":
        return B6SubmitRunResult(
            consumed_lease=consumed_lease,
            submit_receipt=submit_receipt,
            reconciliation_receipt=None,
            final_state="BROKER_KNOWN",
            broker_post_count=1,
            reconciliation_get_count=0,
        )
    if outcome == "KNOWN_REJECTED":
        return B6SubmitRunResult(
            consumed_lease=consumed_lease,
            submit_receipt=submit_receipt,
            reconciliation_receipt=None,
            final_state="BROKER_REJECTED",
            broker_post_count=1,
            reconciliation_get_count=0,
        )

    reconciliation = _reconcile(
        intent_id=lease.intent_id,
        client_order_id=lease.client_order_id,
        headers=headers,
        transport=reconcile_get,
        timeout_seconds=timeout_seconds,
        clock=clock,
    )
    final_state = "BROKER_KNOWN" if reconciliation.outcome == "FOUND" else "SUBMIT_OUTCOME_UNKNOWN"
    return B6SubmitRunResult(
        consumed_lease=consumed_lease,
        submit_receipt=submit_receipt,
        reconciliation_receipt=reconciliation,
        final_state=final_state,
        broker_post_count=1,
        reconciliation_get_count=1,
    )
