from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from aic.research.reopen_judge_existing_evidence_inventory_v01 import (
    build_inventory,
    verify_inventory,
)


DEFAULT_SCOPE = Path(".aic-runtime/b3_research_reopen_s00_scope_zero_call_v0_3.json")
DEFAULT_CLOSURE = Path(".aic-runtime/b3_reopen_remaining_gaps_closure_zero_call_v0_2.json")
DEFAULT_RETRIEVAL = Path(".aic-runtime/b3_retrieval_batch.json")
DEFAULT_SELECTED = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_INITIAL = Path(".aic-runtime/b4_reopen_initial_council_freeze_recovered_v0_2.json")
DEFAULT_JUDGE = Path(".aic-runtime/b4_reopen_judge_production_result_v0_2.json")
DEFAULT_RUNTIME_ROOT = Path(".aic-runtime")
DEFAULT_CONFIG_ROOT = Path("config/event")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_research_reopen_existing_evidence_inventory_zero_call_v0_1.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory durable evidence for the Judge-triggered B3 reopen without external or model calls."
    )
    parser.add_argument("--scope", type=Path, default=DEFAULT_SCOPE)
    parser.add_argument("--historical-closure", type=Path, default=DEFAULT_CLOSURE)
    parser.add_argument("--retrieval", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--selected-reconciliation", type=Path, default=DEFAULT_SELECTED)
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--recovered-initial", type=Path, default=DEFAULT_INITIAL)
    parser.add_argument("--judge", type=Path, default=DEFAULT_JUDGE)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--config-root", type=Path, default=DEFAULT_CONFIG_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _read_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _head_and_clean() -> str:
    head = _git("rev-parse", "HEAD")
    if _git("branch", "--show-current") != "hackathon/alpaca-2026":
        raise RuntimeError("existing-evidence inventory requires hackathon/alpaca-2026")
    if _git("status", "--porcelain"):
        raise RuntimeError("working tree must be clean before existing-evidence inventory")
    return head


def _summary(artifact: Mapping[str, Any], *, output: Path, write_state: str) -> dict[str, Any]:
    return {
        "status": artifact["status"],
        "artifact_hash": artifact["artifact_hash"],
        "code_commit_sha": artifact["code_commit_sha"],
        "source_s00_scope_v03_hash": artifact["source_s00_scope_v03_hash"],
        "inventory_target_count": artifact["inventory_target_count"],
        "resolved_target_count": artifact["resolved_target_count"],
        "local_replay_target_count": artifact["local_replay_target_count"],
        "local_replay_target_ids": artifact["local_replay_target_ids"],
        "residual_external_read_target_count": artifact["residual_external_read_target_count"],
        "residual_external_read_target_ids": artifact["residual_external_read_target_ids"],
        "historical_supplemental_evidence_refs": artifact["historical_supplemental_evidence_refs"],
        "broad_b3_rerun_authorized": artifact["broad_b3_rerun_authorized"],
        "research_run_started": artifact["research_run_started"],
        "provider_reads_authorized": artifact["provider_reads_authorized"],
        "model_calls_authorized": artifact["model_calls_authorized"],
        "judge_rerun_authorized": artifact["judge_rerun_authorized"],
        "rebuttal_rerun_authorized": artifact["rebuttal_rerun_authorized"],
        "final_decision_created": artifact["final_decision_created"],
        "b5_handoff_created": artifact["b5_handoff_created"],
        "execution_authority": artifact["execution_authority"],
        "model_calls": artifact["model_calls"],
        "provider_reads": artifact["provider_reads"],
        "broker_writes": artifact["broker_writes"],
        "alpaca_orders": artifact["alpaca_orders"],
        "live_money": artifact["live_money"],
        "next_gate": artifact["next_gate"],
        "output_path": str(output),
        "write_state": write_state,
    }


def main() -> int:
    args = _args()
    try:
        head = _head_and_clean()
        artifact = build_inventory(
            scope=_read_json(args.scope),
            historical_closure=_read_json(args.historical_closure),
            retrieval=_read_json(args.retrieval),
            selected_reconciliation=_read_json(args.selected_reconciliation),
            handoff=_read_json(args.handoff),
            recovered_initial=_read_json(args.recovered_initial),
            judge_result=_read_json(args.judge),
            runtime_root=str(args.runtime_root),
            config_root=str(args.config_root),
            code_commit_sha=head,
        )
        verify_inventory(artifact, expected_code_commit_sha=head)

        write_state = "CREATED"
        if args.output.exists():
            existing = _read_json(args.output)
            verify_inventory(existing, expected_code_commit_sha=head)
            if dict(existing) != artifact:
                raise RuntimeError("existing evidence inventory differs from deterministic rebuild")
            write_state = "EXISTING_VERIFIED"
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        print(json.dumps(_summary(artifact, output=args.output, write_state=write_state), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
