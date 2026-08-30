from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_remaining_gaps_scope import build_remaining_gaps_scope


DEFAULT_CLAIM_RECONCILIATION = Path(
    ".aic-runtime/b3_reopen_bounded_news_claim_reconciliation_zero_call_v0_1.json"
)
DEFAULT_SELECTED_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_RETRIEVAL = Path(".aic-runtime/b3_retrieval_batch.json")
DEFAULT_B4_INPUT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_reopen_remaining_gaps_scope_zero_call_v0_1.json")


def _head_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    value = result.stdout.strip()
    if len(value) != 40:
        raise ValueError("unable to resolve exact git HEAD")
    return value


def main() -> int:
    if DEFAULT_OUTPUT.exists():
        raise ValueError(f"output already exists: {DEFAULT_OUTPUT}")
    artifact = build_remaining_gaps_scope(
        code_commit_sha=_head_sha(),
        claim_reconciliation_path=DEFAULT_CLAIM_RECONCILIATION,
        selected_reconciliation_path=DEFAULT_SELECTED_RECONCILIATION,
        retrieval_path=DEFAULT_RETRIEVAL,
        b4_input_freeze_path=DEFAULT_B4_INPUT_FREEZE,
        handoff_path=DEFAULT_HANDOFF,
    )
    DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT.write_text(
        json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False))
    print("MODEL_CALLS=0")
    print("PROVIDER_READS=0")
    print("BROKER_WRITES=0")
    print("ALPACA_ORDERS=0")
    print("LIVE_MONEY=PROHIBITED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"STOP: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
