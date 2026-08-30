from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_local_primitives import build_local_primitives_review


DEFAULT_PLAN = Path(".aic-runtime/b3_reopen_remaining_gaps_evidence_plan_zero_call_v0_1.json")
DEFAULT_SCOPE = Path(".aic-runtime/b3_reopen_remaining_gaps_scope_zero_call_v0_1.json")
DEFAULT_RETRIEVAL = Path(".aic-runtime/b3_retrieval_batch.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_RUNTIME_ROOT = Path(".aic-runtime")
DEFAULT_CONFIG_ROOT = Path("config/event")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_reopen_local_valuation_and_portfolio_primitives_zero_call_v0_1.json")


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def main() -> int:
    try:
        head = _git("rev-parse", "HEAD")
        branch = _git("branch", "--show-current")
        if branch != "hackathon/alpaca-2026":
            raise ValueError("runner requires hackathon/alpaca-2026 branch")
        if _git("status", "--porcelain"):
            raise ValueError("runner requires clean worktree")
        if DEFAULT_OUTPUT.exists():
            raise ValueError("local primitives output already exists")

        artifact = build_local_primitives_review(
            code_commit_sha=head,
            evidence_plan_path=DEFAULT_PLAN,
            scope_path=DEFAULT_SCOPE,
            retrieval_path=DEFAULT_RETRIEVAL,
            handoff_path=DEFAULT_HANDOFF,
            runtime_root=DEFAULT_RUNTIME_ROOT,
            config_root=DEFAULT_CONFIG_ROOT,
        )
        artifact["output_path"] = str(DEFAULT_OUTPUT)
        # Rebind hash after adding the persisted output path.
        from aic.domain.canonical import canonical_sha256

        artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
        DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUTPUT.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
        print("MODEL_CALLS=0")
        print("PROVIDER_READS=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("LIVE_MONEY=PROHIBITED")
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "B3_REOPEN_LOCAL_VALUATION_AND_PORTFOLIO_PRIMITIVES_ZERO_CALL_BLOCKED",
                    "error_class": exc.__class__.__name__,
                    "error": str(exc),
                    "model_calls": 0,
                    "provider_reads": 0,
                    "broker_writes": 0,
                    "alpaca_orders": 0,
                    "live_money": "PROHIBITED",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
