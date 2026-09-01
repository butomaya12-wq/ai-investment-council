from __future__ import annotations

import ast
from argparse import Namespace
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


EVENT_RUNTIME_EVIDENCE_NAMES = (
    "b3_research_reopen_final_competition_closure_zero_call_v0_1.json",
    "b3_research_reopen_residual_external_read_plan_zero_call_v0_1.json",
    "b3_reopen_remaining_gaps_closure_zero_call_v0_2.json",
    "b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json",
    "b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json",
    "b4_post_research_reopen_rebuttal_council_freeze_v0_1.json",
    "b4_judge_selected_model_authority_v0_1.json",
    "b4_judge_model_eval_v0_1.json",
    "b4_judge_model_eval_paid_receipts_v0_1.jsonl",
    "b4_reopen_judge_production_request_preflight_v0_2.json",
    "b4_post_research_reopen_current_invest_eligibility_zero_call_v0_4__40d7f5c.json",
    "b4_post_research_reopen_current_judge_entry_zero_call_v0_4__40d7f5c.json",
    "b4_post_research_reopen_current_judge_preflight_zero_call_v0_4__40d7f5c.json",
    "b4_post_research_reopen_current_judge_readiness_zero_call_v0_4__40d7f5c.json",
    "b4_post_research_reopen_current_judge_owner_approval_v0_4__40d7f5c.json",
    "b4_post_research_reopen_current_judge_paid_dispatch_ledger_v0_4__40d7f5c.json",
    "b4_post_research_reopen_current_judge_raw_response_v0_4__40d7f5c.json",
)


def _event_evidence() -> dict[str, Path]:
    paths = {
        name: Path(".aic-runtime") / name for name in EVENT_RUNTIME_EVIDENCE_NAMES
    }
    present = [path.exists() for path in paths.values()]
    if not any(present):
        pytest.skip("requires local immutable B4 production runtime evidence")
    missing = [name for name, path in paths.items() if not path.exists()]
    assert not missing, f"incomplete local immutable B4 production runtime evidence: {missing}"
    return paths


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


def _synthetic_inputs(monkeypatch: pytest.MonkeyPatch) -> dict:
    context = SimpleNamespace(
        mandate_version="SYNTHETIC_MANDATE",
        deep_comparison_id="SYNTHETIC_COMPARISON",
        judge_input_hash="1" * 64,
    )
    gate = {
        "candidate_order": ["NVDA", "MSFT", "META"],
        "invest_eligible_candidates": ["NVDA"],
        "candidate_results": [
            {
                "candidate_id": candidate,
                "supported_basis_claim_ids": [f"{candidate}_BASIS"],
            }
            for candidate in ("NVDA", "MSFT", "META")
        ],
    }
    synthetic_raw = judge.build_raw_capture(
        request_hash=MODULE.SOURCE_REQUEST_HASH,
        raw={"id": "resp_synthetic", "synthetic": True},
        started_at="2026-09-01T00:00:00Z",
        captured_at="2026-09-01T00:00:01Z",
    )
    synthetic_hash = synthetic_raw["raw_response_hash"]
    proposal = _synthetic_proposal(context, gate)
    monkeypatch.setattr(
        MODULE,
        "_verify_original_lineage",
        lambda **_kwargs: ({"pricing": {}}, context),
    )
    monkeypatch.setattr(MODULE.judge, "verify_raw_capture", lambda *_args, **_kwargs: synthetic_hash)
    monkeypatch.setattr(
        MODULE.judge,
        "_request",
        lambda *_args, **_kwargs: SimpleNamespace(request_hash=MODULE.SOURCE_REQUEST_HASH),
    )
    monkeypatch.setattr(
        MODULE,
        "parse_council_responses_payload",
        lambda *_args, **_kwargs: (SimpleNamespace(response_id="resp_synthetic"), proposal),
    )
    monkeypatch.setattr(
        MODULE, "actual_cost_usd", lambda *_args, **_kwargs: Decimal("0.1433875")
    )
    monkeypatch.setattr(MODULE.judge, "validate_proposal", lambda *_args, **_kwargs: None)
    return {
        "source_executor_head": MODULE.SOURCE_EXECUTOR_HEAD,
        "recovery_code_head": "a" * 40,
        "gate": gate,
        "entry": {},
        "preflight": {},
        "readiness": {},
        "owner_approval": {},
        "ledger": {},
        "raw": synthetic_raw,
        "expected_raw_hash": synthetic_hash,
    }


def _recover_synthetic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    **overrides: object,
) -> tuple[dict, Path, Path]:
    inputs = _synthetic_inputs(monkeypatch)
    result_path = tmp_path / "recovered-result.json"
    receipt_path = tmp_path / "recovery-receipt.json"
    result = MODULE.recover_captured_response(
        **inputs,
        recovered_result_path=result_path,
        recovery_receipt_path=receipt_path,
        original_result_path=tmp_path / "original-result.json",
        **overrides,
    )
    return result, result_path, receipt_path


