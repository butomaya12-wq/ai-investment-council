from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from aic.research.reopen_s00 import (
    DEFAULT_REOPEN_AUTHORITY_PATH,
    build_research_reopen_s00_artifact,
    load_reopen_authority,
)


DEFAULT_PRODUCTION_RESULT = Path(".aic-runtime/b4_judge_production_result_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_research_reopen_s00_v0_1.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bind the successful B4 production Judge reopen request to a new B3 S00 lifecycle without provider/model calls."
    )
    parser.add_argument("--production-result", type=Path, default=DEFAULT_PRODUCTION_RESULT)
    parser.add_argument("--authority", type=Path, default=DEFAULT_REOPEN_AUTHORITY_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _read_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _git_head_and_clean() -> str:
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
    if status:
        raise RuntimeError("working tree must be clean before B3 reopen S00 link")
    return head


def main() -> int:
    args = _args()
    try:
        head = _git_head_and_clean()
        authority = load_reopen_authority(args.authority)
        artifact = build_research_reopen_s00_artifact(
            _read_json(args.production_result),
            authority=authority,
            code_commit_sha=head,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        summary = {
            "status": artifact["status"],
            "artifact_hash": artifact["artifact_hash"],
            "source_production_judge_result_hash": artifact[
                "source_production_judge_result_hash"
            ],
            "source_research_reopen_request_hash": artifact[
                "source_research_reopen_request_hash"
            ],
            "new_run_start_state": artifact["new_run_start_state"],
            "next_lifecycle": artifact["next_lifecycle"],
            "required_source_ref_ids": artifact["required_source_ref_ids"],
            "next_gate": artifact["next_gate"],
            "model_calls": 0,
            "provider_reads": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "live_money": "PROHIBITED",
            "output_path": str(args.output),
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
