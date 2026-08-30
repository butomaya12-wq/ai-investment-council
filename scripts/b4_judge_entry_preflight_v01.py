from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from aic.council.judge_entry_preflight import build_judge_entry_preflight


DEFAULT_REBUTTAL_FREEZE = Path(".aic-runtime/b4_rebuttal_council_freeze_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_judge_entry_preflight_v0_1.json")


def _read(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return raw


def _git_context() -> str:
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
    if status.strip():
        raise ValueError("Judge entry preflight requires clean git worktree")
    return head


def main() -> int:
    try:
        head = _git_context()
        rebuttal_freeze = _read(DEFAULT_REBUTTAL_FREEZE)
        artifact = build_judge_entry_preflight(
            rebuttal_freeze,
            code_commit_sha=head,
        )
        DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUTPUT.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "status": artifact["status"],
                    "code_commit_sha": artifact["code_commit_sha"],
                    "rebuttal_council_freeze_artifact_hash": artifact[
                        "rebuttal_council_freeze_artifact_hash"
                    ],
                    "candidate_order": artifact["candidate_order"],
                    "rebuttal_bundle_hashes": artifact["rebuttal_bundle_hashes"],
                    "research_reopen_required_candidates": artifact[
                        "research_reopen_required_candidates"
                    ],
                    "judge_entry_barrier_satisfied": True,
                    "judge_model_selection_required": True,
                    "judge_execution_authorized": False,
                    "invest_eligible_candidates": [],
                    "invest_persistence_allowed": False,
                    "allowed_judge_outcomes_for_current_frozen_run": artifact[
                        "allowed_judge_outcomes_for_current_frozen_run"
                    ],
                    "b3_reopen_is_separate_lifecycle": True,
                    "new_research_inside_b4_allowed": False,
                    "model_calls": 0,
                    "provider_reads": 0,
                    "broker_writes": 0,
                    "alpaca_orders": 0,
                    "live_money": "PROHIBITED",
                    "judge_authorized": False,
                    "rerun_authorized": False,
                    "artifact_hash": artifact["artifact_hash"],
                    "output_path": str(DEFAULT_OUTPUT),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(
            f"B4 Judge entry preflight failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
