from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_remaining_gaps_closure_v02 import ClosureError, build_closure


DEFAULTS = {
    "recovery": Path(".aic-runtime/b3_reopen_minimal_external_read_recovery_zero_call_v0_1.json"),
    "claim_reconciliation": Path(".aic-runtime/b3_reopen_bounded_news_claim_reconciliation_zero_call_v0_1.json"),
    "evidence_plan": Path(".aic-runtime/b3_reopen_remaining_gaps_evidence_plan_zero_call_v0_1.json"),
    "scope": Path(".aic-runtime/b3_reopen_remaining_gaps_scope_zero_call_v0_1.json"),
    "primitives": Path(".aic-runtime/b3_reopen_local_valuation_and_portfolio_primitives_zero_call_v0_1.json"),
    "selected_reconciliation": Path(".aic-runtime/b3_selected_model_reconciliation.json"),
    "judge_result": Path(".aic-runtime/b4_judge_production_result_v0_1.json"),
    "output": Path(".aic-runtime/b3_reopen_remaining_gaps_closure_zero_call_v0_2.json"),
}


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build additive B3 reopen closure overlay without any provider/model calls.")
    for name, default in DEFAULTS.items():
        p.add_argument("--" + name.replace("_", "-"), type=Path, default=default)
    return p.parse_args()


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_exclusive(path: Path, payload: dict) -> None:
    if path.exists():
        raise ClosureError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def main() -> int:
    a = _args()
    try:
        artifact = build_closure(
            code_commit_sha=_head(),
            recovery_path=a.recovery,
            claim_reconciliation_path=a.claim_reconciliation,
            evidence_plan_path=a.evidence_plan,
            scope_path=a.scope,
            primitives_path=a.primitives,
            selected_reconciliation_path=a.selected_reconciliation,
            judge_result_path=a.judge_result,
        )
        _write_exclusive(a.output, artifact)
    except (ClosureError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2))
    print(f"OUTPUT_PATH={a.output}")
    print("NEW_PROVIDER_DISPATCH_ATTEMPTS=0")
    print("NEW_PROVIDER_READS=0")
    print("MODEL_CALLS=0")
    print("BROKER_WRITES=0")
    print("ALPACA_ORDERS=0")
    print("LIVE_MONEY=PROHIBITED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
