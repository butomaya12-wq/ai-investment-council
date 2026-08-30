from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter_ns
from typing import Any, Mapping

from aic.council.eval_cost import load_openai_text_pricing
from aic.council.initial_runtime import (
    INITIAL_COUNCIL_BLOCKED_STATUS,
    INITIAL_RUNTIME_VERSION,
    actual_cost_usd,
    build_initial_council_freeze_artifact,
    build_initial_runtime_plan,
    process_initial_provider_response,
    processed_response_record,
)
from aic.council.initial_runtime_authorization import (
    INITIAL_RUNTIME_PAID_RECEIPT_VERSION,
    build_initial_runtime_paid_authorization,
)
from aic.council.initial_runtime_cost import verify_initial_runtime_cost_preflight
from aic.council.initial_runtime_preflight import verify_initial_runtime_request_preflight
from aic.council.model_input import build_initial_model_inputs
from aic.council.model_selection import load_initial_selected_model_authority
from aic.council.models import CouncilInputFreezeArtifact
from aic.domain.canonical import canonical_sha256
from aic.research.handoff import load_real_event_handoff
from aic.research.runtime import parse_responses_payload


DEFAULT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_RUNTIME_PREFLIGHT = Path(".aic-runtime/b4_initial_runtime_request_preflight_v0_1.json")
DEFAULT_COST_PREFLIGHT = Path(".aic-runtime/b4_initial_runtime_cost_preflight_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_initial_council_freeze_v0_1.json")
DEFAULT_AUTHORIZATION_OUTPUT = Path(".aic-runtime/b4_initial_runtime_paid_authorization_v0_1.json")
DEFAULT_RECEIPT_JOURNAL = Path(".aic-runtime/b4_initial_runtime_paid_receipts_v0_1.jsonl")


class InitialPaidRunnerError(ValueError):
    pass


