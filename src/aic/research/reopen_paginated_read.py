from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from pydantic import BaseModel, ConfigDict, model_validator

from aic.data.providers.alpaca_cli_news import CLI_PROFILE_CREDENTIAL_PLACEHOLDER
from aic.data.providers.alpaca_news import ALPACA_NEWS_ENDPOINT, AlpacaNewsReadError, AlpacaNewsTransport
from aic.data.providers.alpaca_news_reopen import (
    MAX_REOPEN_NEWS_PAGES,
    ReopenAlpacaCliNewsTransport,
    read_alpaca_news_window_for_reopen,
)
from aic.domain.canonical import canonical_sha256
from aic.research.reopen_pagination_preflight import PREFLIGHT_STATUS, inspect_alpaca_news_help


AUTHORITY_VERSION = "B3_REOPEN_PAGINATED_PROVIDER_READ_AUTHORITY_v0_1"
AUTHORIZATION_ARTIFACT_VERSION = "B3_REOPEN_PAGINATED_PROVIDER_READ_AUTHORIZATION_v0_1"
RESULT_ARTIFACT_VERSION = "B3_REOPEN_PAGINATED_PROVIDER_READ_RESULT_v0_1"
RECEIPT_EVENT_VERSION = "B3_REOPEN_PAGINATED_PROVIDER_READ_RECEIPT_EVENT_v0_1"
SUCCESS_STATUS = "B3_REOPEN_PAGINATED_PROVIDER_READ_COMPLETE"
PARTIAL_STATUS = "B3_REOPEN_PAGINATED_PROVIDER_READ_PARTIAL"
BLOCKED_STATUS = "B3_REOPEN_PAGINATED_PROVIDER_READ_BLOCKED"
EXPECTED_REQUIRED_REF = "ALPACA_NEWS_PAGINATION_INCOMPLETE"
EXPECTED_CANDIDATES = ("NVDA", "MSFT", "META")


class ReopenPaginatedReadError(RuntimeError):
    pass


class ReopenPaginatedReadAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authority_version: str
    owner_approval_id: str
    source_zero_call_preflight_hash: str
    source_s00_artifact_hash: str
    source_production_judge_result_hash: str
    source_research_reopen_request_hash: str
    required_source_ref_ids: tuple[str, ...]
    approved_candidate_ids: tuple[str, ...]
    approved_provider: str
    approved_auth_mode: str
    approved_max_pages_per_candidate: int
    approved_page_size_max: int
    approved_provider_dispatch_attempts_max: int
    authorization_consumption_rule: str
    automatic_retries: int
    rerun_authorized: bool
    model_calls_authorized: int
    broker_writes_authorized: int
    alpaca_orders_authorized: int
    live_money: str

    @model_validator(mode="after")
    def _contract(self):
        if self.authority_version != AUTHORITY_VERSION:
            raise ValueError("unexpected paginated provider-read authority version")
        if self.required_source_ref_ids != (EXPECTED_REQUIRED_REF,):
            raise ValueError("provider-read authority source-ref drift")
        if self.approved_candidate_ids != EXPECTED_CANDIDATES:
            raise ValueError("provider-read authority candidate scope drift")
        if self.approved_provider != "ALPACA_NEWS":
            raise ValueError("provider-read authority provider drift")
        if self.approved_auth_mode != "CLI_PROFILE:paper":
            raise ValueError("provider-read authority requires paper CLI profile")
        if self.approved_max_pages_per_candidate != MAX_REOPEN_NEWS_PAGES:
            raise ValueError("provider-read authority page bound drift")
        if self.approved_page_size_max != 5:
            raise ValueError("provider-read authority page-size bound drift")
        if self.approved_provider_dispatch_attempts_max != 18:
            raise ValueError("provider-read authority global dispatch bound drift")
        if self.authorization_consumption_rule != "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT":
            raise ValueError("provider-read authority consumption rule drift")
        if self.automatic_retries != 0 or self.rerun_authorized is not False:
            raise ValueError("provider-read authority retry/rerun drift")
        if (
            self.model_calls_authorized != 0
            or self.broker_writes_authorized != 0
            or self.alpaca_orders_authorized != 0
        ):
            raise ValueError("provider-read authority side-effect scope drift")
        if self.live_money != "PROHIBITED":
            raise ValueError("provider-read authority live-money boundary drift")
        return self


