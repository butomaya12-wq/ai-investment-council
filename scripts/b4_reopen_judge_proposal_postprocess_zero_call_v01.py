from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from aic.council.reopen_judge_postprocess_v01 import (
    build_postprocess_artifact,
    build_research_reopen_request,
    verify_judge_result,
    verify_paid_authorization,
    verify_postprocess_artifact,
    verify_receipt_journal,
)
from aic.domain.contracts import RESEARCH_REOPEN_REQUEST_V1


DEFAULT_RESULT = Path(".aic-runtime/b4_reopen_judge_production_result_v0_2.json")
DEFAULT_AUTHORIZATION = Path(".aic-runtime/b4_reopen_judge_production_paid_authorization_v0_2.json")
DEFAULT_JOURNAL = Path(".aic-runtime/b4_reopen_judge_production_paid_receipts_v0_2.jsonl")
DEFAULT_REOPEN_OUTPUT = Path(".aic-runtime/b4_reopen_judge_research_reopen_request_v0_1.json")
DEFAULT_POSTPROCESS_OUTPUT = Path(".aic-runtime/b4_reopen_judge_proposal_postprocess_zero_call_v0_1.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Persist exactly one canonical RESEARCH_REOPEN_REQUEST_V1 from the "
            "frozen post-reopen Judge WATCH result without provider/model calls."
        )
    )
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    parser.add_argument("--receipt-journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--reopen-output", type=Path, default=DEFAULT_REOPEN_OUTPUT)
    parser.add_argument("--postprocess-output", type=Path, default=DEFAULT_POSTPROCESS_OUTPUT)
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} root must be an object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path} JSONL row must be an object")
        rows.append(value)
    return rows


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _branch() -> str:
    return subprocess.run(
        ["git", "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _clean() -> bool:
    return not subprocess.run(
        ["git", "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _persist_or_verify(path: Path, expected: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        observed = _read_json(path)
        if observed != dict(expected):
            raise ValueError(f"existing immutable artifact differs from deterministic rebuild: {path}")
        return "EXISTING_VERIFIED"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
        handle.flush()
    return "CREATED"


def main() -> int:
    args = _args()
    try:
        if _branch() != "hackathon/alpaca-2026":
            raise RuntimeError("wrong branch")
        if not _clean():
            raise RuntimeError("Judge postprocess requires clean worktree")
        head = _git_head()

        result = _read_json(args.result)
        authorization = _read_json(args.authorization)
        journal = _read_jsonl(args.receipt_journal)

        result_hash = verify_judge_result(result)
        authorization_hash = verify_paid_authorization(authorization)
        attempt, receipt = verify_receipt_journal(journal)

        reopen = build_research_reopen_request(result, receipt)
        reopen_payload = reopen.model_dump(mode="json", exclude_none=False, warnings=False)
        RESEARCH_REOPEN_REQUEST_V1.model_validate(reopen_payload)

        postprocess = build_postprocess_artifact(
            result=result,
            authorization=authorization,
            attempt=attempt,
            receipt=receipt,
            reopen_request=reopen,
            code_commit_sha=head,
        )
        postprocess_hash = verify_postprocess_artifact(postprocess)

        reopen_write_state = _persist_or_verify(args.reopen_output, reopen_payload)
        postprocess_write_state = _persist_or_verify(args.postprocess_output, postprocess)

        summary = {
            "status": postprocess["status"],
            "source_judge_result_hash": result_hash,
            "source_paid_authorization_hash": authorization_hash,
            "source_dispatch_attempt_hash": attempt["event_hash"],
            "source_paid_call_receipt_hash": receipt["receipt_hash"],
            "source_outcome": postprocess["source_outcome"],
            "source_primary_candidate_id": postprocess["source_primary_candidate_id"],
            "source_judge_model_calls": 1,
            "source_judge_actual_cost_usd": "0.1525175",
            "research_reopen_request_write_state": reopen_write_state,
            "research_reopen_request_hash": reopen_payload["request_hash"],
            "research_reopen_request_count": 1,
            "postprocess_write_state": postprocess_write_state,
            "postprocess_artifact_hash": postprocess_hash,
            "new_run_start_state": "S00",
            "research_run_started": False,
            "final_decision_created": False,
            "b5_handoff_created": False,
            "execution_authority": False,
            "next_gate": postprocess["next_gate"],
            "model_calls_this_step": 0,
            "provider_reads_this_step": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "cost_usd_this_step": "0",
            "live_money": "PROHIBITED",
            "reopen_output": str(args.reopen_output),
            "postprocess_output": str(args.postprocess_output),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        print("MODEL_CALLS_THIS_STEP=0", file=sys.stderr)
        print("PROVIDER_READS_THIS_STEP=0", file=sys.stderr)
        print("BROKER_WRITES=0", file=sys.stderr)
        print("ALPACA_ORDERS=0", file=sys.stderr)
        print("LIVE_MONEY=PROHIBITED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
