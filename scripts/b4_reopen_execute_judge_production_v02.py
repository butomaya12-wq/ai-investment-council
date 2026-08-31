from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from aic.council import reopen_judge_paid_runtime_v02 as paid
from aic.council import reopen_judge_production_v02 as gate
from aic.council import reopen_rebuttal_credential_probe_v02 as probe_v02
from aic.council import reopen_rebuttal_production_cost_preflight_v02 as rebuttal_cost_v02
from aic.council.initial_runtime_cost_v02 import load_initial_runtime_pricing
from aic.council.reopen_initial_runtime import load_and_build_reopen_initial_runtime_plan
from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key


DEFAULT_EVAL = Path(".aic-runtime/b4_judge_model_eval_v0_1.json")
DEFAULT_EVAL_RECEIPTS = Path(".aic-runtime/b4_judge_model_eval_paid_receipts_v0_1.jsonl")
DEFAULT_SELECTION = Path(".aic-runtime/b4_judge_selected_model_authority_v0_1.json")
DEFAULT_INITIAL_COST = Path(".aic-runtime/b4_reopen_production_cost_preflight_zero_call_v0_1.json")
DEFAULT_LIFECYCLE = Path(".aic-runtime/b4_reopen_lifecycle_plan_zero_call_v0_2.json")
DEFAULT_OVERLAY = Path(".aic-runtime/b4_reopen_input_overlay_zero_call_v0_1.json")
DEFAULT_CLOSURE = Path(".aic-runtime/b3_reopen_remaining_gaps_closure_zero_call_v0_2.json")
DEFAULT_INPUT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_INITIAL_AUTHORITY = Path("config/event/b4_initial_selected_model_v1.json")
DEFAULT_PRICING = Path("config/event/openai_text_pricing_2026_08_30.json")
DEFAULT_RECOVERED_INITIAL = Path(".aic-runtime/b4_reopen_initial_council_freeze_recovered_v0_2.json")
DEFAULT_REBUTTAL_FREEZE = Path(".aic-runtime/b4_reopen_rebuttal_council_freeze_v0_3.json")
DEFAULT_ENTRY = Path(".aic-runtime/b4_reopen_judge_entry_preflight_v0_2.json")
DEFAULT_REQUEST = Path(".aic-runtime/b4_reopen_judge_production_request_preflight_v0_2.json")
DEFAULT_COST = Path(".aic-runtime/b4_reopen_judge_production_cost_preflight_v0_2.json")
DEFAULT_DRY = Path(".aic-runtime/b4_reopen_judge_production_runner_dry_v0_2.json")
DEFAULT_AUTH = Path(".aic-runtime/b4_reopen_judge_production_paid_authorization_v0_2.json")
DEFAULT_JOURNAL = Path(".aic-runtime/b4_reopen_judge_production_paid_receipts_v0_2.jsonl")
DEFAULT_RESULT = Path(".aic-runtime/b4_reopen_judge_production_result_v0_2.json")


