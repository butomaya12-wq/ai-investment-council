from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

from aic.council.judge_eval_preflight import (
    EXPECTED_JUDGE_ENTRY_HASH,
    EXPECTED_JUDGE_EVAL_CASE_IDS,
    EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX,
    build_judge_eval_cost_preflight,
    build_judge_eval_request_preflight,
)
from aic.council.judge_eval_runtime import (
    JUDGE_EVAL_RUNTIME_VERSION,
    dry_run_manifest,
)
from aic.domain.canonical import canonical_sha256


def _runner_module():
    path = Path("scripts/b4_judge_model_eval_v01.py")
    spec = importlib.util.spec_from_file_location(
        "_aic_judge_eval_runner_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _entry() -> dict:
    return {
        "allowed_judge_outcomes_for_current_frozen_run": [
            "WATCH",
            "ABSTAIN",
        ],
        "alpaca_orders": 0,
        "artifact_hash": EXPECTED_JUDGE_ENTRY_HASH,
        "artifact_version": "B4_JUDGE_ENTRY_PREFLIGHT_v0_1",
        "b3_reopen_is_separate_lifecycle": True,
        "broker_writes": 0,
        "candidate_order": ["NVDA", "MSFT", "META"],
        "code_commit_sha": "1250ee490170db20c25a2d4e01d98ab64c2ee1c7",
        "invest_block_reason": (
            "RESEARCH_REOPEN_REQUIRED_FOR_ALL_THREE_FROZEN_CANDIDATES"
        ),
        "invest_eligible_candidates": [],
        "invest_persistence_allowed": False,
        "judge_authorized": False,
        "judge_entry_barrier_satisfied": True,
        "judge_execution_authorized": False,
        "judge_model_selection_required": True,
        "live_money": "PROHIBITED",
        "model_calls": 0,
        "new_research_inside_b4_allowed": False,
        "paid_rebuttal_authorization_artifact_hash": (
            "1ddaa743678ebc3aae7c7050e84566f627f720f7ffa350d365e48f063b535443"
        ),
        "provider_reads": 0,
        "rebuttal_bundle_count": 3,
        "rebuttal_bundle_hashes": [
            "8824f4eeb792407a427657b9116c70a5a2557fd0958241b26f26854bd0361763",
            "e9ff46cb1e38db6ed525d34677a8af20048206fb1cc8f1a652b08815908fffb8",
            "dd400c55953a4c494611e5ab5f27c28a71bef1ab10a0774901083cb9914282a8",
        ],
        "rebuttal_council_freeze_artifact_hash": (
            "dd83aa4f873c0d6ac6582cd6dc89c1612088239aa8e979d18f7f95c3386552a5"
        ),
        "rebuttal_receipt_manifest_hash": (
            "c36cb817bf0e61020a0781cd7a6dc30c5432acaaa2184f93abb8e4f1565270d3"
        ),
        "rebuttal_run_id": (
            "AIC-B4-REBUTTAL-RUNTIME-20260830T122106121542Z-b5dba042bc75"
        ),
        "rerun_authorized": False,
        "research_reopen_must_remain_visible_to_judge": True,
        "research_reopen_required_candidates": ["NVDA", "MSFT", "META"],
        "status": "PASS_ZERO_CALL_JUDGE_ENTRY_RESEARCH_REOPEN_BOUND",
    }


def _authorities(head: str = "a" * 40):
    request = build_judge_eval_request_preflight(
        code_commit_sha=head,
        entry_preflight=_entry(),
    )
    cost = build_judge_eval_cost_preflight(request)
    return request, cost


def test_judge_eval_runtime_dry_manifest_is_exact_three_by_seven_surface() -> None:
    manifest = dry_run_manifest()
    assert manifest["runtime_version"] == JUDGE_EVAL_RUNTIME_VERSION
    assert manifest["request_count"] == 21
    assert manifest["candidate_keys"] == ["J1", "J2", "J3"]
    assert manifest["case_ids"] == list(EXPECTED_JUDGE_EVAL_CASE_IDS)
    assert [
        (row["candidate_key"], row["case_id"])
        for row in manifest["requests"]
    ] == [
        (candidate_key, case_id)
        for candidate_key in ("J1", "J2", "J3")
        for case_id in EXPECTED_JUDGE_EVAL_CASE_IDS
    ]
    assert all(
        row["max_output_tokens"] == 8192
        for row in manifest["requests"]
    )
    assert manifest["manifest_hash"] == canonical_sha256(
        manifest,
        exclude_fields=("manifest_hash",),
    )


def test_paid_runner_dry_binds_exact_request_cost_and_event_authorities() -> None:
    runner = _runner_module()
    request, cost = _authorities()
    dry = runner._dry_run(request, cost)
    artifact = runner._runner_dry_artifact(
        request_preflight=request,
        cost_preflight=cost,
        dry=dry,
    )

    assert dry["request_preflight_hash"] == request["artifact_hash"]
    assert dry["request_manifest_hash"] == request["request_manifest_hash"]
    assert dry["cost_preflight_hash"] == cost["artifact_hash"]
    assert str(dry["cost_ceiling_usd"]) == cost[
        "total_judge_eval_cost_upper_bound_usd"
    ]
    assert artifact["artifact_hash"] == canonical_sha256(
        artifact,
        exclude_fields=("artifact_hash",),
    )
    assert artifact["judge_entry_preflight_artifact_hash"] == (
        EXPECTED_JUDGE_ENTRY_HASH
    )
    assert artifact["planned_paid_calls_max"] == 21
    assert artifact["max_output_tokens_per_call"] == 8192
    assert artifact["consumption_rule"] == (
        "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT"
    )
    assert artifact["unknown_dispatch_fail_closed"] is True
    assert artifact["semantic_fail_continues_ladder"] is True
    assert artifact["stop_on_incomplete_cost_receipt"] is True
    assert artifact["automatic_repair_calls_authorized"] is False
    assert artifact["production_judge_authorized"] is False
    assert artifact["rerun_authorized"] is False
    assert artifact["model_calls"] == 0
    assert artifact["provider_reads"] == 0
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert artifact["live_money"] == "PROHIBITED"


def test_paid_authorization_requires_all_exact_hashes_runner_dry_and_ceiling() -> None:
    runner = _runner_module()
    request, cost = _authorities()
    dry = runner._dry_run(request, cost)
    runner_dry = runner._runner_dry_artifact(
        request_preflight=request,
        cost_preflight=cost,
        dry=dry,
    )

    approved = runner.validate_paid_execution_authorization(
        request_preflight=request,
        cost_preflight=cost,
        runner_dry=runner_dry,
        approve_request_preflight_hash=request["artifact_hash"],
        approve_request_manifest_hash=request["request_manifest_hash"],
        approve_cost_artifact_hash=cost["artifact_hash"],
        approve_runner_dry_artifact_hash=runner_dry["artifact_hash"],
        approve_max_usd=cost["total_judge_eval_cost_upper_bound_usd"],
    )
    assert str(approved) == cost["total_judge_eval_cost_upper_bound_usd"]

    mutations = (
        {
            "approve_request_preflight_hash": "0" * 64,
        },
        {
            "approve_request_manifest_hash": "0" * 64,
        },
        {
            "approve_cost_artifact_hash": "0" * 64,
        },
        {
            "approve_runner_dry_artifact_hash": "0" * 64,
        },
        {
            "approve_max_usd": "999",
        },
    )
    baseline = {
        "approve_request_preflight_hash": request["artifact_hash"],
        "approve_request_manifest_hash": request["request_manifest_hash"],
        "approve_cost_artifact_hash": cost["artifact_hash"],
        "approve_runner_dry_artifact_hash": runner_dry["artifact_hash"],
        "approve_max_usd": cost["total_judge_eval_cost_upper_bound_usd"],
    }
    for mutation in mutations:
        kwargs = {**baseline, **mutation}
        with pytest.raises(runner.JudgeEvalAuthorizationError):
            runner.validate_paid_execution_authorization(
                request_preflight=request,
                cost_preflight=cost,
                runner_dry=runner_dry,
                **kwargs,
            )


def test_dispatch_tracker_consumes_authority_even_when_delegate_raises() -> None:
    runner = _runner_module()

    class BrokenTransport:
        def post(self, *, payload, api_key):
            raise RuntimeError("unknown provider state")

    tracker = runner.DispatchTrackingTransport(BrokenTransport())
    with pytest.raises(RuntimeError, match="unknown provider state"):
        tracker.post(payload={"model": "x"}, api_key="not-a-real-key")
    assert tracker.dispatch_attempts == 1
    assert tracker.provider_responses == 0

    blocked = runner._blocked_artifact(
        status="BLOCKED_UNKNOWN_PROVIDER_DISPATCH",
        reason="unknown provider state",
        run_id="RUN",
        code_commit_sha="a" * 40,
        request_preflight={
            "artifact_hash": "1" * 64,
            "request_manifest_hash": "2" * 64,
        },
        cost_preflight={"artifact_hash": "3" * 64},
        runner_dry={"artifact_hash": "4" * 64},
        authorization_hash="5" * 64,
        ceiling=runner.Decimal("1"),
        dispatch_attempts=1,
        model_calls=0,
        known_cost=runner.Decimal("0"),
        receipt_hashes=[],
        receipt_journal=Path("receipts.jsonl"),
    )
    assert blocked["judge_eval_authorization_consumed"] is True
    assert blocked["production_judge_authorized"] is False
    assert blocked["rerun_authorized"] is False


def test_paid_runner_secret_load_happens_after_durable_authorization() -> None:
    text = Path("scripts/b4_judge_model_eval_v01.py").read_text(
        encoding="utf-8"
    )
    authorization_write = text.index(
        "_write_durable_new(args.authorization_output, authorization)"
    )
    key_load = text.index("load_openai_api_key()")
    assert authorization_write < key_load
    assert (
        '"consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT"'
        in text
    )
    assert '"semantic_fail_continues_ladder": True' in text
    assert '"automatic_repair_attempted": False' in text
    assert '"production_judge_authorized": False' in text
    assert '"rerun_authorized": False' in text
    assert "_require_fresh_paid_paths(" in text
    assert "EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX" in text
    assert "--execute-paid-eval" in text
    assert "retry" not in text.lower()


def test_paid_evidence_paths_are_fresh_only(tmp_path: Path) -> None:
    runner = _runner_module()
    empty = tmp_path / "new.json"
    runner._require_fresh_paid_paths(empty)
    empty.write_text("{}", encoding="utf-8")
    with pytest.raises(
        runner.JudgeEvalAuthorizationError,
        match="refusing overwrite",
    ):
        runner._require_fresh_paid_paths(empty)


def test_runner_plan_constants_match_frozen_judge_eval_surface() -> None:
    runner = _runner_module()
    assert runner.EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX == 21
    assert runner.EXPECTED_MAX_OUTPUT_TOKENS == 8192
    assert runner.EXPECTED_JUDGE_EVAL_CASE_IDS == (
        "E3",
        "E4",
        "E10",
        "E11",
        "E12",
        "E14",
        "E15",
    )
    assert [item.candidate_key for item in runner.JUDGE_MODEL_LADDER] == [
        "J1",
        "J2",
        "J3",
    ]
