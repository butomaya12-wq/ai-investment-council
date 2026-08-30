from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

from aic.council.judge_model_selection_v01 import (
    build_judge_selected_model_authority,
    verify_judge_selected_model_authority,
)
from aic.council.judge_production import build_judge_production_context
from aic.council.judge_production_preflight import (
    build_judge_production_cost_preflight,
    build_judge_production_request_preflight,
    verify_judge_production_cost_preflight,
    verify_judge_production_request_preflight,
)
from aic.council.models import CouncilInputFreezeArtifact
from aic.research.handoff import load_real_event_handoff


DEFAULT_EVAL = Path(".aic-runtime/b4_judge_model_eval_v0_1.json")
DEFAULT_EVAL_RECEIPTS = Path(".aic-runtime/b4_judge_model_eval_paid_receipts_v0_1.jsonl")
DEFAULT_SELECTION = Path(".aic-runtime/b4_judge_selected_model_authority_v0_1.json")
DEFAULT_INPUT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_INITIAL_FREEZE = Path(".aic-runtime/b4_initial_council_freeze_v0_5.json")
DEFAULT_REBUTTAL_FREEZE = Path(".aic-runtime/b4_rebuttal_council_freeze_v0_1.json")
DEFAULT_JUDGE_ENTRY = Path(".aic-runtime/b4_judge_entry_preflight_v0_1.json")
DEFAULT_REQUEST = Path(".aic-runtime/b4_judge_production_request_preflight_v0_1.json")
DEFAULT_COST = Path(".aic-runtime/b4_judge_production_cost_preflight_v0_1.json")
DEFAULT_RUNNER_DRY = Path(".aic-runtime/b4_judge_production_runner_dry_v0_1.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build final B4 production Judge readiness artifacts with zero model/provider calls.")
    parser.add_argument("--eval", type=Path, default=DEFAULT_EVAL)
    parser.add_argument("--eval-receipts", type=Path, default=DEFAULT_EVAL_RECEIPTS)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--input-freeze", type=Path, default=DEFAULT_INPUT_FREEZE)
    parser.add_argument("--reconciliation", type=Path, default=DEFAULT_RECONCILIATION)
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--initial-freeze", type=Path, default=DEFAULT_INITIAL_FREEZE)
    parser.add_argument("--rebuttal-freeze", type=Path, default=DEFAULT_REBUTTAL_FREEZE)
    parser.add_argument("--judge-entry", type=Path, default=DEFAULT_JUDGE_ENTRY)
    parser.add_argument("--request-output", type=Path, default=DEFAULT_REQUEST)
    parser.add_argument("--cost-output", type=Path, default=DEFAULT_COST)
    parser.add_argument("--runner-dry-output", type=Path, default=DEFAULT_RUNNER_DRY)
    return parser.parse_args()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact root must be object: {path}")
    return value


def _read_receipts(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("receipt journal line must be object")
        result.append(value)
    return result


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def _runner_module():
    path = Path("scripts/b4_run_judge_production_v01.py")
    spec = importlib.util.spec_from_file_location("_aic_judge_production_runner_zero_call", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load production Judge runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    args = _args()
    eval_artifact = _read(args.eval)
    eval_receipts = _read_receipts(args.eval_receipts)
    selection = build_judge_selected_model_authority(eval_artifact, eval_receipts)
    verify_judge_selected_model_authority(selection)
    _write(args.selection, selection)

    input_freeze = CouncilInputFreezeArtifact.model_validate(_read(args.input_freeze))
    reconciliation = _read(args.reconciliation)
    handoff = load_real_event_handoff(args.handoff)
    initial_freeze = _read(args.initial_freeze)
    rebuttal_freeze = _read(args.rebuttal_freeze)
    judge_entry = _read(args.judge_entry)
    context = build_judge_production_context(
        input_freeze=input_freeze,
        reconciliation=reconciliation,
        handoff=handoff,
        initial_freeze=initial_freeze,
        rebuttal_freeze=rebuttal_freeze,
        judge_entry=judge_entry,
        selected_model_authority=selection,
    )
    head = _head()
    request = build_judge_production_request_preflight(
        code_commit_sha=head,
        context=context,
        selected_model_authority=selection,
    )
    request_hash = verify_judge_production_request_preflight(request)
    cost = build_judge_production_cost_preflight(request)
    cost_hash = verify_judge_production_cost_preflight(cost)
    _write(args.request_output, request)
    _write(args.cost_output, cost)

    runner = _runner_module()
    runner_args = SimpleNamespace(
        eval=args.eval,
        eval_receipts=args.eval_receipts,
        selection=args.selection,
        input_freeze=args.input_freeze,
        reconciliation=args.reconciliation,
        handoff=args.handoff,
        initial_freeze=args.initial_freeze,
        rebuttal_freeze=args.rebuttal_freeze,
        judge_entry=args.judge_entry,
        request_preflight=args.request_output,
        cost_preflight=args.cost_output,
        runner_dry=args.runner_dry_output,
    )
    dry = runner._deterministic_dry(runner_args)
    runner_dry = runner._runner_dry_artifact(dry)
    _write(args.runner_dry_output, runner_dry)

    summary = {
        "status": "READY_FOR_EXPLICIT_OWNER_B4_PRODUCTION_JUDGE_AUTHORIZATION",
        "code_commit_sha": head,
        "judge_model_eval_artifact_hash": eval_artifact["artifact_hash"],
        "judge_eval_receipt_manifest_hash": eval_artifact["receipt_manifest_hash"],
        "judge_selected_model_authority_hash": selection["artifact_hash"],
        "selected_candidate": selection["selected_candidate"],
        "production_judge_request_preflight_hash": request_hash,
        "production_judge_request_manifest_hash": request["request_manifest_hash"],
        "production_judge_request_hash": request["request_hash"],
        "production_judge_request_body_utf8_bytes": request["request_body_utf8_bytes"],
        "production_judge_context_hash": request["judge_context_hash"],
        "production_judge_input_hash": request["judge_input_hash"],
        "production_judge_cost_preflight_hash": cost_hash,
        "production_judge_cost_ceiling_usd": cost["production_judge_cost_upper_bound_usd"],
        "long_context_surcharge_assumed": cost["long_context_surcharge_assumed"],
        "pricing_version": cost["pricing_version"],
        "pricing_hash": cost["pricing_hash"],
        "production_judge_runner_dry_hash": runner_dry["artifact_hash"],
        "planned_paid_calls_max": 1,
        "max_output_tokens_per_call": 8192,
        "allowed_outcomes": ["WATCH", "ABSTAIN"],
        "required_unknown_refs": ["ALPACA_NEWS_PAGINATION_INCOMPLETE"],
        "required_next_directive": "RESEARCH_REOPEN_REQUEST",
        "final_decision_creation_allowed_for_current_frozen_run": False,
        "b5_handoff_allowed_for_current_frozen_run": False,
        "new_run_start_state_on_success": "S00",
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "production_judge_authorized": False,
        "rerun_authorized": False,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
