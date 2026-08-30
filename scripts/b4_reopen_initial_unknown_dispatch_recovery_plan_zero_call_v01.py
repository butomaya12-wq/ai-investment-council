from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

from aic.council.reopen_initial_runtime import load_and_build_reopen_initial_runtime_plan
from aic.council.reopen_initial_unknown_dispatch_recovery import (
    B4ReopenInitialUnknownDispatchRecoveryError,
    load_and_build_recovery_plan_artifact,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cost-preflight",
        type=Path,
        default=Path(".aic-runtime/b4_reopen_production_cost_preflight_zero_call_v0_1.json"),
    )
    parser.add_argument(
        "--source-authorization",
        type=Path,
        default=Path(".aic-runtime/b4_reopen_initial_runtime_paid_authorization_v0_1.json"),
    )
    parser.add_argument(
        "--source-blocked-artifact",
        type=Path,
        default=Path(".aic-runtime/b4_reopen_initial_council_freeze_v0_1.json"),
    )
    parser.add_argument(
        "--source-receipt-journal",
        type=Path,
        default=Path(".aic-runtime/b4_reopen_initial_runtime_paid_receipts_v0_1.jsonl"),
    )
    parser.add_argument(
        "--lifecycle",
        type=Path,
        default=Path(".aic-runtime/b4_reopen_lifecycle_plan_zero_call_v0_2.json"),
    )
    parser.add_argument(
        "--overlay",
        type=Path,
        default=Path(".aic-runtime/b4_reopen_input_overlay_zero_call_v0_1.json"),
    )
    parser.add_argument(
        "--closure",
        type=Path,
        default=Path(".aic-runtime/b3_reopen_remaining_gaps_closure_zero_call_v0_2.json"),
    )
    parser.add_argument(
        "--freeze",
        type=Path,
        default=Path(".aic-runtime/b4_council_input_freeze.json"),
    )
    parser.add_argument(
        "--reconciliation",
        type=Path,
        default=Path(".aic-runtime/b3_selected_model_reconciliation.json"),
    )
    parser.add_argument(
        "--handoff",
        type=Path,
        default=Path("config/event/b2_real_event_handoff_v0_1.json"),
    )
    parser.add_argument(
        "--initial-authority",
        type=Path,
        default=Path("config/event/b4_initial_selected_model_v1.json"),
    )
    parser.add_argument(
        "--pricing",
        type=Path,
        default=Path("config/event/openai_text_pricing_2026_08_30.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".aic-runtime/b4_reopen_initial_unknown_dispatch_recovery_plan_zero_call_v0_1.json"),
    )
    return parser.parse_args()


def _git_context() -> tuple[str, bool]:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], text=True)
    return head, not bool(dirty.strip())


def _write_durable_fresh(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def main() -> int:
    args = _args()
    try:
        head, clean = _git_context()
        if not clean:
            raise B4ReopenInitialUnknownDispatchRecoveryError("worktree must be clean")
        if args.output.exists():
            raise B4ReopenInitialUnknownDispatchRecoveryError(
                f"recovery-plan output already exists: {args.output}"
            )

        cost, plan, _authority, _pricing = load_and_build_reopen_initial_runtime_plan(
            cost_preflight_path=args.cost_preflight,
            lifecycle_path=args.lifecycle,
            overlay_path=args.overlay,
            closure_path=args.closure,
            freeze_path=args.freeze,
            reconciliation_path=args.reconciliation,
            handoff_path=args.handoff,
            initial_authority_path=args.initial_authority,
            pricing_path=args.pricing,
        )
        artifact = load_and_build_recovery_plan_artifact(
            code_commit_sha=head,
            cost_preflight_path=args.cost_preflight,
            source_authorization_path=args.source_authorization,
            blocked_artifact_path=args.source_blocked_artifact,
            receipt_journal_path=args.source_receipt_journal,
            runtime_plan=plan,
        )
        if artifact["source_cost_preflight_artifact_hash"] != cost["artifact_hash"]:
            raise B4ReopenInitialUnknownDispatchRecoveryError(
                "recovery plan/cost preflight lineage mismatch"
            )
        _write_durable_fresh(args.output, artifact)
        print(json.dumps(artifact, ensure_ascii=False, indent=2))
        print(f"OUTPUT_PATH={args.output}")
        print("MODEL_CALLS=0")
        print("PROVIDER_READS=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("LIVE_MONEY=PROHIBITED")
        return 0
    except (B4ReopenInitialUnknownDispatchRecoveryError, ValueError) as exc:
        print(
            f"B4 reopen Initial unknown-dispatch recovery plan failed closed: "
            f"{type(exc).__name__}: {exc}"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