def load_read_authority(path: str | Path) -> ReopenPaginatedReadAuthority:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReopenPaginatedReadError("unable to read paginated provider-read authority") from exc
    if not isinstance(payload, dict):
        raise ReopenPaginatedReadError("provider-read authority root must be an object")
    return ReopenPaginatedReadAuthority.model_validate(payload)


def _load_json_object(path: str | Path, *, label: str) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReopenPaginatedReadError(f"unable to read {label}") from exc
    if not isinstance(payload, dict):
        raise ReopenPaginatedReadError(f"{label} root must be an object")
    return payload


def load_approved_preflight(
    path: str | Path,
    *,
    authority: ReopenPaginatedReadAuthority,
) -> dict[str, Any]:
    payload = _load_json_object(path, label="zero-call pagination preflight")
    observed = payload.get("artifact_hash")
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    if observed != expected:
        raise ReopenPaginatedReadError("zero-call pagination preflight self-hash mismatch")
    if observed != authority.source_zero_call_preflight_hash:
        raise ReopenPaginatedReadError("zero-call pagination preflight is not owner-approved")
    if payload.get("status") != PREFLIGHT_STATUS:
        raise ReopenPaginatedReadError("zero-call pagination preflight status drift")
    if payload.get("source_s00_artifact_hash") != authority.source_s00_artifact_hash:
        raise ReopenPaginatedReadError("zero-call pagination preflight S00 lineage drift")
    if payload.get("source_production_judge_result_hash") != authority.source_production_judge_result_hash:
        raise ReopenPaginatedReadError("zero-call pagination preflight Judge lineage drift")
    if payload.get("source_research_reopen_request_hash") != authority.source_research_reopen_request_hash:
        raise ReopenPaginatedReadError("zero-call pagination preflight reopen lineage drift")
    if payload.get("required_source_ref_ids") != list(authority.required_source_ref_ids):
        raise ReopenPaginatedReadError("zero-call pagination preflight source-ref drift")
    if payload.get("provider_reads_authorized") is not False:
        raise ReopenPaginatedReadError("zero-call pagination preflight unexpectedly authorizes provider reads")
    if payload.get("planned_provider_reads_max") != authority.approved_provider_dispatch_attempts_max:
        raise ReopenPaginatedReadError("zero-call pagination preflight dispatch ceiling drift")
    if payload.get("max_pages_per_candidate") != authority.approved_max_pages_per_candidate:
        raise ReopenPaginatedReadError("zero-call pagination preflight page ceiling drift")
    if payload.get("model_calls") != 0 or payload.get("provider_reads") != 0:
        raise ReopenPaginatedReadError("zero-call pagination preflight is not zero-call")
    if (
        payload.get("broker_writes") != 0
        or payload.get("alpaca_orders") != 0
        or payload.get("live_money") != "PROHIBITED"
    ):
        raise ReopenPaginatedReadError("zero-call pagination preflight side-effect boundary drift")
    windows = payload.get("candidate_news_windows")
    if not isinstance(windows, list) or len(windows) != 3:
        raise ReopenPaginatedReadError("zero-call pagination preflight candidate windows missing")
    candidate_ids: list[str] = []
    for row in windows:
        if not isinstance(row, Mapping):
            raise ReopenPaginatedReadError("zero-call pagination preflight window must be an object")
        candidate_ids.append(str(row.get("candidate_id")))
        page_size = row.get("page_size")
        if type(page_size) is not int or not 1 <= page_size <= authority.approved_page_size_max:
            raise ReopenPaginatedReadError("zero-call pagination preflight page size drift")
        if row.get("max_pages") != authority.approved_max_pages_per_candidate:
            raise ReopenPaginatedReadError("zero-call pagination preflight candidate page bound drift")
        if row.get("planned_provider_reads_max") != authority.approved_max_pages_per_candidate:
            raise ReopenPaginatedReadError("zero-call pagination preflight candidate dispatch bound drift")
    if tuple(candidate_ids) != authority.approved_candidate_ids:
        raise ReopenPaginatedReadError("zero-call pagination preflight candidate order drift")
    return payload