class RunnerError(ValueError):
    pass


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute exactly one owner-approved post-reopen B4 Judge V02 call.")
    parser.add_argument("--eval", type=Path, default=DEFAULT_EVAL)
    parser.add_argument("--eval-receipts", type=Path, default=DEFAULT_EVAL_RECEIPTS)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--initial-cost", type=Path, default=DEFAULT_INITIAL_COST)
    parser.add_argument("--lifecycle", type=Path, default=DEFAULT_LIFECYCLE)
    parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--closure", type=Path, default=DEFAULT_CLOSURE)
    parser.add_argument("--input-freeze", type=Path, default=DEFAULT_INPUT_FREEZE)
    parser.add_argument("--reconciliation", type=Path, default=DEFAULT_RECONCILIATION)
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--initial-authority", type=Path, default=DEFAULT_INITIAL_AUTHORITY)
    parser.add_argument("--pricing", type=Path, default=DEFAULT_PRICING)
    parser.add_argument("--recovered-initial", type=Path, default=DEFAULT_RECOVERED_INITIAL)
    parser.add_argument("--rebuttal-freeze", type=Path, default=DEFAULT_REBUTTAL_FREEZE)
    parser.add_argument("--entry", type=Path, default=DEFAULT_ENTRY)
    parser.add_argument("--request-preflight", type=Path, default=DEFAULT_REQUEST)
    parser.add_argument("--cost-preflight", type=Path, default=DEFAULT_COST)
    parser.add_argument("--runner-dry", type=Path, default=DEFAULT_DRY)
    parser.add_argument("--authorization-output", type=Path, default=DEFAULT_AUTH)
    parser.add_argument("--receipt-journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--paid-output", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--execute-paid-judge", action="store_true")
    parser.add_argument("--approve-selection-hash")
    parser.add_argument("--approve-entry-hash")
    parser.add_argument("--approve-request-preflight-hash")
    parser.add_argument("--approve-request-manifest-hash")
    parser.add_argument("--approve-cost-artifact-hash")
    parser.add_argument("--approve-runner-dry-artifact-hash")
    parser.add_argument("--approve-max-usd")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    return parser.parse_args()


def _read(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"unable to read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise RunnerError(f"{label} root must be object")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RunnerError(f"unable to read JSONL: {path}") from exc
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RunnerError("JSONL row must be object")
        rows.append(value)
    return rows


def _head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def _branch() -> str:
    return subprocess.run(["git", "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()


def _clean() -> bool:
    return not subprocess.run(["git", "status", "--porcelain"], check=True, capture_output=True, text=True).stdout.strip()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _owner_time(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunnerError("owner approval timestamp required")
    text = value.strip()
    parse_value = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(parse_value)
    except ValueError as exc:
        raise RunnerError("owner approval timestamp must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise RunnerError("owner approval timestamp must be UTC")
    if parsed > datetime.now(UTC):
        raise RunnerError("owner approval timestamp cannot be future")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _owner(owner_id: str | None, owner_at: str | None) -> tuple[str, str]:
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise RunnerError("owner approval ID required")
    value = owner_id.strip()
    if len(value) > 160 or any(ch.isspace() for ch in value):
        raise RunnerError("owner approval ID invalid")
    return value, _owner_time(owner_at)


def _decimal(value: str | None) -> Decimal:
    if not isinstance(value, str):
        raise RunnerError("approved max USD required")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise RunnerError("approved max USD invalid") from exc
    if not result.is_finite() or result <= 0:
        raise RunnerError("approved max USD invalid")
    return result


def _write_new(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _fresh(*paths: Path) -> None:
    for path in paths:
        if path.exists():
            raise RunnerError(f"paid Judge V02 evidence already exists; NEVER overwrite/rerun: {path}")


def _run_id(started: str, request_hash: str) -> str:
    compact = started.replace("-", "").replace(":", "").replace(".", "").replace("Z", "Z")
    suffix = hashlib.sha256(f"{started}:{request_hash}".encode()).hexdigest()[:12]
    return f"AIC-B4-REOPEN-JUDGE-{compact}-{suffix}"


def _sources(args: argparse.Namespace) -> dict[str, Any]:
    eval_artifact = _read(args.eval, label="Judge eval artifact")
    receipts = _jsonl(args.eval_receipts)
    selection = _read(args.selection, label="Judge selected-model authority")
    selection_hash = gate.rebuild_and_verify_judge_selection(eval_artifact, receipts, selection)

    _, initial_plan, _, pricing = load_and_build_reopen_initial_runtime_plan(
        cost_preflight_path=args.initial_cost,
        lifecycle_path=args.lifecycle,
        overlay_path=args.overlay,
        closure_path=args.closure,
        freeze_path=args.input_freeze,
        reconciliation_path=args.reconciliation,
        handoff_path=args.handoff,
        initial_authority_path=args.initial_authority,
        pricing_path=args.pricing,
    )
    recovered = _read(args.recovered_initial, label="recovered Initial freeze")
    rebuttal_cost_v02.verify_recovered_initial_freeze(recovered, initial_plan=initial_plan)
    if recovered.get("artifact_hash") != gate.EXPECTED_RECOVERED_INITIAL_HASH:
        raise RunnerError("recovered Initial freeze hash drift")
    rebuttal = _read(args.rebuttal_freeze, label="Rebuttal V03 freeze")
    gate.verify_current_rebuttal_freeze(rebuttal)

    entry = _read(args.entry, label="Judge V02 entry")
    rebuilt_entry = gate.build_entry(rebuttal, code_commit_sha=_head())
    if entry != rebuilt_entry:
        raise RunnerError("Judge V02 entry differs from deterministic rebuild")
    entry_hash = gate.verify_entry(entry, head=_head())

    context = gate.build_context(
        initial_plan=initial_plan,
        recovered_initial_freeze=recovered,
        rebuttal_freeze=rebuttal,
        entry=entry,
        selection=selection,
    )
    request_preflight = _read(args.request_preflight, label="Judge V02 request preflight")
    rebuilt_request = gate.build_request_preflight(
        code_commit_sha=_head(), entry=entry, context=context, selection=selection
    )
    if request_preflight != rebuilt_request:
        raise RunnerError("Judge V02 request preflight differs from deterministic rebuild")
    request_preflight_hash = gate.verify_request_preflight(request_preflight, head=_head())

    cost_preflight = _read(args.cost_preflight, label="Judge V02 cost preflight")
    rebuilt_cost = gate.build_cost_preflight(request_preflight, pricing=pricing)
    if cost_preflight != rebuilt_cost:
        raise RunnerError("Judge V02 cost preflight differs from deterministic rebuild")
    cost_preflight_hash = gate.verify_cost_preflight(cost_preflight, head=_head())

    runner_dry = _read(args.runner_dry, label="Judge V02 runner dry")
    rebuilt_dry = gate.build_dry(
        code_commit_sha=_head(),
        entry=entry,
        request_preflight=request_preflight,
        cost_preflight=cost_preflight,
        selection=selection,
    )
    if runner_dry != rebuilt_dry:
        raise RunnerError("Judge V02 runner dry differs from deterministic rebuild")
    runner_dry_hash = gate.verify_dry(runner_dry, head=_head())

    request = gate.build_request(context, selection)
    if request.request_hash != request_preflight["request_hash"]:
        raise RunnerError("Judge V02 runtime request differs from frozen preflight")
    return {
        "selection": selection,
        "selection_hash": selection_hash,
        "entry": entry,
        "entry_hash": entry_hash,
        "context": context,
        "request": request,
        "request_preflight": request_preflight,
        "request_preflight_hash": request_preflight_hash,
        "cost_preflight": cost_preflight,
        "cost_preflight_hash": cost_preflight_hash,
        "runner_dry": runner_dry,
        "runner_dry_hash": runner_dry_hash,
        "pricing": pricing,
    }


def _approve(args: argparse.Namespace, src: Mapping[str, Any]) -> Decimal:
    exact = {
        "--approve-selection-hash": (args.approve_selection_hash, src["selection_hash"]),
        "--approve-entry-hash": (args.approve_entry_hash, src["entry_hash"]),
        "--approve-request-preflight-hash": (args.approve_request_preflight_hash, src["request_preflight_hash"]),
        "--approve-request-manifest-hash": (args.approve_request_manifest_hash, src["request_preflight"]["request_manifest_hash"]),
        "--approve-cost-artifact-hash": (args.approve_cost_artifact_hash, src["cost_preflight_hash"]),
        "--approve-runner-dry-artifact-hash": (args.approve_runner_dry_artifact_hash, src["runner_dry_hash"]),
    }
    for label, (observed, expected) in exact.items():
        if observed != expected:
            raise RunnerError(f"{label} does not match frozen Judge V02 evidence")
    approved = _decimal(args.approve_max_usd)
    ceiling = paid.decimal_value(src["cost_preflight"]["production_judge_cost_upper_bound_usd"], field="Judge V02 cost ceiling")
    if approved != ceiling:
        raise RunnerError("approved max USD must exactly match Judge V02 cost ceiling")
    return ceiling


def main() -> int:
    args = _args()
    if not args.execute_paid_judge:
        print("STOP: this is the paid Judge runner; use b4_reopen_run_judge_production_v02.py for zero-call dry", file=sys.stderr)
        print("MODEL_CALLS=0", file=sys.stderr)
        print("PROVIDER_READS=0", file=sys.stderr)
        return 2
    try:
        if _branch() != "hackathon/alpaca-2026":
            raise RunnerError("wrong branch")
        if not _clean():
            raise RunnerError("paid Judge V02 requires clean worktree")
        src = _sources(args)
        ceiling = _approve(args, src)
        owner_id, owner_at = _owner(args.owner_approval_id, args.owner_approval_at_utc)
        _fresh(args.authorization_output, args.receipt_journal, args.paid_output)

        api_key = load_openai_api_key()
        fingerprint = probe_v02.credential_fingerprint_sha256(api_key)
        if fingerprint != gate.EXPECTED_CREDENTIAL_SHA256:
            raise RunnerError("OPENAI_API_KEY fingerprint differs from credential validated by Probe V02")

        started = _now()
        run_id = _run_id(started, src["request"].request_hash)
        authorization = paid.build_paid_authorization(
            run_id=run_id,
            created_at_utc=started,
            code_commit_sha=_head(),
            git_worktree_clean=True,
            owner_approval_id=owner_id,
            owner_approval_at_utc=owner_at,
            selection_hash=src["selection_hash"],
            entry_hash=src["entry_hash"],
            request_preflight_hash=src["request_preflight_hash"],
            request_manifest_hash=src["request_preflight"]["request_manifest_hash"],
            request_hash=src["request"].request_hash,
            cost_preflight_hash=src["cost_preflight_hash"],
            runner_dry_hash=src["runner_dry_hash"],
            approved_cost_ceiling_usd=ceiling,
            receipt_journal_path=str(args.receipt_journal),
        )
        paid.verify_paid_authorization(authorization)
        _write_new(args.authorization_output, authorization)

        dispatch_started = _now()
        attempt = paid.build_attempt_event(
            run_id=run_id,
            started_at_utc=dispatch_started,
            authorization_hash=authorization["artifact_hash"],
            request_hash=src["request"].request_hash,
            request_manifest_hash=src["request_preflight"]["request_manifest_hash"],
        )
        _append(args.receipt_journal, attempt)
        print("[B4 REOPEN JUDGE V02] PROVIDER_DISPATCH_ATTEMPT index=1 model=gpt-5.6-terra", file=sys.stderr, flush=True)

        run = paid.execute_once(
            request=src["request"],
            context=src["context"],
            api_key=api_key,
            transport=StdlibResponsesTransport(),
            pricing=load_initial_runtime_pricing(),
        )
        dispatch_finished = _now()
        receipt = paid.build_result_receipt(
            run_id=run_id,
            started_at_utc=dispatch_started,
            finished_at_utc=dispatch_finished,
            code_commit_sha=_head(),
            authorization_hash=authorization["artifact_hash"],
            attempt_event_hash=attempt["event_hash"],
            request_hash=src["request"].request_hash,
            request_manifest_hash=src["request_preflight"]["request_manifest_hash"],
            run=run,
        )
        _append(args.receipt_journal, receipt)

        if not run.response_received:
            result = paid.build_blocked_artifact(
                status="BLOCKED_UNKNOWN_PROVIDER_DISPATCH",
                reason=run.validation_error or "provider response unavailable",
                run_id=run_id,
                code_commit_sha=_head(),
                authorization_hash=authorization["artifact_hash"],
                attempt_event_hash=attempt["event_hash"],
                receipt_hash=receipt["receipt_hash"],
                runner_dry_hash=src["runner_dry_hash"],
                approved_cost_ceiling_usd=ceiling,
                run=run,
            )
            _write_new(args.paid_output, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2
        if run.cost_receipt_status != "COMPLETE" or run.actual_cost_usd is None:
            result = paid.build_blocked_artifact(
                status="BLOCKED_INCOMPLETE_COST_RECEIPT",
                reason=run.validation_error or "Judge V02 cost receipt incomplete",
                run_id=run_id,
                code_commit_sha=_head(),
                authorization_hash=authorization["artifact_hash"],
                attempt_event_hash=attempt["event_hash"],
                receipt_hash=receipt["receipt_hash"],
                runner_dry_hash=src["runner_dry_hash"],
                approved_cost_ceiling_usd=ceiling,
                run=run,
            )
            _write_new(args.paid_output, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2
        if run.actual_cost_usd > ceiling:
            result = paid.build_blocked_artifact(
                status="BLOCKED_APPROVED_COST_CEILING_EXCEEDED",
                reason="actual Judge V02 cost exceeded approved ceiling",
                run_id=run_id,
                code_commit_sha=_head(),
                authorization_hash=authorization["artifact_hash"],
                attempt_event_hash=attempt["event_hash"],
                receipt_hash=receipt["receipt_hash"],
                runner_dry_hash=src["runner_dry_hash"],
                approved_cost_ceiling_usd=ceiling,
                run=run,
            )
            _write_new(args.paid_output, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2
        if run.validation_status != "PASS":
            result = paid.build_blocked_artifact(
                status="BLOCKED_JUDGE_VALIDATION_FAILED",
                reason=run.validation_error or "Judge V02 deterministic validation failed",
                run_id=run_id,
                code_commit_sha=_head(),
                authorization_hash=authorization["artifact_hash"],
                attempt_event_hash=attempt["event_hash"],
                receipt_hash=receipt["receipt_hash"],
                runner_dry_hash=src["runner_dry_hash"],
                approved_cost_ceiling_usd=ceiling,
                run=run,
            )
            _write_new(args.paid_output, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2

        result = paid.build_success_artifact(
            run_id=run_id,
            code_commit_sha=_head(),
            selection_hash=src["selection_hash"],
            entry_hash=src["entry_hash"],
            request_preflight_hash=src["request_preflight_hash"],
            request_manifest_hash=src["request_preflight"]["request_manifest_hash"],
            cost_preflight_hash=src["cost_preflight_hash"],
            runner_dry_hash=src["runner_dry_hash"],
            authorization_hash=authorization["artifact_hash"],
            receipt_hash=receipt["receipt_hash"],
            approved_cost_ceiling_usd=ceiling,
            run=run,
        )
        _write_new(args.paid_output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
