from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from aic.council import reopen_judge_production_v02 as v02
from aic.council import reopen_rebuttal_production_cost_preflight_v02 as rebuttal_cost_v02
from aic.council.reopen_initial_runtime import load_and_build_reopen_initial_runtime_plan


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


class RunnerError(ValueError):
    pass


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the current post-reopen B4 Judge entry, request, cost and runner-dry "
            "artifacts. This program contains no provider dispatch path."
        )
    )
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
    parser.add_argument("--entry-output", type=Path, default=DEFAULT_ENTRY)
    parser.add_argument("--request-output", type=Path, default=DEFAULT_REQUEST)
    parser.add_argument("--cost-output", type=Path, default=DEFAULT_COST)
    parser.add_argument("--dry-output", type=Path, default=DEFAULT_DRY)
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
        raise RunnerError(f"unable to read Judge eval receipts: {path}") from exc
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RunnerError("Judge eval receipt line must be object")
        rows.append(value)
    return rows


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _branch() -> str:
    return subprocess.run(
        ["git", "branch", "--show-current"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _clean() -> bool:
    return not subprocess.run(
        ["git", "status", "--porcelain"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_new(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    args = _args()
    try:
        head = _head()
        if _branch() != "hackathon/alpaca-2026":
            raise RunnerError("wrong branch")
        if not _clean():
            raise RunnerError("worktree must be clean")

        for output in (
            args.entry_output,
            args.request_output,
            args.cost_output,
            args.dry_output,
        ):
            if output.exists():
                raise RunnerError(f"refusing to overwrite existing Judge V02 evidence: {output}")

        eval_artifact = _read(args.eval, label="Judge eval artifact")
        eval_receipts = _jsonl(args.eval_receipts)
        selection = _read(args.selection, label="Judge selected-model authority")
        selection_hash = v02.rebuild_and_verify_judge_selection(
            eval_artifact, eval_receipts, selection
        )

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
        rebuttal_cost_v02.verify_recovered_initial_freeze(
            recovered, initial_plan=initial_plan
        )
        if recovered.get("artifact_hash") != v02.EXPECTED_RECOVERED_INITIAL_HASH:
            raise RunnerError("recovered Initial freeze hash drift")

        rebuttal = _read(args.rebuttal_freeze, label="Rebuttal V03 freeze")
        v02.verify_current_rebuttal_freeze(rebuttal)

        entry = v02.build_entry(rebuttal, code_commit_sha=head)
        context = v02.build_context(
            initial_plan=initial_plan,
            recovered_initial_freeze=recovered,
            rebuttal_freeze=rebuttal,
            entry=entry,
            selection=selection,
        )
        request_preflight = v02.build_request_preflight(
            code_commit_sha=head,
            entry=entry,
            context=context,
            selection=selection,
        )
        cost_preflight = v02.build_cost_preflight(
            request_preflight, pricing=pricing
        )
        dry = v02.build_dry(
            code_commit_sha=head,
            entry=entry,
            request_preflight=request_preflight,
            cost_preflight=cost_preflight,
            selection=selection,
        )

        v02.verify_entry(entry, head=head)
        v02.verify_request_preflight(request_preflight, head=head)
        v02.verify_cost_preflight(cost_preflight, head=head)
        v02.verify_dry(dry, head=head)

        _write_new(args.entry_output, entry)
        _write_new(args.request_output, request_preflight)
        _write_new(args.cost_output, cost_preflight)
        _write_new(args.dry_output, dry)

        print("=== B4 REOPEN JUDGE V02 / ZERO CALL ===")
        print(f"HEAD={head}")
        print("JUDGE_SELECTION_REPLAY=PASS")
        print(f"JUDGE_SELECTION_HASH={selection_hash}")
        print(f"REBUTTAL_FREEZE_HASH={v02.EXPECTED_REBUTTAL_FREEZE_HASH}")
        print(f"ENTRY_HASH={entry['artifact_hash']}")
        print(f"REQUEST_PREFLIGHT_HASH={request_preflight['artifact_hash']}")
        print(f"REQUEST_HASH={request_preflight['request_hash']}")
        print(f"REQUEST_MANIFEST_HASH={request_preflight['request_manifest_hash']}")
        print(f"REQUEST_BODY_UTF8_BYTES={request_preflight['request_body_utf8_bytes']}")
        print(f"COST_PREFLIGHT_HASH={cost_preflight['artifact_hash']}")
        print(f"JUDGE_COST_CEILING_USD={cost_preflight['production_judge_cost_upper_bound_usd']}")
        print(f"RUNNER_DRY_HASH={dry['artifact_hash']}")
        print("MODEL=gpt-5.6-terra")
        print("REASONING_EFFORT=medium")
        print("MAX_OUTPUT_TOKENS=8192")
        print("ALLOWED_OUTCOMES=INVEST,WATCH,ABSTAIN")
        print("INVEST_ELIGIBLE=META")
        print("INVEST_BLOCKED=NVDA,MSFT")
        print("MODEL_CALLS=0")
        print("PROVIDER_READS=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("COST_USD_THIS_STEP=0")
        print("LIVE_MONEY=PROHIBITED")
        print(f"NEXT_GATE={v02.NEXT_GATE}")
        print()
        print("=== ENTRY ARTIFACT ===")
        print(json.dumps(entry, ensure_ascii=False, indent=2))
        print("=== REQUEST PREFLIGHT ARTIFACT ===")
        print(json.dumps(request_preflight, ensure_ascii=False, indent=2))
        print("=== COST PREFLIGHT ARTIFACT ===")
        print(json.dumps(cost_preflight, ensure_ascii=False, indent=2))
        print("=== RUNNER DRY ARTIFACT ===")
        print(json.dumps(dry, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        print("MODEL_CALLS=0", file=sys.stderr)
        print("PROVIDER_READS=0", file=sys.stderr)
        print("COST_USD_THIS_STEP=0", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
