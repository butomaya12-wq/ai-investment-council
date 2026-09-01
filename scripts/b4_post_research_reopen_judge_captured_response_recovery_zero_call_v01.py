"""Recover one already-captured B4 v0.4 response without any provider I/O."""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from aic.council import post_research_reopen_judge_current_v03 as v03
from aic.council import post_research_reopen_judge_current_v04 as judge
from aic.council.initial_runtime_cost_v02 import actual_cost_usd
from aic.council.proposal import FrozenJudgeDecisionProposal
from aic.council.request import parse_council_responses_payload
from aic.domain.canonical import canonical_sha256


ROOT = Path(".aic-runtime")
SOURCE_EXECUTOR_HEAD = "40d7f5c72e85e1add0922673f98e5faaebebe5f2"
SOURCE_REQUEST_HASH = "2312558ae6e3979d6f8816b6b1c64309750e4e420890c4f6447f755ce4423c53"
SOURCE_APPROVAL_HASH = "72e25a2dc686292093d856b55168645e307fc81ec3e199d5668eb2c21acaabbd"
SOURCE_RAW_HASH = "fc4d73a86a178c03e1acbda64f176df4bd4fe225227832fcd5b286fa2c77e37d"
SOURCE_LEDGER_HASH = "5da3d882e21fc2d4d8150c9b8cc1d4f1c0d62b8da677223a58a320307e82bf75"
SOURCE_RESPONSE_ID = "resp_071e5625bc07e951016a96927b756087d28fc9b9eba67c2780"
EXPECTED_ACTUAL_COST_USD = Decimal("0.1433875")
MAX_APPROVED_COST_USD = Decimal("0.485459")
ORIGINAL_FAILURE = "RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:CurrentJudgeV04Error"


class CapturedResponseRecoveryError(RuntimeError):
    pass