def verify_cli_help_still_bound(preflight: Mapping[str, Any]) -> dict[str, Any]:
    observed = inspect_alpaca_news_help()
    if observed.get("alpaca_news_help_sha256") != preflight.get("alpaca_news_help_sha256"):
        raise ReopenPaginatedReadError("Alpaca CLI news help changed since zero-call preflight")
    if observed.get("required_news_flags") != preflight.get("required_news_flags"):
        raise ReopenPaginatedReadError("Alpaca CLI required news flags changed since zero-call preflight")
    if observed.get("page_token_flag_present") is not True:
        raise ReopenPaginatedReadError("Alpaca CLI page-token support is unavailable")
    return observed


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_json_exclusive_fsync(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _append_jsonl_fsync(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with path.open("ab") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _with_receipt_hash(payload: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(payload)
    body["receipt_hash"] = canonical_sha256(body)
    return body


@dataclass
class ProviderDispatchTracker:
    authority_hash: str
    preflight_hash: str
    receipt_path: Path
    max_dispatch_attempts: int
    dispatch_attempts: int = 0
    candidate_attempts: dict[str, int] = field(default_factory=dict)
    receipt_hashes: list[str] = field(default_factory=list)

    def append_event(self, payload: Mapping[str, Any]) -> str:
        event = _with_receipt_hash(payload)
        _append_jsonl_fsync(self.receipt_path, event)
        receipt_hash = str(event["receipt_hash"])
        self.receipt_hashes.append(receipt_hash)
        return receipt_hash

    def begin_dispatch(self, *, candidate_id: str, query: Mapping[str, str]) -> tuple[int, int, str]:
        if self.dispatch_attempts >= self.max_dispatch_attempts:
            raise ReopenPaginatedReadError("approved global provider dispatch ceiling exhausted")
        candidate_count = self.candidate_attempts.get(candidate_id, 0)
        if candidate_count >= MAX_REOPEN_NEWS_PAGES:
            raise ReopenPaginatedReadError("approved candidate provider dispatch ceiling exhausted")
        self.dispatch_attempts += 1
        candidate_count += 1
        self.candidate_attempts[candidate_id] = candidate_count
        page_token = query.get("page_token")
        event = {
            "receipt_event_version": RECEIPT_EVENT_VERSION,
            "event": "PROVIDER_DISPATCH_ATTEMPT",
            "authority_hash": self.authority_hash,
            "preflight_hash": self.preflight_hash,
            "global_dispatch_attempt": self.dispatch_attempts,
            "candidate_dispatch_attempt": candidate_count,
            "candidate_id": candidate_id,
            "endpoint": ALPACA_NEWS_ENDPOINT,
            "query_hash": canonical_sha256(dict(query)),
            "page_token_present": page_token is not None,
            "page_token_hash": None if page_token is None else hashlib.sha256(page_token.encode("utf-8")).hexdigest(),
            "attempted_at_utc": _utc_now(),
        }
        receipt_hash = self.append_event(event)
        return self.dispatch_attempts, candidate_count, receipt_hash


@dataclass(frozen=True)
class DurableBudgetedAlpacaNewsTransport:
    candidate_id: str
    base_transport: AlpacaNewsTransport
    tracker: ProviderDispatchTracker

    def get(
        self,
        *,
        endpoint: str,
        query: Mapping[str, str],
        api_key_id: str,
        api_secret_key: str,
    ) -> tuple[int, bytes]:
        if endpoint != ALPACA_NEWS_ENDPOINT:
            raise AlpacaNewsReadError("authorized reopen provider-read endpoint drift")
        global_attempt, candidate_attempt, attempt_receipt_hash = self.tracker.begin_dispatch(
            candidate_id=self.candidate_id,
            query=query,
        )
        try:
            status, raw = self.base_transport.get(
                endpoint=endpoint,
                query=query,
                api_key_id=api_key_id,
                api_secret_key=api_secret_key,
            )
        except Exception as exc:
            self.tracker.append_event(
                {
                    "receipt_event_version": RECEIPT_EVENT_VERSION,
                    "event": "PROVIDER_DISPATCH_FAILED",
                    "authority_hash": self.tracker.authority_hash,
                    "preflight_hash": self.tracker.preflight_hash,
                    "global_dispatch_attempt": global_attempt,
                    "candidate_dispatch_attempt": candidate_attempt,
                    "candidate_id": self.candidate_id,
                    "attempt_receipt_hash": attempt_receipt_hash,
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                    "failed_at_utc": _utc_now(),
                }
            )
            raise
        self.tracker.append_event(
            {
                "receipt_event_version": RECEIPT_EVENT_VERSION,
                "event": "PROVIDER_RESPONSE_RECEIVED",
                "authority_hash": self.tracker.authority_hash,
                "preflight_hash": self.tracker.preflight_hash,
                "global_dispatch_attempt": global_attempt,
                "candidate_dispatch_attempt": candidate_attempt,
                "candidate_id": self.candidate_id,
                "attempt_receipt_hash": attempt_receipt_hash,
                "http_status": status,
                "response_bytes": len(raw),
                "raw_payload_sha256": hashlib.sha256(raw).hexdigest(),
                "received_at_utc": _utc_now(),
            }
        )
        return status, raw


def build_execution_authorization_artifact(
    *,
    authority: ReopenPaginatedReadAuthority,
    authority_hash: str,
    preflight: Mapping[str, Any],
    code_commit_sha: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_version": AUTHORIZATION_ARTIFACT_VERSION,
        "status": "AUTHORIZED_UNCONSUMED",
        "owner_approval_id": authority.owner_approval_id,
        "authority_hash": authority_hash,
        "source_zero_call_preflight_hash": preflight["artifact_hash"],
        "source_s00_artifact_hash": authority.source_s00_artifact_hash,
        "source_production_judge_result_hash": authority.source_production_judge_result_hash,
        "source_research_reopen_request_hash": authority.source_research_reopen_request_hash,
        "approved_candidate_ids": list(authority.approved_candidate_ids),
        "approved_provider_dispatch_attempts_max": authority.approved_provider_dispatch_attempts_max,
        "approved_max_pages_per_candidate": authority.approved_max_pages_per_candidate,
        "approved_page_size_max": authority.approved_page_size_max,
        "approved_auth_mode": authority.approved_auth_mode,
        "authorization_consumption_rule": authority.authorization_consumption_rule,
        "automatic_retries": 0,
        "rerun_authorized": False,
        "model_calls_authorized": 0,
        "broker_writes_authorized": 0,
        "alpaca_orders_authorized": 0,
        "live_money": "PROHIBITED",
        "code_commit_sha": code_commit_sha,
        "execution_authorization_recorded_at_utc": _utc_now(),
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReopenPaginatedReadError("approved news window must use canonical UTC Z form")
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def _result_common(
    *,
    authority: ReopenPaginatedReadAuthority,
    authority_hash: str,
    authorization_artifact: Mapping[str, Any],
    preflight: Mapping[str, Any],
    code_commit_sha: str,
    tracker: ProviderDispatchTracker,
) -> dict[str, Any]:
    return {
        "artifact_version": RESULT_ARTIFACT_VERSION,
        "owner_approval_id": authority.owner_approval_id,
        "authority_hash": authority_hash,
        "execution_authorization_artifact_hash": authorization_artifact["artifact_hash"],
        "source_zero_call_preflight_hash": preflight["artifact_hash"],
        "source_s00_artifact_hash": authority.source_s00_artifact_hash,
        "source_production_judge_result_hash": authority.source_production_judge_result_hash,
        "source_research_reopen_request_hash": authority.source_research_reopen_request_hash,
        "required_source_ref_ids": list(authority.required_source_ref_ids),
        "code_commit_sha": code_commit_sha,
        "approved_candidate_ids": list(authority.approved_candidate_ids),
        "approved_provider_dispatch_attempts_max": authority.approved_provider_dispatch_attempts_max,
        "dispatch_attempts": tracker.dispatch_attempts,
        "provider_reads": tracker.dispatch_attempts,
        "provider_read_authorization_consumed": tracker.dispatch_attempts > 0,
        "receipt_event_hashes": list(tracker.receipt_hashes),
        "receipt_manifest_hash": canonical_sha256({"receipt_event_hashes": tracker.receipt_hashes}),
        "automatic_retries": 0,
        "rerun_authorized": False,
        "model_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }


def execute_paginated_provider_reads(
    *,
    authority: ReopenPaginatedReadAuthority,
    preflight: Mapping[str, Any],
    code_commit_sha: str,
    authorization_path: Path,
    receipts_path: Path,
    result_path: Path,
    base_transport_factory: Callable[[str], AlpacaNewsTransport] | None = None,
) -> dict[str, Any]:
    for path in (authorization_path, receipts_path, result_path):
        if path.exists():
            raise ReopenPaginatedReadError(f"provider-read evidence path must be fresh: {path}")

    authority_hash = canonical_sha256(authority.model_dump(mode="json", exclude_none=False))
    authorization_artifact = build_execution_authorization_artifact(
        authority=authority,
        authority_hash=authority_hash,
        preflight=preflight,
        code_commit_sha=code_commit_sha,
    )
    _write_json_exclusive_fsync(authorization_path, authorization_artifact)

    tracker = ProviderDispatchTracker(
        authority_hash=authority_hash,
        preflight_hash=str(preflight["artifact_hash"]),
        receipt_path=receipts_path,
        max_dispatch_attempts=authority.approved_provider_dispatch_attempts_max,
    )
    candidate_results: list[dict[str, Any]] = []
    factory = base_transport_factory or (lambda _candidate_id: ReopenAlpacaCliNewsTransport(profile="paper"))

    try:
        for row in preflight["candidate_news_windows"]:
            candidate_id = str(row["candidate_id"])
            transport = DurableBudgetedAlpacaNewsTransport(
                candidate_id=candidate_id,
                base_transport=factory(candidate_id),
                tracker=tracker,
            )
            read = read_alpaca_news_window_for_reopen(
                symbol=candidate_id,
                window_start=_parse_utc(str(row["window_start"])),
                window_end=_parse_utc(str(row["window_end"])),
                research_cutoff=_parse_utc(str(row["research_cutoff"])),
                page_size=int(row["page_size"]),
                max_pages=int(row["max_pages"]),
                api_key_id=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
                api_secret_key=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
                transport=transport,
            )
            candidate_results.append(
                {
                    "candidate_id": candidate_id,
                    "need_id": row["need_id"],
                    "status": "COMPLETE" if read.pagination_complete else "PARTIAL",
                    "page_count": read.page_count,
                    "article_count": len(read.articles),
                    "pagination_complete": read.pagination_complete,
                    "aggregate_payload_hash": read.aggregate_payload_hash,
                    "read": read.model_dump(mode="json", exclude_none=False, warnings=False),
                }
            )
    except Exception as exc:
        result = {
            **_result_common(
                authority=authority,
                authority_hash=authority_hash,
                authorization_artifact=authorization_artifact,
                preflight=preflight,
                code_commit_sha=code_commit_sha,
                tracker=tracker,
            ),
            "status": BLOCKED_STATUS,
            "candidate_results": candidate_results,
            "error_class": type(exc).__name__,
            "error": str(exc),
            "gap_closed": False,
            "next_gate": "B3_REOPEN_PROVIDER_READ_BLOCKED_OWNER_REVIEW",
        }
        result["artifact_hash"] = canonical_sha256(result)
        _write_json_exclusive_fsync(result_path, result)
        return result

    all_complete = all(row["pagination_complete"] for row in candidate_results)
    result = {
        **_result_common(
            authority=authority,
            authority_hash=authority_hash,
            authorization_artifact=authorization_artifact,
            preflight=preflight,
            code_commit_sha=code_commit_sha,
            tracker=tracker,
        ),
        "status": SUCCESS_STATUS if all_complete else PARTIAL_STATUS,
        "candidate_results": candidate_results,
        "error_class": None,
        "error": None,
        "gap_closed": all_complete,
        "next_gate": (
            "B3_REOPEN_PAGINATION_GAP_CLOSURE_VALIDATION"
            if all_complete
            else "B3_REOPEN_PAGINATION_STILL_INCOMPLETE_OWNER_REVIEW"
        ),
    }
    result["artifact_hash"] = canonical_sha256(result)
    _write_json_exclusive_fsync(result_path, result)
    return result


def summarize_result(result: Mapping[str, Any]) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    rows = result.get("candidate_results")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping):
                summaries.append(
                    {
                        "candidate_id": row.get("candidate_id"),
                        "status": row.get("status"),
                        "page_count": row.get("page_count"),
                        "article_count": row.get("article_count"),
                        "pagination_complete": row.get("pagination_complete"),
                        "aggregate_payload_hash": row.get("aggregate_payload_hash"),
                    }
                )
    return {
        "status": result.get("status"),
        "artifact_hash": result.get("artifact_hash"),
        "dispatch_attempts": result.get("dispatch_attempts"),
        "provider_reads": result.get("provider_reads"),
        "provider_read_authorization_consumed": result.get("provider_read_authorization_consumed"),
        "receipt_manifest_hash": result.get("receipt_manifest_hash"),
        "gap_closed": result.get("gap_closed"),
        "next_gate": result.get("next_gate"),
        "candidate_results": summaries,
        "model_calls": result.get("model_calls"),
        "broker_writes": result.get("broker_writes"),
        "alpaca_orders": result.get("alpaca_orders"),
        "live_money": result.get("live_money"),
        "error_class": result.get("error_class"),
        "error": result.get("error"),
    }