def test_synthetic_capture_is_validated_without_transport_and_keeps_truthful_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _, receipt_path = _recover_synthetic(tmp_path, monkeypatch)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result["repaired_validation"] == "PASS"
    assert result["source_paid_model_calls"] == 1
    assert result["recovery_model_calls"] == result["provider_reads_this_recovery"] == 0
    assert result["broker_writes"] == result["alpaca_orders"] == 0
    assert result["b5_handoff_created"] is False
    assert receipt["recovered_result_hash"] == result["artifact_hash"]
    assert receipt["artifact_hash"] == canonical_sha256(receipt, exclude_fields=("artifact_hash",))


def test_synthetic_source_hash_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _synthetic_inputs(monkeypatch)
    monkeypatch.setattr(MODULE.judge, "verify_raw_capture", lambda *_args, **_kwargs: "f" * 64)
    with pytest.raises(MODULE.CapturedResponseRecoveryError, match="raw hash does not match"):
        MODULE.recover_captured_response(
            **inputs,
            recovered_result_path=tmp_path / "result.json",
            recovery_receipt_path=tmp_path / "receipt.json",
            original_result_path=tmp_path / "original.json",
        )


def _synthetic_lineage_inputs(monkeypatch: pytest.MonkeyPatch) -> dict:
    source_context = SimpleNamespace()
    monkeypatch.setattr(
        MODULE,
        "_reconstruct_source_inputs",
        lambda _head: {
            "source_entry": {},
            "source_context": source_context,
            "pricing": {},
            "historical_request_hashes": [],
        },
    )
    monkeypatch.setattr(MODULE.judge, "verify_gate", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE.judge, "verify_entry", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE.judge, "build_context", lambda *_args, **_kwargs: source_context)
    monkeypatch.setattr(MODULE.judge, "verify_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE.judge, "verify_readiness", lambda *_args, **_kwargs: "2" * 64)
    monkeypatch.setattr(
        MODULE.judge, "verify_owner_approval", lambda *_args, **_kwargs: MODULE.SOURCE_APPROVAL_HASH
    )
    monkeypatch.setattr(
        MODULE.judge, "verify_raw_capture", lambda *_args, **_kwargs: MODULE.SOURCE_RAW_HASH
    )
    ledger = {
        "entries": [
            {
                "dispatch_index": 1,
                "request_hash": MODULE.SOURCE_REQUEST_HASH,
                "state": "DISPATCH_STARTED_UNKNOWN",
                "automatic_retry_permitted": False,
                "raw_response_hash": MODULE.SOURCE_RAW_HASH,
                "stop_reason": MODULE.ORIGINAL_FAILURE,
            }
        ]
    }
    ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",))
    monkeypatch.setattr(MODULE, "SOURCE_LEDGER_HASH", ledger["ledger_hash"])
    return {
        "source_executor_head": MODULE.SOURCE_EXECUTOR_HEAD,
        "gate": {},
        "entry": {"code_commit_sha": MODULE.SOURCE_EXECUTOR_HEAD},
        "preflight": {
            "request_hash": MODULE.SOURCE_REQUEST_HASH,
            "code_commit_sha": MODULE.SOURCE_EXECUTOR_HEAD,
        },
        "readiness": {"code_commit_sha": MODULE.SOURCE_EXECUTOR_HEAD},
        "owner_approval": {"approved_executor_code_commit_sha": MODULE.SOURCE_EXECUTOR_HEAD},
        "ledger": ledger,
        "raw": {"provider_response_id": MODULE.SOURCE_RESPONSE_ID},
    }


def test_synthetic_lineage_requires_one_non_retrying_captured_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _synthetic_lineage_inputs(monkeypatch)
    MODULE._verify_original_lineage(**inputs)
    inputs["ledger"]["entries"].append(dict(inputs["ledger"]["entries"][0]))
    inputs["ledger"]["ledger_hash"] = canonical_sha256(
        inputs["ledger"], exclude_fields=("ledger_hash",)
    )
    monkeypatch.setattr(MODULE, "SOURCE_LEDGER_HASH", inputs["ledger"]["ledger_hash"])
    with pytest.raises(MODULE.CapturedResponseRecoveryError, match="exactly one source dispatch"):
        MODULE._verify_original_lineage(**inputs)

    retry_inputs = _synthetic_lineage_inputs(monkeypatch)
    retry_inputs["ledger"]["entries"][0]["automatic_retry_permitted"] = True
    retry_inputs["ledger"]["ledger_hash"] = canonical_sha256(
        retry_inputs["ledger"], exclude_fields=("ledger_hash",)
    )
    monkeypatch.setattr(
        MODULE, "SOURCE_LEDGER_HASH", retry_inputs["ledger"]["ledger_hash"]
    )
    with pytest.raises(MODULE.CapturedResponseRecoveryError, match="single-call state"):
        MODULE._verify_original_lineage(**retry_inputs)


def test_original_production_result_existing_blocks_before_recovered_outputs(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original-result.json"
    original.write_text("must remain immutable", encoding="utf-8")
    with pytest.raises(MODULE.CapturedResponseRecoveryError, match="original production result exists"):
        MODULE.recover_captured_response(
            source_executor_head=MODULE.SOURCE_EXECUTOR_HEAD,
            recovery_code_head="a" * 40,
            gate={}, entry={}, preflight={}, readiness={}, owner_approval={}, ledger={}, raw={},
            recovered_result_path=tmp_path / "result.json",
            recovery_receipt_path=tmp_path / "receipt.json",
            original_result_path=original,
        )
    assert not (tmp_path / "result.json").exists()
    assert not (tmp_path / "receipt.json").exists()


def test_real_source_lineage_hashes_cost_and_single_dispatch_remain_enforced() -> None:
    paths = _event_evidence()
    read = lambda name: json.loads(paths[name].read_text(encoding="utf-8"))
    gate = read("b4_post_research_reopen_current_invest_eligibility_zero_call_v0_4__40d7f5c.json")
    entry = read("b4_post_research_reopen_current_judge_entry_zero_call_v0_4__40d7f5c.json")
    preflight = read("b4_post_research_reopen_current_judge_preflight_zero_call_v0_4__40d7f5c.json")
    readiness = read("b4_post_research_reopen_current_judge_readiness_zero_call_v0_4__40d7f5c.json")
    approval = read("b4_post_research_reopen_current_judge_owner_approval_v0_4__40d7f5c.json")
    ledger = read("b4_post_research_reopen_current_judge_paid_dispatch_ledger_v0_4__40d7f5c.json")
    raw = read("b4_post_research_reopen_current_judge_raw_response_v0_4__40d7f5c.json")
    source, _ = MODULE._verify_original_lineage(
        source_executor_head=MODULE.SOURCE_EXECUTOR_HEAD,
        gate=gate,
        entry=entry,
        preflight=preflight,
        readiness=readiness,
        owner_approval=approval,
        ledger=ledger,
        raw=raw,
    )
    assert MODULE.actual_cost_usd(
        raw["raw_response"], model="gpt-5.6-terra", pricing=source["pricing"]
    ) == MODULE.EXPECTED_ACTUAL_COST_USD


@pytest.mark.parametrize("existing", ["result", "receipt"])
def test_partial_pair_rerun_verifies_existing_and_creates_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: str,
) -> None:
    result, result_path, receipt_path = _recover_synthetic(tmp_path, monkeypatch)
    if existing == "result":
        receipt_path.unlink()
    else:
        result_path.unlink()
    rerun, _, _ = _recover_synthetic(tmp_path, monkeypatch)
    assert rerun == result
    assert result_path.exists()
    assert receipt_path.exists()


@pytest.mark.parametrize("different", ["result", "receipt"])
def test_differing_existing_pair_artifact_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    different: str,
) -> None:
    _, result_path, receipt_path = _recover_synthetic(tmp_path, monkeypatch)
    path = result_path if different == "result" else receipt_path
    path.write_text('{"different":true}\n', encoding="utf-8")
    with pytest.raises(MODULE.CapturedResponseRecoveryError, match="existing recovery artifact differs"):
        _recover_synthetic(tmp_path, monkeypatch)


