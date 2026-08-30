from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.research.models import AlpacaNewsWindowParameters, ResearchNeedType
from aic.research.plan_freeze import FrozenPlannerBatch, load_frozen_planner_batch


REVIEW_VERSION = "B3_REOPEN_BOUNDED_NEWS_REVIEW_v0_1"
PASS_STATUS = "B3_REOPEN_BOUNDED_NEWS_ZERO_CALL_PASS"
EXPECTED_GAP = "ALPACA_NEWS_PAGINATION_INCOMPLETE"
REPLACEMENT_REF = "ALPACA_NEWS_BOUNDED_TOP_N_SATISFIED"
EXPECTED_CANDIDATES = ("NVDA", "MSFT", "META")
EXPECTED_BLOCKED_STATUS = "B3_REOPEN_PAGINATED_PROVIDER_READ_BLOCKED"


class ReopenBoundedNewsReviewError(ValueError):
    pass


def _load_json(path: str | Path, *, label: str) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReopenBoundedNewsReviewError(f"unable to read {label}") from exc
    if not isinstance(payload, dict):
        raise ReopenBoundedNewsReviewError(f"{label} root must be an object")
    return payload


def _validate_self_hash(payload: Mapping[str, Any], *, label: str) -> str:
    observed = payload.get("artifact_hash")
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    if observed != expected:
        raise ReopenBoundedNewsReviewError(f"{label} self-hash mismatch")
    if not isinstance(observed, str):
        raise ReopenBoundedNewsReviewError(f"{label} artifact_hash missing")
    return observed