def _need(ok: bool, message: str) -> None:
    if not ok:
        raise CapturedResponseRecoveryError(message)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _need(isinstance(value, dict), f"object required: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            value = json.loads(line)
            _need(isinstance(value, dict), f"object row required: {path}")
            rows.append(value)
    return rows


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _hash(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    _need(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        f"{field} missing",
    )
    _need(value == canonical_sha256(payload, exclude_fields=(field,)), f"{field} mismatch")
    return value


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")


def _reconstruct_source_inputs(source_executor_head: str) -> dict[str, Any]:
    """Reconstruct the frozen original v0.4 context from canonical inputs."""
    _need(source_executor_head == SOURCE_EXECUTOR_HEAD, "source executor HEAD mismatch")
    _need(
        _git("rev-parse", source_executor_head) == SOURCE_EXECUTOR_HEAD,
        "source executor commit unavailable",
    )
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
    selection_hash = v03.verify_selection(selection, eval_artifact=evaluation, receipts=receipts)
    source_entry = v03.build_entry(
        code_commit_sha=source_executor_head,
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
        source_context.model_input.get("source_lineage", {}).get("judge_selection_authority_hash")
        == selection_hash,
        "source context Judge selection lineage drift",
    )
    return {
        "source_entry": source_entry,
        "source_context": source_context,
        "pricing": pricing,
        "historical_request_hashes": [
            old_preflight["request_hash"],
            "8eedd3e58e95d97bf7e29e368fe199c3c681f17876feeabb22e6fbd8cc1c20d7",
            "72bb6db79203a54b20b47452acfbd3de4fc42385ced6330ac8f75cf18419c628",
        ],
    }


def _verify_original_lineage(
    *,
    source_executor_head: str,
    gate: Mapping[str, Any],
    entry: Mapping[str, Any],
    preflight: Mapping[str, Any],
    readiness: Mapping[str, Any],
    owner_approval: Mapping[str, Any],
    ledger: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> tuple[dict[str, Any], Any]:
    source = _reconstruct_source_inputs(source_executor_head)
    source_entry = source["source_entry"]
    source_context = source["source_context"]
    judge.verify_gate(gate, source_entry=source_entry, source_context=source_context)
    judge.verify_entry(
        entry,
        code_commit_sha=source_executor_head,
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
    inputs = {
        "entry": entry,
        "context": context,
        "source_entry": source_entry,
        "source_context": source_context,
        "gate": gate,
        "pricing": source["pricing"],
        "historical_request_hashes": source["historical_request_hashes"],
    }
    judge.verify_preflight(preflight, code_commit_sha=source_executor_head, **inputs)
    readiness_hash = judge.verify_readiness(
        readiness,
        code_commit_sha=source_executor_head,
        preflight=preflight,
        **inputs,
    )
    approval_hash = judge.verify_owner_approval(
        owner_approval,
        code_commit_sha=source_executor_head,
        readiness_hash=readiness_hash,
        preflight=preflight,
        entry=entry,
        gate=gate,
    )
    _need(preflight.get("request_hash") == SOURCE_REQUEST_HASH, "source request hash mismatch")
    _need(approval_hash == SOURCE_APPROVAL_HASH, "source approval hash mismatch")
    raw_hash = judge.verify_raw_capture(raw, request_hash=SOURCE_REQUEST_HASH)
    _need(raw_hash == SOURCE_RAW_HASH, "source raw hash mismatch")
    _need(raw.get("provider_response_id") == SOURCE_RESPONSE_ID, "source response ID mismatch")
    _need(_hash(ledger, "ledger_hash") == SOURCE_LEDGER_HASH, "source ledger hash mismatch")
    entries = ledger.get("entries")
    _need(isinstance(entries, list) and len(entries) == 1, "exactly one source dispatch required")
    row = entries[0]
    _need(isinstance(row, Mapping), "source dispatch entry malformed")
    _need(
        row.get("dispatch_index") == 1
        and row.get("request_hash") == SOURCE_REQUEST_HASH
        and row.get("raw_response_hash") == SOURCE_RAW_HASH
        and row.get("automatic_retry_permitted") is False
        and row.get("stop_reason") == ORIGINAL_FAILURE
        and row.get("state") == "DISPATCH_STARTED_UNKNOWN"
        and "processed_record_hash" not in row,
        "source dispatch ledger does not prove captured-unaccepted single-call state",
    )
    _need(
        entry.get("code_commit_sha") == source_executor_head
        and preflight.get("code_commit_sha") == source_executor_head
        and readiness.get("code_commit_sha") == source_executor_head
        and owner_approval.get("approved_executor_code_commit_sha") == source_executor_head,
        "source executor lineage mismatch",
    )
    return source, context


def recover_captured_response(
    *,
    source_executor_head: str,
    recovery_code_head: str,
    gate: Mapping[str, Any],
    entry: Mapping[str, Any],
    preflight: Mapping[str, Any],
    readiness: Mapping[str, Any],
    owner_approval: Mapping[str, Any],
    ledger: Mapping[str, Any],
    raw: Mapping[str, Any],
    recovered_result_path: Path,
    recovery_receipt_path: Path,
    expected_raw_hash: str = SOURCE_RAW_HASH,
    expected_actual_cost_usd: Decimal = EXPECTED_ACTUAL_COST_USD,
) -> dict[str, Any]:
    """Validate and freeze an existing capture; this function has no transport."""
    _need(
        isinstance(recovery_code_head, str)
        and re.fullmatch(r"[0-9a-f]{40}", recovery_code_head) is not None,
        "recovery code HEAD invalid",
    )
    _need(not recovered_result_path.exists(), "recovered result already exists")
    _need(not recovery_receipt_path.exists(), "recovery receipt already exists")
    source, context = _verify_original_lineage(
        source_executor_head=source_executor_head,
        gate=gate,
        entry=entry,
        preflight=preflight,
        readiness=readiness,
        owner_approval=owner_approval,
        ledger=ledger,
        raw=raw,
    )
    raw_hash = judge.verify_raw_capture(raw, request_hash=SOURCE_REQUEST_HASH)
    _need(raw_hash == expected_raw_hash, "raw hash does not match recovery authority")
    request = judge._request(entry, context)
    _need(request.request_hash == SOURCE_REQUEST_HASH, "source request reconstruction drift")
    call, proposal = parse_council_responses_payload(
        raw["raw_response"], request=request, latency_ms=0
    )
    judge.validate_proposal(proposal, context=context, gate=gate)
    actual = actual_cost_usd(raw["raw_response"], model="gpt-5.6-terra", pricing=source["pricing"])
    _need(actual == expected_actual_cost_usd, "captured actual cost mismatch")
    _need(actual <= MAX_APPROVED_COST_USD, "captured actual cost exceeds approved maximum")
    frozen = FrozenJudgeDecisionProposal.from_draft(proposal)
    record: dict[str, Any] = {
        "outcome": proposal.outcome.value,
        "next_directive": proposal.next_directive.value,
        "response_id": call.response_id,
        "frozen_judge_proposal": frozen.model_dump(mode="json", exclude_none=False),
    }
    record["record_hash"] = canonical_sha256(record, exclude_fields=("record_hash",))
    result: dict[str, Any] = {
        "artifact_version": "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_CAPTURED_RESPONSE_RECOVERY_v0_1",
        "status": "B4_CAPTURED_RESPONSE_RECOVERED_ZERO_CALL",
        "source_executor_head": source_executor_head,
        "recovery_code_head": recovery_code_head,
        "source_request_hash": SOURCE_REQUEST_HASH,
        "source_approval_hash": SOURCE_APPROVAL_HASH,
        "source_ledger_hash": SOURCE_LEDGER_HASH,
        "source_raw_response_hash": raw_hash,
        "source_response_id": call.response_id,
        "original_validation_failure": ORIGINAL_FAILURE,
        "repaired_validation": "PASS",
        "processed_record": record,
        "actual_cost_usd": format(actual, "f"),
        "source_paid_model_calls": 1,
        "recovery_model_calls": 0,
        "provider_reads_this_recovery": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "b5_handoff_created": False,
        "live_money": "PROHIBITED",
    }
    result["artifact_hash"] = canonical_sha256(result, exclude_fields=("artifact_hash",))
    _write_exclusive(recovered_result_path, result)
    receipt: dict[str, Any] = {
        "artifact_version": "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_CAPTURED_RESPONSE_RECOVERY_RECEIPT_v0_1",
        "source_executor_head": source_executor_head,
        "recovery_code_head": recovery_code_head,
        "source_request_hash": SOURCE_REQUEST_HASH,
        "source_approval_hash": SOURCE_APPROVAL_HASH,
        "source_ledger_hash": SOURCE_LEDGER_HASH,
        "source_raw_response_hash": raw_hash,
        "source_response_id": call.response_id,
        "original_validation_failure": ORIGINAL_FAILURE,
        "repaired_validation": "PASS",
        "source_paid_model_calls": 1,
        "recovery_model_calls": 0,
        "provider_reads_this_recovery": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "recovered_result_hash": result["artifact_hash"],
    }
    receipt["artifact_hash"] = canonical_sha256(receipt, exclude_fields=("artifact_hash",))
    _write_exclusive(recovery_receipt_path, receipt)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    for flag in (
        "--source-executor-head",
        "--gate",
        "--entry",
        "--preflight",
        "--readiness",
        "--owner-approval",
        "--ledger",
        "--raw",
        "--recovered-result",
        "--recovery-receipt",
    ):
        parser.add_argument(flag, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = recover_captured_response(
        source_executor_head=args.source_executor_head,
        recovery_code_head=_git("rev-parse", "HEAD"),
        gate=_read(Path(args.gate)),
        entry=_read(Path(args.entry)),
        preflight=_read(Path(args.preflight)),
        readiness=_read(Path(args.readiness)),
        owner_approval=_read(Path(args.owner_approval)),
        ledger=_read(Path(args.ledger)),
        raw=_read(Path(args.raw)),
        recovered_result_path=Path(args.recovered_result),
        recovery_receipt_path=Path(args.recovery_receipt),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
