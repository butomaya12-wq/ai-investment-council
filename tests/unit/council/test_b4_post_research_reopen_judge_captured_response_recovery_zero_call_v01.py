from __future__ import annotations

import ast
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aic.council import post_research_reopen_judge_current_v04 as judge
from aic.council.proposal import (
    JudgeDecisionProposalDraft,
    JudgeEvidenceStatus,
    JudgeNextDirective,
    JudgeOutcome,
    WhyNotCandidate,
)
from aic.domain.canonical import canonical_sha256


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "b4_post_research_reopen_judge_captured_response_recovery_zero_call_v01.py"
)
SPEC = importlib.util.spec_from_file_location("b4_captured_response_recovery", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _read(name: str) -> dict:
    return json.loads((Path(".aic-runtime") / name).read_text(encoding="utf-8"))


def _synthetic_proposal(context, gate) -> JudgeDecisionProposalDraft:
    primary = gate["invest_eligible_candidates"][0]
    basis = next(
        row["supported_basis_claim_ids"][0]
        for row in gate["candidate_results"]
        if row["candidate_id"] == primary
    )
    return JudgeDecisionProposalDraft(
        b4_decision_id="SYNTHETIC_RECOVERY_TEST",
        outcome=JudgeOutcome.INVEST,
        primary_candidate_id=primary,
        watch_candidate_ids=(),
        mandate_version=context.mandate_version,
        deep_comparison_id=context.deep_comparison_id,
        judge_input_hash=context.judge_input_hash,
        council_policy_version="COUNCIL_POLICY_vB4_0_1",
        judge_policy_version="JUDGE_POLICY_vB4_0_1",
        model_policy_version="MODEL_POLICY_vB4_0_1",
        selected_candidate_basis_claim_ids=(basis,),
        why_not_other_candidates=tuple(
            WhyNotCandidate(
                candidate_id=candidate,
                claim_ids=(
                    next(
                        row["supported_basis_claim_ids"][0]
                        for row in gate["candidate_results"]
                        if row["candidate_id"] == candidate
                    ),
                ),
                reason_codes=("SYNTHETIC_NOT_SELECTED",),
            )
            for candidate in gate["candidate_order"]
            if candidate != primary
        ),
        unresolved_dispute_refs=(),
        material_conflict_refs=(),
        material_unknown_refs=(),
        blocking_reason_codes=(),
        research_reopen_required=False,
        research_reopen_reason_codes=(),
        what_would_change_decision=(),
        invalidation_condition_refs=(),
        evidence_status=JudgeEvidenceStatus.PARTIAL,
        execution_authority=False,
        next_directive=JudgeNextDirective.PROMOTE_FINAL_DECISION,
        model_run_ref=judge.MODEL_RUN_REF,
    )


def test_recovery_script_has_explicit_inputs_and_no_provider_or_execution_imports() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
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
        assert flag in source
    assert all("runtime" not in module or "initial_runtime_cost" in module for module in imports)
    assert "StdlibResponsesTransport" not in source
    assert "load_openai_api_key" not in source
    assert "urlopen" not in source
    with pytest.raises(SystemExit):
        MODULE.parse_args([])


def test_synthetic_capture_is_validated_without_transport_and_keeps_truthful_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = _read("b4_post_research_reopen_current_invest_eligibility_zero_call_v0_4__40d7f5c.json")
    entry = _read("b4_post_research_reopen_current_judge_entry_zero_call_v0_4__40d7f5c.json")
    preflight = _read("b4_post_research_reopen_current_judge_preflight_zero_call_v0_4__40d7f5c.json")
    readiness = _read("b4_post_research_reopen_current_judge_readiness_zero_call_v0_4__40d7f5c.json")
    approval = _read("b4_post_research_reopen_current_judge_owner_approval_v0_4__40d7f5c.json")
    source = MODULE._reconstruct_source_inputs(MODULE.SOURCE_EXECUTOR_HEAD)
    context = judge.build_context(
        entry=entry,
        source_entry=source["source_entry"],
        source_context=source["source_context"],
        gate=gate,
    )
    synthetic_raw = judge.build_raw_capture(
        request_hash=MODULE.SOURCE_REQUEST_HASH,
        raw={"id": "resp_synthetic", "synthetic": True},
        started_at="2026-09-01T00:00:00Z",
        captured_at="2026-09-01T00:00:01Z",
    )
    synthetic_hash = synthetic_raw["raw_response_hash"]
    ledger = {
        "ledger_version": judge.LEDGER_VERSION,
        "approval_hash": MODULE.SOURCE_APPROVAL_HASH,
        "entries": [
            {
                "dispatch_index": 1,
                "request_hash": MODULE.SOURCE_REQUEST_HASH,
                "state": "DISPATCH_STARTED_UNKNOWN",
                "automatic_retry_permitted": False,
                "raw_response_hash": synthetic_hash,
                "stop_reason": MODULE.ORIGINAL_FAILURE,
            }
        ],
    }
    ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",))
    proposal = _synthetic_proposal(context, gate)
    monkeypatch.setattr(MODULE, "SOURCE_RAW_HASH", synthetic_hash)
    monkeypatch.setattr(MODULE, "SOURCE_LEDGER_HASH", ledger["ledger_hash"])
    monkeypatch.setattr(MODULE, "SOURCE_RESPONSE_ID", "resp_synthetic")
    monkeypatch.setattr(
        MODULE,
        "parse_council_responses_payload",
        lambda *_args, **_kwargs: (SimpleNamespace(response_id="resp_synthetic"), proposal),
    )
    monkeypatch.setattr(
        MODULE, "actual_cost_usd", lambda *_args, **_kwargs: Decimal("0.1433875")
    )
    result = MODULE.recover_captured_response(
        source_executor_head=MODULE.SOURCE_EXECUTOR_HEAD,
        recovery_code_head="a" * 40,
        gate=gate,
        entry=entry,
        preflight=preflight,
        readiness=readiness,
        owner_approval=approval,
        ledger=ledger,
        raw=synthetic_raw,
        recovered_result_path=tmp_path / "recovered-result.json",
        recovery_receipt_path=tmp_path / "recovery-receipt.json",
        expected_raw_hash=synthetic_hash,
    )
    receipt = json.loads((tmp_path / "recovery-receipt.json").read_text(encoding="utf-8"))
    assert result["repaired_validation"] == "PASS"
    assert result["source_paid_model_calls"] == 1
    assert result["recovery_model_calls"] == result["provider_reads_this_recovery"] == 0
    assert result["broker_writes"] == result["alpaca_orders"] == 0
    assert result["b5_handoff_created"] is False
    assert receipt["recovered_result_hash"] == result["artifact_hash"]
    assert receipt["artifact_hash"] == canonical_sha256(receipt, exclude_fields=("artifact_hash",))


def test_synthetic_raw_hash_mismatch_stops_before_outputs(tmp_path: Path) -> None:
    raw = {"raw_response_hash": "0" * 64}
    existing = tmp_path / "already-exists.json"
    existing.write_text("reserved", encoding="utf-8")
    with pytest.raises(MODULE.CapturedResponseRecoveryError, match="recovered result"):
        MODULE.recover_captured_response(
            source_executor_head=MODULE.SOURCE_EXECUTOR_HEAD,
            recovery_code_head="a" * 40,
            gate={}, entry={}, preflight={}, readiness={}, owner_approval={}, ledger={}, raw=raw,
            recovered_result_path=existing,
            recovery_receipt_path=tmp_path / "receipt.json",
        )
