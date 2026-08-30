from __future__ import annotations

import pytest

from aic.council.eval_cost import load_openai_text_pricing
from aic.council.initial_runtime_authorization import (
    InitialRuntimeAuthorizationError,
    build_initial_runtime_paid_authorization,
)
from aic.council.initial_runtime_cost import build_initial_runtime_cost_preflight
from aic.council.initial_runtime_preflight import (
    INITIAL_RUNTIME_REQUEST_PREFLIGHT_RUN_CLASS,
    INITIAL_RUNTIME_REQUEST_PREFLIGHT_VERSION,
    RUNTIME_REQUEST_PREFLIGHT_STATUS,
)
from aic.council.model_policy import OUTPUT_TOKEN_BUDGET_VERSION
from aic.council.model_selection import load_initial_selected_model_authority
from aic.domain.canonical import canonical_sha256


HEAD = "a" * 40


def _runtime():
    authority = load_initial_selected_model_authority()
    variants = [
        {
            "candidate": candidate,
            "lane": lane,
            "model": authority.selected_candidate.model,
            "request_body_utf8_bytes": 20000,
        }
        for candidate in ("AAA", "BBB", "CCC")
        for lane in ("BULL", "BEAR", "RED_TEAM")
    ]
    artifact = {
        "artifact_version": INITIAL_RUNTIME_REQUEST_PREFLIGHT_VERSION,
        "run_class": INITIAL_RUNTIME_REQUEST_PREFLIGHT_RUN_CLASS,
        "status": RUNTIME_REQUEST_PREFLIGHT_STATUS,
        "code_commit_sha": HEAD,
        "source_request_preflight_artifact_hash": "1" * 64,
        "b4_input_freeze_artifact_hash": "2" * 64,
        "b3_reconciliation_artifact_hash": "3" * 64,
        "b2_handoff_hash": "4" * 64,
        "mandate_version": "TEST",
        "selected_model_authority_version": authority.artifact_version,
        "selected_model_authority_selection_hash": authority.selection_hash,
        "selected_model_eval_artifact_hash": authority.model_eval_artifact_hash,
        "selected_candidate": authority.selected_candidate.model_dump(mode="json"),
        "candidate_order": ["AAA", "BBB", "CCC"],
        "logical_call_count": 9,
        "planned_paid_calls_max": 9,
        "automatic_repair_calls_authorized": False,
        "output_token_budget_version": OUTPUT_TOKEN_BUDGET_VERSION,
        "max_output_tokens_per_call": 4096,
        "selected_request_variants": variants,
        "request_manifest_hash": canonical_sha256({"selected_request_variants": variants}),
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return authority, artifact


def test_paid_authorization_binds_exact_cost_hash_ceiling_and_scope() -> None:
    authority, runtime = _runtime()
    cost = build_initial_runtime_cost_preflight(runtime, pricing=load_openai_text_pricing())
    auth = build_initial_runtime_paid_authorization(
        runtime_preflight=runtime,
        cost_preflight=cost,
        authority=authority,
        approve_cost_artifact_hash=cost["artifact_hash"],
        approve_max_usd=cost["total_initial_runtime_cost_upper_bound_usd"],
        owner_approval_id="OWNER-B4-INITIAL-RUNTIME-TEST",
        owner_approval_at_utc="2026-08-30T04:00:00Z",
        code_commit_sha=HEAD,
        git_worktree_clean=True,
        created_at_utc="2026-08-30T04:01:00Z",
        run_id="B4-INITIAL-TEST-RUN",
        receipt_journal_path=".aic-runtime/test.jsonl",
    )
    assert auth["planned_paid_calls_max"] == 9
    assert auth["automatic_repair_calls_authorized"] is False
    assert auth["owner_approval"]["rebuttal_authorized"] is False
    assert auth["owner_approval"]["judge_authorized"] is False
    assert auth["owner_approval"]["rerun_authorized"] is False
    assert auth["model_calls"] == 0


def test_paid_authorization_rejects_higher_or_lower_ceiling() -> None:
    authority, runtime = _runtime()
    cost = build_initial_runtime_cost_preflight(runtime, pricing=load_openai_text_pricing())
    with pytest.raises(InitialRuntimeAuthorizationError, match="exactly equal"):
        build_initial_runtime_paid_authorization(
            runtime_preflight=runtime,
            cost_preflight=cost,
            authority=authority,
            approve_cost_artifact_hash=cost["artifact_hash"],
            approve_max_usd="99",
            owner_approval_id="OWNER-B4-INITIAL-RUNTIME-TEST",
            owner_approval_at_utc="2026-08-30T04:00:00Z",
            code_commit_sha=HEAD,
            git_worktree_clean=True,
            created_at_utc="2026-08-30T04:01:00Z",
            run_id="B4-INITIAL-TEST-RUN",
            receipt_journal_path=".aic-runtime/test.jsonl",
        )


def test_paid_authorization_rejects_dirty_or_wrong_head() -> None:
    authority, runtime = _runtime()
    cost = build_initial_runtime_cost_preflight(runtime, pricing=load_openai_text_pricing())
    common = dict(
        runtime_preflight=runtime,
        cost_preflight=cost,
        authority=authority,
        approve_cost_artifact_hash=cost["artifact_hash"],
        approve_max_usd=cost["total_initial_runtime_cost_upper_bound_usd"],
        owner_approval_id="OWNER-B4-INITIAL-RUNTIME-TEST",
        owner_approval_at_utc="2026-08-30T04:00:00Z",
        created_at_utc="2026-08-30T04:01:00Z",
        run_id="B4-INITIAL-TEST-RUN",
        receipt_journal_path=".aic-runtime/test.jsonl",
    )
    with pytest.raises(InitialRuntimeAuthorizationError, match="clean git"):
        build_initial_runtime_paid_authorization(
            **common,
            code_commit_sha=HEAD,
            git_worktree_clean=False,
        )
    with pytest.raises(InitialRuntimeAuthorizationError, match="exact preflight git commit"):
        build_initial_runtime_paid_authorization(
            **common,
            code_commit_sha="b" * 40,
            git_worktree_clean=True,
        )