def _parse_utc(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReopenBoundedNewsReviewError(f"{field} must use canonical UTC Z form")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReopenBoundedNewsReviewError(f"{field} is not a valid timestamp") from exc
    return parsed.astimezone(UTC)


def _load_receipt_events(path: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    source = Path(path)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReopenBoundedNewsReviewError("unable to read blocked provider-read receipts") from exc
    if not lines:
        raise ReopenBoundedNewsReviewError("blocked provider-read receipts are empty")
    events: list[dict[str, Any]] = []
    hashes: list[str] = []
    for index, line in enumerate(lines, start=1):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReopenBoundedNewsReviewError(f"receipt line {index} is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ReopenBoundedNewsReviewError(f"receipt line {index} must be an object")
        observed = payload.get("receipt_hash")
        expected = canonical_sha256(payload, exclude_fields=("receipt_hash",))
        if observed != expected or not isinstance(observed, str):
            raise ReopenBoundedNewsReviewError(f"receipt line {index} self-hash mismatch")
        events.append(payload)
        hashes.append(observed)
    return events, hashes


def _news_need_rows(planner: FrozenPlannerBatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for frozen in planner.results:
        plan = frozen.research_plan
        needs = [need for need in plan.requested_needs if need.need_type is ResearchNeedType.NEED_ALPACA_NEWS_WINDOW]
        if len(needs) != 1:
            raise ReopenBoundedNewsReviewError(
                f"{plan.candidate_id} must contain exactly one frozen Alpaca news need"
            )
        need = needs[0]
        if not isinstance(need.parameters, AlpacaNewsWindowParameters):
            raise ReopenBoundedNewsReviewError("frozen Alpaca news parameter type drift")
        if need.max_items != 5:
            raise ReopenBoundedNewsReviewError(
                f"{plan.candidate_id} frozen news max_items must remain exactly 5"
            )
        rows.append(
            {
                "candidate_id": plan.candidate_id,
                "need_id": need.need_id,
                "question_id": need.question_id,
                "max_items": need.max_items,
                "window_start": need.parameters.window_start.astimezone(UTC),
                "window_end": need.parameters.window_end.astimezone(UTC),
                "research_cutoff": plan.research_cutoff.astimezone(UTC),
                "expected_evidence_role": need.expected_evidence_role,
            }
        )
    if tuple(row["candidate_id"] for row in rows) != EXPECTED_CANDIDATES:
        raise ReopenBoundedNewsReviewError("frozen planner candidate order drift")
    return rows


def _candidate_map(retrieval: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    candidates = retrieval.get("candidates")
    if not isinstance(candidates, list):
        raise ReopenBoundedNewsReviewError("historical retrieval candidates missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in candidates:
        if not isinstance(row, Mapping):
            raise ReopenBoundedNewsReviewError("historical candidate row must be an object")
        candidate = row.get("candidate")
        if not isinstance(candidate, str) or candidate in result:
            raise ReopenBoundedNewsReviewError("historical candidate identity invalid or duplicated")
        result[candidate] = row
    if tuple(candidate for candidate in EXPECTED_CANDIDATES if candidate in result) != EXPECTED_CANDIDATES:
        raise ReopenBoundedNewsReviewError("historical retrieval is missing frozen candidates")
    return result


def _review_candidate(*, frozen: Mapping[str, Any], historical: Mapping[str, Any]) -> dict[str, Any]:
    candidate_id = str(frozen["candidate_id"])
    receipts = historical.get("provider_receipts")
    if not isinstance(receipts, list):
        raise ReopenBoundedNewsReviewError(f"{candidate_id} provider receipts missing")
    news_receipts = [
        receipt
        for receipt in receipts
        if isinstance(receipt, Mapping)
        and receipt.get("provider") == "ALPACA"
        and receipt.get("endpoint_class") == "GET_NEWS_WINDOW"
    ]
    if len(news_receipts) != 1:
        raise ReopenBoundedNewsReviewError(f"{candidate_id} must have exactly one historical Alpaca news receipt")
    receipt = news_receipts[0]
    receipt_id = receipt.get("provider_read_receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise ReopenBoundedNewsReviewError(f"{candidate_id} Alpaca receipt id missing")
    if receipt.get("error") is not None or receipt.get("http_status") != 200:
        raise ReopenBoundedNewsReviewError(f"{candidate_id} historical Alpaca receipt is not successful")
    if type(receipt.get("record_count")) is not int or int(receipt["record_count"]) < 0:
        raise ReopenBoundedNewsReviewError(f"{candidate_id} historical Alpaca record_count invalid")

    research_evidence = historical.get("research_evidence")
    if not isinstance(research_evidence, Mapping):
        raise ReopenBoundedNewsReviewError(f"{candidate_id} historical research_evidence missing")
    evidence_items = research_evidence.get("evidence_items")
    if not isinstance(evidence_items, list):
        raise ReopenBoundedNewsReviewError(f"{candidate_id} historical evidence_items missing")
    news_items = [
        item
        for item in evidence_items
        if isinstance(item, Mapping)
        and item.get("provider") == "ALPACA"
        and item.get("source_type") == "ALPACA_NEWS"
        and item.get("provider_read_receipt_id") == receipt_id
    ]
    if len(news_items) != int(receipt["record_count"]):
        raise ReopenBoundedNewsReviewError(f"{candidate_id} Alpaca receipt/evidence record count mismatch")

    max_items = int(frozen["max_items"])
    if len(news_items) > max_items:
        raise ReopenBoundedNewsReviewError(f"{candidate_id} historical news evidence exceeds frozen max_items")
    evidence_ids: list[str] = []
    raw_hashes: list[str] = []
    for item in news_items:
        if item.get("entity_id") != candidate_id:
            raise ReopenBoundedNewsReviewError(f"{candidate_id} historical news evidence entity drift")
        evidence_id = item.get("evidence_id")
        raw_hash = item.get("raw_content_hash")
        if not isinstance(evidence_id, str) or not evidence_id:
            raise ReopenBoundedNewsReviewError(f"{candidate_id} historical news evidence id missing")
        if not isinstance(raw_hash, str) or len(raw_hash) != 64:
            raise ReopenBoundedNewsReviewError(f"{candidate_id} historical news raw hash invalid")
        for field in ("published_at", "observed_at", "as_of"):
            value = item.get(field)
            if value is not None and _parse_utc(value, field=f"{candidate_id}.{field}") > frozen["research_cutoff"]:
                raise ReopenBoundedNewsReviewError(f"{candidate_id} historical news evidence exceeds cutoff")
        if item.get("knowable_at_cutoff") is not True:
            raise ReopenBoundedNewsReviewError(f"{candidate_id} historical news evidence is not cutoff-eligible")
        evidence_ids.append(evidence_id)
        raw_hashes.append(raw_hash)
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ReopenBoundedNewsReviewError(f"{candidate_id} historical news evidence ids are duplicated")

    provider_exhausted = receipt.get("pagination_complete") is True
    bounded_request_satisfied = provider_exhausted or len(news_items) == max_items
    if not bounded_request_satisfied:
        raise ReopenBoundedNewsReviewError(
            f"{candidate_id} bounded news request is not satisfied by historical evidence"
        )

    return {
        "candidate_id": candidate_id,
        "need_id": frozen["need_id"],
        "question_id": frozen["question_id"],
        "expected_evidence_role": frozen["expected_evidence_role"],
        "frozen_max_items": max_items,
        "historical_news_evidence_count": len(news_items),
        "historical_provider_record_count": int(receipt["record_count"]),
        "provider_dataset_exhausted": provider_exhausted,
        "bounded_request_satisfied": True,
        "evidence_ids": evidence_ids,
        "raw_content_hashes": raw_hashes,
        "provider_read_receipt_id": receipt_id,
        "historical_pagination_flag": bool(receipt.get("pagination_complete")),
        "completeness_semantics": (
            "PROVIDER_EXHAUSTED"
            if provider_exhausted
            else "TOP_N_BOUND_SATISFIED_ADDITIONAL_PROVIDER_RECORDS_EXIST"
        ),
    }


def build_bounded_news_review(
    *,
    code_commit_sha: str,
    planner_path: str | Path,
    retrieval_path: str | Path,
    s00_path: str | Path,
    blocked_result_path: str | Path,
    blocked_receipts_path: str | Path,
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise ReopenBoundedNewsReviewError("code_commit_sha must be lowercase 40-char git SHA")

    planner = load_frozen_planner_batch(planner_path)
    s00 = _load_json(s00_path, label="B3 reopen S00 artifact")
    s00_hash = _validate_self_hash(s00, label="B3 reopen S00 artifact")
    if s00.get("required_source_ref_ids") != [EXPECTED_GAP]:
        raise ReopenBoundedNewsReviewError("B3 reopen S00 source-ref drift")

    retrieval = _load_json(retrieval_path, label="historical B3 retrieval artifact")
    retrieval_hash = _validate_self_hash(retrieval, label="historical B3 retrieval artifact")
    if retrieval.get("planner_artifact_hash") != planner.artifact_hash:
        raise ReopenBoundedNewsReviewError("historical B3 retrieval planner lineage mismatch")

    blocked = _load_json(blocked_result_path, label="blocked paginated provider-read result")
    blocked_hash = _validate_self_hash(blocked, label="blocked paginated provider-read result")
    if blocked.get("status") != EXPECTED_BLOCKED_STATUS:
        raise ReopenBoundedNewsReviewError("paginated provider-read result is not the expected blocked run")
    if blocked.get("provider_read_authorization_consumed") is not True:
        raise ReopenBoundedNewsReviewError("blocked run must record consumed provider-read authorization")
    if blocked.get("dispatch_attempts") != 17 or blocked.get("provider_reads") != 17:
        raise ReopenBoundedNewsReviewError("blocked run dispatch/read count drift")
    if blocked.get("error_class") != "AlpacaNewsReadError" or blocked.get("error") != "news.symbol must be trimmed":
        raise ReopenBoundedNewsReviewError("blocked run error signature drift")

    receipt_events, receipt_hashes = _load_receipt_events(blocked_receipts_path)
    if blocked.get("receipt_event_hashes") != receipt_hashes:
        raise ReopenBoundedNewsReviewError("blocked run receipt hash list mismatch")
    manifest_hash = canonical_sha256({"receipt_event_hashes": receipt_hashes})
    if blocked.get("receipt_manifest_hash") != manifest_hash:
        raise ReopenBoundedNewsReviewError("blocked run receipt manifest mismatch")
    dispatch_events = [event for event in receipt_events if event.get("event") == "PROVIDER_DISPATCH_ATTEMPT"]
    response_events = [event for event in receipt_events if event.get("event") == "PROVIDER_RESPONSE_RECEIVED"]
    failed_events = [event for event in receipt_events if event.get("event") == "PROVIDER_DISPATCH_FAILED"]
    if len(dispatch_events) != 17 or len(response_events) != 17 or failed_events:
        raise ReopenBoundedNewsReviewError(
            "blocked run must show 17 dispatched responses and no transport-level dispatch failure"
        )

    historical_candidates = _candidate_map(retrieval)
    candidate_reviews = [
        _review_candidate(frozen=frozen, historical=historical_candidates[str(frozen["candidate_id"])])
        for frozen in _news_need_rows(planner)
    ]

    artifact: dict[str, Any] = {
        "artifact_version": REVIEW_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_s00_artifact_hash": s00_hash,
        "source_historical_b3_retrieval_artifact_hash": retrieval_hash,
        "source_frozen_planner_artifact_hash": planner.artifact_hash,
        "source_blocked_paginated_provider_read_result_hash": blocked_hash,
        "source_blocked_receipt_manifest_hash": manifest_hash,
        "blocked_run_dispatch_attempts": 17,
        "blocked_run_provider_responses_received": 17,
        "blocked_run_transport_failures": 0,
        "blocked_run_local_normalization_error": "news.symbol must be trimmed",
        "superseded_source_ref_id": EXPECTED_GAP,
        "replacement_source_ref_id": REPLACEMENT_REF,
        "candidate_reviews": candidate_reviews,
        "gap_closed": all(row["bounded_request_satisfied"] for row in candidate_reviews),
        "closure_basis": "FROZEN_RESEARCH_NEED_MAX_ITEMS_BOUND_NOT_FULL_PROVIDER_DATASET_EXHAUSTION",
        "provider_dataset_exhaustion_required": False,
        "new_provider_reads": 0,
        "model_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": "B3_REOPEN_BOUNDED_NEWS_CLAIM_RECONCILIATION_ZERO_CALL",
    }
    if artifact["gap_closed"] is not True:
        raise ReopenBoundedNewsReviewError("bounded-news review did not close the pagination source gap")
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