def _args() -> Namespace:
    return Namespace(
        source_executor_head=MODULE.SOURCE_EXECUTOR_HEAD,
        gate="gate.json",
        entry="entry.json",
        preflight="preflight.json",
        readiness="readiness.json",
        owner_approval="approval.json",
        ledger="ledger.json",
        raw="raw.json",
        recovered_result="result.json",
        recovery_receipt="receipt.json",
    )


def test_cli_requires_canonical_branch_and_clean_tracked_checkout_before_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MODULE, "parse_args", lambda _argv=None: _args())
    called = False

    def no_recovery(**_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(MODULE, "recover_captured_response", no_recovery)
    monkeypatch.setattr(MODULE, "_git", lambda *_args: "wrong-branch")
    with pytest.raises(MODULE.CapturedResponseRecoveryError, match="canonical branch"):
        MODULE.main([])
    assert called is False

    def dirty_git(*args: str) -> str:
        values = {
            ("branch", "--show-current"): MODULE.CANONICAL_BRANCH,
            ("status", "--porcelain=v1", "--untracked-files=no"): " M tracked.py",
        }
        return values[args]

    monkeypatch.setattr(MODULE, "_git", dirty_git)
    with pytest.raises(MODULE.CapturedResponseRecoveryError, match="tracked worktree"):
        MODULE.main([])
    assert called is False


def test_cli_allows_untracked_runtime_and_binds_clean_canonical_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MODULE, "parse_args", lambda _argv=None: _args())
    observed: dict[str, object] = {}
    values = {
        ("branch", "--show-current"): MODULE.CANONICAL_BRANCH,
        ("status", "--porcelain=v1", "--untracked-files=no"): "",
        ("rev-parse", "HEAD"): "b" * 40,
    }
    monkeypatch.setattr(MODULE, "_git", lambda *args: values[args])
    monkeypatch.setattr(MODULE, "_read", lambda _path: {})
    monkeypatch.setattr(
        MODULE,
        "recover_captured_response",
        lambda **kwargs: observed.update(kwargs) or {"status": "SYNTHETIC"},
    )
    assert MODULE.main([]) == 0
    assert observed["recovery_code_head"] == "b" * 40
