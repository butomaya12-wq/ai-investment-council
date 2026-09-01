"""Explicit paid B4 v0.4 Judge runner.

This entrypoint performs no provider work until every deterministic artifact,
checkout, approval, and exclusive-output gate has passed.  It intentionally
does not have defaults for any v0.4 input or paid-output artifact.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from aic.council import post_research_reopen_judge_current_v03 as v03
from aic.council import post_research_reopen_judge_current_v04 as judge


ROOT = Path(".aic-runtime")
CANONICAL_BRANCH = "hackathon/alpaca-2026"
HISTORICAL_REQUEST_HASHES = [
    "8eedd3e58e95d97bf7e29e368fe199c3c681f17876feeabb22e6fbd8cc1c20d7",
    "72bb6db79203a54b20b47452acfbd3de4fc42385ced6330ac8f75cf18419c628",
]


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"STOP: object required: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise SystemExit(f"STOP: object row required: {path}")
        rows.append(value)
    return rows


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _tracked_worktree_status() -> str:
    """Ignore intentionally untracked .aic-runtime evidence."""
    return _git("status", "--porcelain=v1", "--untracked-files=no")


def _need(ok: bool, message: str) -> None:
    if not ok:
        raise SystemExit(f"STOP: {message}")


def _reconstruct_source_inputs(head: str) -> dict[str, Any]:
    """Rebuild exactly the frozen v0.3 source inputs consumed by v0.4."""
    rd = lambda name: _read(ROOT / name)
    closure = rd("b3_research_reopen_final_competition_closure_zero_call_v0_1.json")
    residual = rd("b3_research_reopen_residual_external_read_plan_zero_call_v0_1.json")
    gaps = rd("b3_reopen_remaining_gaps_closure_zero_call_v0_2.json")
    initial = rd("b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json")
    cost = rd("b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
    rebuttal = rd("b4_post_research_reopen_rebuttal_council_freeze_v0_1.json")
    selection = rd("b4_judge_selected_model_authority_v0_1.json")
    evaluation = rd("b4_judge_model_eval_v0_1.json")
    receipts = _read_jsonl(ROOT / "b4_judge_model_eval_paid_receipts_v0_1.jsonl")
    pricing = _read(Path("config/event/openai_text_pricing_2026_08_30.json"))
    old_preflight = rd("b4_reopen_judge_production_request_preflight_v0_2.json")

    selection_hash = v03.verify_selection(
        selection, eval_artifact=evaluation, receipts=receipts
    )
    source_entry = v03.build_entry(
        code_commit_sha=head,
        closure=closure,
        residual_plan=residual,
        remaining_gaps_closure=gaps,
        initial_freeze=initial,
        initial_cost=cost,
        rebuttal_freeze=rebuttal,
    )
    source_context = v03.build_context(
        entry=source_entry,
        closure=closure,
        residual_plan=residual,
        remaining_gaps_closure=gaps,
        initial_cost=cost,
        initial_freeze=initial,
        rebuttal_freeze=rebuttal,
        selection=selection,
    )
    _need(
        source_context.model_input.get("source_lineage", {}).get(
            "judge_selection_authority_hash"
        )
        == selection_hash,
        "source context Judge selection lineage drift",
    )
    return {
        "source_entry": source_entry,
        "source_context": source_context,
        "pricing": pricing,
        "historical_request_hashes": [
            old_preflight["request_hash"],
            *HISTORICAL_REQUEST_HASHES,
        ],
    }


def _required_paths(parser: argparse.ArgumentParser) -> None:
    for flag in (
        "--gate",
        "--entry",
        "--preflight",
        "--readiness",
        "--owner-approval",
        "--ledger",
        "--raw",
        "--result",
    ):
        parser.add_argument(flag, type=Path, required=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-paid-judge", action="store_true")
    _required_paths(parser)
    return parser.parse_args(argv)


def _prepare(args: argparse.Namespace) -> dict[str, Any]:
    """Verify every pre-transport condition without loading provider credentials."""
    _need(args.execute_paid_judge is True, "--execute-paid-judge is required")
    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    _need(branch == CANONICAL_BRANCH, "canonical branch required")
    _need(not _tracked_worktree_status(), "tracked worktree must be clean")
    for path in (args.ledger, args.raw, args.result):
        _need(not path.exists(), f"exclusive paid output exists: {path}")

    source = _reconstruct_source_inputs(head)
    source_entry = source["source_entry"]
    source_context = source["source_context"]
    gate = _read(args.gate)
    entry = _read(args.entry)
    preflight = _read(args.preflight)
    readiness = _read(args.readiness)
    approval = _read(args.owner_approval)

    judge.verify_gate(
        gate, source_entry=source_entry, source_context=source_context
    )
    _need(bool(gate.get("invest_eligible_candidates")), "no INVEST-eligible candidates")
    _need(
        {"INVEST", "WATCH", "ABSTAIN"}.issubset(
            set(gate.get("allowed_judge_outcomes", []))
        ),
        "v0.4 allowed Judge outcomes incomplete",
    )
    _need(entry.get("code_commit_sha") == head, "entry HEAD mismatch")
    _need(preflight.get("code_commit_sha") == head, "preflight HEAD mismatch")
    _need(readiness.get("code_commit_sha") == head, "readiness HEAD mismatch")
    _need(
        approval.get("approved_executor_code_commit_sha") == head,
        "owner approval executor HEAD mismatch",
    )

    judge.verify_entry(
        entry,
        code_commit_sha=head,
        source_entry=source_entry,
        source_context=source_context,
        gate=gate,
    )
    context = judge.build_context(
        entry=entry,
        source_entry=source_entry,
        source_context=source_context,
        gate=gate,
    )
    judge.verify_context(
        context,
        entry=entry,
        source_entry=source_entry,
        source_context=source_context,
        gate=gate,
    )
    verification_inputs = {
        "entry": entry,
        "context": context,
        "source_entry": source_entry,
        "source_context": source_context,
        "gate": gate,
        "pricing": source["pricing"],
        "historical_request_hashes": source["historical_request_hashes"],
    }
    judge.verify_preflight(preflight, code_commit_sha=head, **verification_inputs)
    readiness_hash = judge.verify_readiness(
        readiness,
        code_commit_sha=head,
        preflight=preflight,
        **verification_inputs,
    )
    judge.verify_owner_approval(
        approval,
        code_commit_sha=head,
        readiness_hash=readiness_hash,
        preflight=preflight,
        entry=entry,
        gate=gate,
    )
    _need(
        preflight.get("new_paid_call_count_ceiling") == 1
        and preflight.get("automatic_retries") == 0,
        "preflight paid-call ceiling or retry policy drift",
    )
    _need(
        approval.get("new_paid_call_count") == 1
        and approval.get("new_paid_call_count_ceiling") == 1
        and approval.get("automatic_retries") == 0,
        "owner approval paid-call ceiling or retry policy drift",
    )
    _need(
        approval.get("request_hash") == preflight.get("request_hash"),
        "owner approval request hash mismatch",
    )
    _need(
        approval.get("approved_judge_max_cost_usd")
        == preflight.get("judge_max_cost_usd"),
        "owner approval cost ceiling mismatch",
    )
    return {
        "branch": branch,
        "head": head,
        "gate": gate,
        "entry": entry,
        "context": context,
        "preflight": preflight,
        "readiness": readiness,
        "approval": approval,
        **source,
    }


def _real_transport_factory() -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    """Import credentials and HTTP transport only after deterministic gates pass."""
    from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key

    api_key = load_openai_api_key()
    transport = StdlibResponsesTransport()
    return lambda payload: transport.post(payload=payload, api_key=api_key)


def run(
    args: argparse.Namespace,
    *,
    transport_factory: Callable[
        [], Callable[[Mapping[str, Any]], Mapping[str, Any]]
    ]
    | None = None,
) -> dict[str, Any]:
    prepared = _prepare(args)
    factory = transport_factory or _real_transport_factory
    return judge.execute_paid(
        execute_paid_judge=True,
        branch=prepared["branch"],
        code_commit_sha=prepared["head"],
        worktree_clean=True,
        preflight=prepared["preflight"],
        readiness=prepared["readiness"],
        approval=prepared["approval"],
        entry=prepared["entry"],
        context=prepared["context"],
        source_entry=prepared["source_entry"],
        source_context=prepared["source_context"],
        gate=prepared["gate"],
        pricing=prepared["pricing"],
        historical_request_hashes=prepared["historical_request_hashes"],
        ledger_path=args.ledger,
        raw_path=args.raw,
        result_path=args.result,
        transport_factory=factory,
    )


def main(argv: list[str] | None = None) -> int:
    result = run(parse_args(argv))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