class TrackingTransport:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.dispatch_attempts = 0
        self.provider_responses = 0
        self.last_response: Mapping[str, Any] | None = None

    def post(self, *, payload: Mapping[str, Any], api_key: str) -> Mapping[str, Any]:
        self.dispatch_attempts += 1
        response = self.delegate.post(payload=payload, api_key=api_key)
        self.provider_responses += 1
        if isinstance(response, Mapping):
            self.last_response = response
        return response


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-paid-initial", action="store_true")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    parser.add_argument("--approve-cost-artifact-hash")
    parser.add_argument("--approve-max-usd")
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--reconciliation", type=Path, default=DEFAULT_RECONCILIATION)
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--runtime-preflight", type=Path, default=DEFAULT_RUNTIME_PREFLIGHT)
    parser.add_argument("--cost-preflight", type=Path, default=DEFAULT_COST_PREFLIGHT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--authorization-output", type=Path, default=DEFAULT_AUTHORIZATION_OUTPUT)
    parser.add_argument("--receipt-journal", type=Path, default=DEFAULT_RECEIPT_JOURNAL)
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InitialPaidRunnerError(f"unable to read runtime artifact: {path}") from exc
    if not isinstance(raw, dict):
        raise InitialPaidRunnerError(f"runtime artifact root must be object: {path}")
    return raw


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_durable_fresh(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _git_context() -> tuple[str, bool]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InitialPaidRunnerError("unable to prove git execution context") from exc
    if len(head) != 40 or any(ch not in "0123456789abcdef" for ch in head):
        raise InitialPaidRunnerError("git HEAD is not canonical SHA")
    return head, not bool(status.strip())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _run_id(created_at: str, cost_hash: str, request_manifest_hash: str) -> str:
    suffix = canonical_sha256(
        {
            "created_at_utc": created_at,
            "cost_preflight_artifact_hash": cost_hash,
            "request_manifest_hash": request_manifest_hash,
        }
    )[:12]
    compact = created_at.replace("-", "").replace(":", "").replace(".", "")
    return f"AIC-B4-INITIAL-RUNTIME-{compact}-{suffix}"


def _require_fresh_paths(*paths: Path) -> None:
    for path in paths:
        if path.exists():
            raise InitialPaidRunnerError(f"paid Initial evidence path already exists: {path}")


def _structured_output_from_raw(raw: Mapping[str, Any], *, requested_model: str, latency_ms: int):
    try:
        call = parse_responses_payload(raw, requested_model=requested_model, latency_ms=latency_ms)
        decoded = json.loads(call.output_text)
        if isinstance(decoded, dict):
            return call, decoded, canonical_sha256(decoded)
        return call, None, None
    except Exception:
        return None, None, None


def _build_receipt(
    *,
    run_id: str,
    item: Any,
    tracker: TrackingTransport,
    latency_ms: int,
    started_at: str,
    finished_at: str,
    authorization_hash: str,
    cost_preflight: Mapping[str, Any],
    approved_ceiling: Decimal,
    owner_approval_id: str,
    owner_approval_at_utc: str,
    processed_record: Mapping[str, Any] | None,
    validation_error: str | None,
    pricing: Mapping[str, Any],
) -> dict[str, Any]:
    raw = tracker.last_response
    requested_model = item.request.request_payload["model"]
    call = structured = structured_hash = None
    call_obj = None
    call_obj, structured, structured_hash = (
        _structured_output_from_raw(raw, requested_model=requested_model, latency_ms=latency_ms)
        if raw is not None
        else (None, None, None)
    )
    response_received = tracker.provider_responses == 1 and raw is not None
    call_cost = actual_cost_usd(raw, model=requested_model, pricing=pricing) if response_received else Decimal("0")
    usage = raw.get("usage") if isinstance(raw, Mapping) else None
    input_tokens = usage.get("input_tokens") if isinstance(usage, Mapping) and type(usage.get("input_tokens")) is int else None
    output_tokens = usage.get("output_tokens") if isinstance(usage, Mapping) and type(usage.get("output_tokens")) is int else None
    input_details = usage.get("input_tokens_details") if isinstance(usage, Mapping) else None
    output_details = usage.get("output_tokens_details") if isinstance(usage, Mapping) else None
    cached_tokens = input_details.get("cached_tokens") if isinstance(input_details, Mapping) and type(input_details.get("cached_tokens")) is int else None
    reasoning_tokens = output_details.get("reasoning_tokens") if isinstance(output_details, Mapping) and type(output_details.get("reasoning_tokens")) is int else None

    receipt: dict[str, Any] = {
        "receipt_version": INITIAL_RUNTIME_PAID_RECEIPT_VERSION,
        "run_id": run_id,
        "dispatch_index": item.dispatch_index,
        "dispatch_started_at_utc": started_at,
        "dispatch_finished_at_utc": finished_at,
        "candidate_id": item.candidate_id,
        "lane": item.lane.value,
        "stage": item.stage.value,
        "model_run_ref": item.request.request_payload["text"]["format"]["schema"]["properties"]["model_run_ref"]["const"],
        "request_hash": item.request.request_hash,
        "request_body_utf8_bytes": item.request_body_utf8_bytes,
        "requested_model": requested_model,
        "reasoning_effort": item.request.request_payload["reasoning"]["effort"],
        "max_output_tokens": item.request.request_payload["max_output_tokens"],
        "runtime_cost_preflight_artifact_hash": cost_preflight["artifact_hash"],
        "paid_authorization_artifact_hash": authorization_hash,
        "approved_cost_ceiling_usd": str(approved_ceiling),
        "owner_approval_id": owner_approval_id,
        "owner_approval_at_utc": owner_approval_at_utc,
        "dispatch_attempted": tracker.dispatch_attempts == 1,
        "provider_response_received": response_received,
        "response_id": None if call_obj is None else call_obj.response_id,
        "effective_model": None if call_obj is None else call_obj.effective_model,
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "latency_ms": latency_ms,
        "actual_cost_usd": str(call_cost) if response_received else None,
        "cost_receipt_status": "COMPLETE" if response_received else "INCOMPLETE",
        "validation_status": "PASS" if processed_record is not None else "FAIL",
        "validation_error": validation_error,
        "structured_output": structured,
        "structured_output_hash": structured_hash,
        "semantic_replay_status": "COMPLETE" if structured is not None else "INCOMPLETE",
        "processed_record_hash": None if processed_record is None else processed_record["record_hash"],
        "raw_provider_response_persisted": False,
        "provider_output_text_persisted": False,
        "automatic_repair_attempted": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    receipt["receipt_hash"] = canonical_sha256(receipt)
    return receipt


def _blocked_artifact(
    *,
    run_id: str,
    runtime_preflight: Mapping[str, Any],
    cost_preflight: Mapping[str, Any],
    authorization_hash: str,
    processed_records: list[Mapping[str, Any]],
    receipt_hashes: list[str],
    receipt_journal: Path,
    dispatch_attempts: int,
    model_calls: int,
    actual_cost: Decimal,
    reason: str,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_version": "B4_INITIAL_COUNCIL_BLOCKED_ARTIFACT_v0_1",
        "runtime_version": INITIAL_RUNTIME_VERSION,
        "run_class": "B4_REAL_SELECTED_MODEL_INITIAL_COUNCIL",
        "status": INITIAL_COUNCIL_BLOCKED_STATUS,
        "run_id": run_id,
        "code_commit_sha": runtime_preflight["code_commit_sha"],
        "runtime_request_preflight_artifact_hash": runtime_preflight["artifact_hash"],
        "runtime_cost_preflight_artifact_hash": cost_preflight["artifact_hash"],
        "paid_authorization_artifact_hash": authorization_hash,
        "selected_candidate": dict(runtime_preflight["selected_candidate"]),
        "candidate_order": list(runtime_preflight["candidate_order"]),
        "processed_opinion_count": len(processed_records),
        "processed_records": [dict(item) for item in processed_records],
        "dispatch_attempts": dispatch_attempts,
        "model_calls": model_calls,
        "automatic_repair_calls": 0,
        "actual_cost_usd_known": str(actual_cost),
        "cost_receipt_status": "COMPLETE" if model_calls == dispatch_attempts else "INCOMPLETE",
        "paid_call_receipt_hashes": list(receipt_hashes),
        "receipt_manifest_hash": canonical_sha256({"receipt_hashes": receipt_hashes}),
        "receipt_journal_path": str(receipt_journal),
        "blocked_reason": reason,
        "initial_freeze_barrier": False,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def main() -> int:
    args = _args()
    try:
        runtime_preflight = _read_json(args.runtime_preflight)
        runtime_hash = verify_initial_runtime_request_preflight(runtime_preflight)
        cost_preflight = _read_json(args.cost_preflight)
        cost_hash = verify_initial_runtime_cost_preflight(cost_preflight)
        if cost_preflight.get("runtime_request_preflight_artifact_hash") != runtime_hash:
            raise InitialPaidRunnerError("cost preflight does not bind runtime request preflight")
        pricing = load_openai_text_pricing()
        if pricing.get("pricing_hash") != cost_preflight.get("pricing_hash"):
            raise InitialPaidRunnerError("local pricing authority differs from runtime cost preflight")

        freeze = CouncilInputFreezeArtifact.model_validate(_read_json(args.freeze))
        reconciliation = _read_json(args.reconciliation)
        handoff = load_real_event_handoff(args.handoff)
        authority = load_initial_selected_model_authority()
        model_inputs = build_initial_model_inputs(freeze, reconciliation, handoff)
        plan = build_initial_runtime_plan(
            freeze=freeze,
            model_inputs=model_inputs,
            runtime_preflight=runtime_preflight,
            authority=authority,
        )
        if len(plan) != 9:
            raise InitialPaidRunnerError("Initial paid runner deterministic plan is not exactly nine calls")

        if not args.execute_paid_initial:
            print(
                json.dumps(
                    {
                        "status": "READY_FOR_EXPLICIT_OWNER_B4_INITIAL_RUNTIME_AUTHORIZATION",
                        "code_commit_sha": runtime_preflight["code_commit_sha"],
                        "runtime_request_preflight_artifact_hash": runtime_hash,
                        "runtime_cost_preflight_artifact_hash": cost_hash,
                        "selected_candidate": runtime_preflight["selected_candidate"],
                        "planned_paid_calls_max": 9,
                        "automatic_repair_calls_authorized": False,
                        "cost_ceiling_usd": cost_preflight["total_initial_runtime_cost_upper_bound_usd"],
                        "model_calls": 0,
                        "provider_reads": 0,
                        "broker_writes": 0,
                        "alpaca_orders": 0,
                        "live_money": "PROHIBITED",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        required = {
            "owner_approval_id": args.owner_approval_id,
            "owner_approval_at_utc": args.owner_approval_at_utc,
            "approve_cost_artifact_hash": args.approve_cost_artifact_hash,
            "approve_max_usd": args.approve_max_usd,
        }
        missing = [key for key, value in required.items() if not isinstance(value, str) or not value]
        if missing:
            raise InitialPaidRunnerError("paid Initial execution missing explicit owner authorization fields: " + ", ".join(missing))

        head, clean = _git_context()
        _require_fresh_paths(args.output, args.authorization_output, args.receipt_journal)
        created_at = _now()
        run_id = _run_id(created_at, cost_hash, runtime_preflight["request_manifest_hash"])
        authorization = build_initial_runtime_paid_authorization(
            runtime_preflight=runtime_preflight,
            cost_preflight=cost_preflight,
            authority=authority,
            approve_cost_artifact_hash=args.approve_cost_artifact_hash,
            approve_max_usd=args.approve_max_usd,
            owner_approval_id=args.owner_approval_id,
            owner_approval_at_utc=args.owner_approval_at_utc,
            code_commit_sha=head,
            git_worktree_clean=clean,
            created_at_utc=created_at,
            run_id=run_id,
            receipt_journal_path=str(args.receipt_journal),
        )
        _write_durable_fresh(args.authorization_output, authorization)

        from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key

        api_key = load_openai_api_key()
        approved_ceiling = Decimal(args.approve_max_usd)
        processed_records: list[Mapping[str, Any]] = []
        receipt_hashes: list[str] = []
        cumulative_cost = Decimal("0")
        dispatch_attempts = 0
        model_calls = 0

        for item in plan:
            if dispatch_attempts >= 9:
                raise InitialPaidRunnerError("Initial paid dispatch ceiling exhausted")
            print(
                f"[B4 INITIAL] {item.dispatch_index}/9 {item.candidate_id} {item.lane.value} "
                f"{runtime_preflight['selected_candidate']['model']}/{runtime_preflight['selected_candidate']['reasoning_effort']}",
                file=sys.stderr,
                flush=True,
            )
            started_at = _now()
            tracker = TrackingTransport(StdlibResponsesTransport())
            started_ns = perf_counter_ns()
            processed_record = None
            validation_error = None
            try:
                raw = tracker.post(payload=item.request.request_payload, api_key=api_key)
                latency_ms = max(0, (perf_counter_ns() - started_ns) // 1_000_000)
                processed = process_initial_provider_response(
                    item,
                    raw_response=raw,
                    latency_ms=latency_ms,
                    frozen_at=datetime.now(UTC),
                    pricing=pricing,
                )
                processed_record = processed_response_record(processed)
            except Exception as exc:
                latency_ms = max(0, (perf_counter_ns() - started_ns) // 1_000_000)
                validation_error = f"{type(exc).__name__}: {exc}"
            finished_at = _now()
            dispatch_attempts += tracker.dispatch_attempts
            model_calls += tracker.provider_responses

            receipt = _build_receipt(
                run_id=run_id,
                item=item,
                tracker=tracker,
                latency_ms=latency_ms,
                started_at=started_at,
                finished_at=finished_at,
                authorization_hash=authorization["artifact_hash"],
                cost_preflight=cost_preflight,
                approved_ceiling=approved_ceiling,
                owner_approval_id=args.owner_approval_id,
                owner_approval_at_utc=args.owner_approval_at_utc,
                processed_record=processed_record,
                validation_error=validation_error,
                pricing=pricing,
            )
            _append_receipt(args.receipt_journal, receipt)
            receipt_hashes.append(receipt["receipt_hash"])
            if receipt["actual_cost_usd"] is not None:
                cumulative_cost += Decimal(receipt["actual_cost_usd"])
            if cumulative_cost > approved_ceiling:
                validation_error = "cumulative paid Initial cost exceeded approved ceiling"
                processed_record = None
            if processed_record is None:
                reason = validation_error or "Initial provider response could not be validated/promoted"
                blocked = _blocked_artifact(
                    run_id=run_id,
                    runtime_preflight=runtime_preflight,
                    cost_preflight=cost_preflight,
                    authorization_hash=authorization["artifact_hash"],
                    processed_records=processed_records,
                    receipt_hashes=receipt_hashes,
                    receipt_journal=args.receipt_journal,
                    dispatch_attempts=dispatch_attempts,
                    model_calls=model_calls,
                    actual_cost=cumulative_cost,
                    reason=reason,
                )
                _write(args.output, blocked)
                print(json.dumps(blocked, ensure_ascii=False, indent=2))
                return 2
            processed_records.append(processed_record)

        if dispatch_attempts != 9 or model_calls != 9 or len(processed_records) != 9 or len(receipt_hashes) != 9:
            raise InitialPaidRunnerError("Initial paid run did not complete exact 9/9 dispatch/response/promotion surface")
        receipt_manifest_hash = canonical_sha256({"receipt_hashes": receipt_hashes})
        artifact = build_initial_council_freeze_artifact(
            processed_records=tuple(processed_records),
            freeze=freeze,
            runtime_preflight=runtime_preflight,
            cost_preflight=cost_preflight,
            authority=authority,
            run_id=run_id,
            paid_authorization_artifact_hash=authorization["artifact_hash"],
            receipt_manifest_hash=receipt_manifest_hash,
            actual_cost_usd_total=cumulative_cost,
        )
        artifact.pop("artifact_hash", None)
        artifact["paid_call_receipt_hashes"] = receipt_hashes
        artifact["receipt_journal_path"] = str(args.receipt_journal)
        artifact["artifact_hash"] = canonical_sha256(artifact)
        _write(args.output, artifact)
        print(
            json.dumps(
                {
                    "artifact_version": artifact["artifact_version"],
                    "status": artifact["status"],
                    "run_id": artifact["run_id"],
                    "selected_candidate": artifact["selected_candidate"],
                    "initial_opinion_count": artifact["initial_opinion_count"],
                    "dispatch_attempts": artifact["dispatch_attempts"],
                    "model_calls": artifact["model_calls"],
                    "automatic_repair_calls": artifact["automatic_repair_calls"],
                    "actual_cost_usd": artifact["actual_cost_usd"],
                    "receipt_manifest_hash": artifact["receipt_manifest_hash"],
                    "initial_freeze_barrier": artifact["initial_freeze_barrier"],
                    "rebuttal_authorized": artifact["rebuttal_authorized"],
                    "judge_authorized": artifact["judge_authorized"],
                    "broker_writes": 0,
                    "alpaca_orders": 0,
                    "live_money": "PROHIBITED",
                    "artifact_hash": artifact["artifact_hash"],
                    "output_path": str(args.output),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(
            f"B4 Initial production runtime failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
