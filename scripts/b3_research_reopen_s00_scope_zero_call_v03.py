from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from aic.research.reopen_judge_s00_scope_v03 import build_scope_artifact, verify_scope_artifact


DEFAULT_REOPEN = Path(".aic-runtime/b4_reopen_judge_research_reopen_request_v0_1.json")
DEFAULT_POSTPROCESS = Path(".aic-runtime/b4_reopen_judge_proposal_postprocess_zero_call_v0_1.json")
DEFAULT_JUDGE = Path(".aic-runtime/b4_reopen_judge_production_result_v0_2.json")
DEFAULT_REBUTTAL = Path(".aic-runtime/b4_reopen_rebuttal_council_freeze_v0_3.json")
DEFAULT_INITIAL = Path(".aic-runtime/b4_reopen_initial_council_freeze_recovered_v0_2.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_research_reopen_s00_scope_zero_call_v0_3.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Judge-triggered B3 research-reopen S00 scope V03 from frozen Initial+Rebuttal lineage with zero provider/model calls.")
    parser.add_argument("--reopen", type=Path, default=DEFAULT_REOPEN)
    parser.add_argument("--postprocess", type=Path, default=DEFAULT_POSTPROCESS)
    parser.add_argument("--judge", type=Path, default=DEFAULT_JUDGE)
    parser.add_argument("--rebuttal", type=Path, default=DEFAULT_REBUTTAL)
    parser.add_argument("--recovered-initial", type=Path, default=DEFAULT_INITIAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _read_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _head_and_clean() -> str:
    head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    branch = subprocess.run(["git", "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip()
    if branch != "hackathon/alpaca-2026":
        raise RuntimeError("S00 scope V03 must run on hackathon/alpaca-2026")
    status = subprocess.run(["git", "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
    if status:
        raise RuntimeError("working tree must be clean before S00 scope V03")
    return head


def _summary(artifact: Mapping[str, Any], *, output: Path, write_state: str) -> dict[str, Any]:
    requirements = artifact["canonical_reopen_requirements"]
    conditions = artifact["judge_change_conditions_for_executable_invest"]
    return {
        "status": artifact["status"],
        "artifact_hash": artifact["artifact_hash"],
        "code_commit_sha": artifact["code_commit_sha"],
        "rebuttal_reason_derivation": artifact["rebuttal_reason_derivation"],
        "initial_claim_derivation": artifact["initial_claim_derivation"],
        "source_recovered_initial_freeze_artifact_hash": artifact["source_recovered_initial_freeze_artifact_hash"],
        "source_research_reopen_request_hash": artifact["source_research_reopen_request_hash"],
        "source_rebuttal_freeze_artifact_hash": artifact["source_rebuttal_freeze_artifact_hash"],
        "canonical_reopen_requirement_count": artifact["canonical_reopen_requirement_count"],
        "canonical_reopen_requirement_ids": [row["requirement_id"] for row in requirements],
        "judge_change_condition_count": artifact["judge_change_condition_count"],
        "judge_change_condition_ids": [row["condition_id"] for row in conditions],
        "planned_current_developments_candidate_symbols": artifact["planned_current_developments_candidate_symbols"],
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
        artifact = build_scope_artifact(
            reopen_request=_read_json(args.reopen),
            postprocess=_read_json(args.postprocess),
            judge_result=_read_json(args.judge),
            rebuttal_freeze=_read_json(args.rebuttal),
            recovered_initial_freeze=_read_json(args.recovered_initial),
            code_commit_sha=head,
        )
        verify_scope_artifact(artifact, expected_code_commit_sha=head)
        write_state = "CREATED"
        if args.output.exists():
            existing = _read_json(args.output)
            verify_scope_artifact(existing, expected_code_commit_sha=head)
            if dict(existing) != artifact:
                raise RuntimeError("existing S00 scope V03 artifact differs from deterministic rebuild")
            write_state = "EXISTING_VERIFIED"
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        print(json.dumps(_summary(artifact, output=args.output, write_state=write_state), indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
