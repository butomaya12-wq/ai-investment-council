from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from aic.council import reopen_initial_runtime as runtime
from aic.domain.canonical import canonical_sha256


class _Request:
    def __init__(self, request_hash: str) -> None:
        self.request_hash = request_hash
        self.request_payload = {
            "model": "gpt-5.6-terra",
            "reasoning": {"effort": "low"},
            "max_output_tokens": 4096,
        }


def _fake_plan():
    plan = []
    index = 0
    for candidate in runtime.EXPECTED_CANDIDATES:
        for stage, lane in runtime._STAGE_LANE:
            index += 1
            plan.append(
                SimpleNamespace(
                    dispatch_index=index,
                    candidate_id=candidate,
                    lane=lane,
                    stage=stage,
                    request=_Request(f"request-{index}"),
                    request_body_utf8_bytes=1000 + index,
                )
            )
    return tuple(plan)


def _cost_fixture(monkeypatch: pytest.MonkeyPatch):
    rows = []
    for item in _fake_plan():
        rows.append(
            {
                "candidate_id": item.candidate_id,
                "lane": item.lane.value,
                "stage": item.stage.value,
                "model_run_ref": f"run-{item.dispatch_index}",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "low",
                "model_input_hash": f"input-{item.dispatch_index}",
                "effective_bundle_hash": f"bundle-{item.dispatch_index}",
                "historical_candidate_packet_hash": f"packet-{item.dispatch_index}",
                "request_hash": item.request.request_hash,
                "request_body_utf8_bytes": item.request_body_utf8_bytes,
                "input_tokens_upper_bound": item.request_body_utf8_bytes,
                "max_output_tokens": 4096,
                "per_call_cost_upper_bound_usd": "1",
                "effective_material_claim_count": 1,
                "effective_material_claim_ids": [f"claim-{item.dispatch_index}"],
                "schema_allows_all_effective_material_claim_ids": True,
                "effective_data_gap_refs": [],
            }
        )
    manifest = canonical_sha256(
        {
            "rows": [
                {
                    "candidate_id": row["candidate_id"],
                    "lane": row["lane"],
                    "model_input_hash": row["model_input_hash"],
                    "effective_bundle_hash": row["effective_bundle_hash"],
                    "request_hash": row["request_hash"],
                    "request_body_utf8_bytes": row["request_body_utf8_bytes"],
                    "max_output_tokens": row["max_output_tokens"],
                }
                for row in rows
            ]
        }
    )
    monkeypatch.setattr(runtime, "EXPECTED_REQUEST_MANIFEST_HASH", manifest)
    monkeypatch.setattr(runtime, "EXPECTED_EFFECTIVE_INPUT_MANIFEST_HASH", "e" * 64)
    monkeypatch.setattr(runtime, "EXPECTED_COST_PREFLIGHT_SOURCE_HEAD", "a" * 40)
    monkeypatch.setattr(runtime, "EXPECTED_COST_CEILING_USD", Decimal("9"))

    artifact = {
        "artifact_version": "B4_REOPEN_PRODUCTION_COST_PREFLIGHT_v0_1",
        "status": "B4_REOPEN_PRODUCTION_COST_PREFLIGHT_ZERO_CALL_PASS",
        "code_commit_sha": "a" * 40,
        "cost_authority_mode": "STAGED_EXACT",
        "source_b4_reopen_lifecycle_plan_hash": runtime.EXPECTED_LIFECYCLE_HASH,
        "source_b4_reopen_input_overlay_hash": runtime.EXPECTED_OVERLAY_HASH,
        "source_b3_reopen_closure_hash": runtime.EXPECTED_CLOSURE_HASH,
        "source_initial_selected_model_selection_hash": runtime.EXPECTED_INITIAL_SELECTION_HASH,
        "effective_material_claim_count": 37,
        "effective_unresolved_data_gap_refs": [],
        "effective_unresolved_reopen_reason_codes": [],
        "selected_initial_model": dict(runtime.EXPECTED_SELECTED_MODEL),
        "planned_total_production_calls_max": 13,
        "exactly_costed_now_stage": "INITIAL",
        "exactly_costed_now_calls": 9,
        "deferred_exact_costing_calls": 4,
        "all_13_owner_approval_ready": False,
        "next_owner_approval_scope": "INITIAL_ONLY",
        "request_manifest_hash": manifest,
        "effective_input_manifest_hash": "e" * 64,
        "initial_request_rows": rows,
        "max_request_body_utf8_bytes": max(row["request_body_utf8_bytes"] for row in rows),
        "max_output_tokens_per_call": 4096,
        "initial_exact_cost_upper_bound_usd": "9",
        "owner_cost_approval_required": True,
        "initial_paid_dispatch_authorized": False,
        "rebuttal_paid_dispatch_authorized": False,
        "judge_paid_dispatch_authorized": False,
        "model_calls_authorized": False,
        "provider_reads_authorized": False,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "rerun_authorized": False,
        "broker_writes_authorized": 0,
        "alpaca_orders_authorized": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    monkeypatch.setattr(runtime, "EXPECTED_COST_PREFLIGHT_HASH", artifact["artifact_hash"])
    return artifact, _fake_plan()


def test_cost_preflight_verifier_binds_exact_nine_rows(monkeypatch: pytest.MonkeyPatch):
    cost, _ = _cost_fixture(monkeypatch)
    assert runtime.verify_reopen_initial_cost_preflight(cost) == cost["artifact_hash"]


def test_cost_preflight_verifier_rejects_rebuttal_authority(monkeypatch: pytest.MonkeyPatch):
    cost, _ = _cost_fixture(monkeypatch)
    cost = dict(cost)
    cost["rebuttal_paid_dispatch_authorized"] = True
    cost["artifact_hash"] = canonical_sha256(cost, exclude_fields=("artifact_hash",))
    monkeypatch.setattr(runtime, "EXPECTED_COST_PREFLIGHT_HASH", cost["artifact_hash"])
    with pytest.raises(runtime.B4ReopenInitialRuntimeError, match="rebuttal_paid_dispatch_authorized"):
        runtime.verify_reopen_initial_cost_preflight(cost)


def test_dry_artifact_is_zero_call_and_not_authority(monkeypatch: pytest.MonkeyPatch):
    cost, plan = _cost_fixture(monkeypatch)
    dry = runtime.build_reopen_initial_dry_artifact(
        code_commit_sha="b" * 40,
        cost_preflight=cost,
        plan=plan,
    )
    assert dry["status"] == runtime.REOPEN_INITIAL_DRY_STATUS
    assert dry["planned_paid_calls_max"] == 9
    assert dry["paid_dispatch_authorized"] is False
    assert dry["rebuttal_authorized"] is False
    assert dry["judge_authorized"] is False
    assert dry["model_calls"] == 0
    assert dry["provider_reads"] == 0
    assert dry["artifact_hash"] == canonical_sha256(dry, exclude_fields=("artifact_hash",))


def test_paid_authorization_requires_exact_ceiling_and_clean_worktree(
    monkeypatch: pytest.MonkeyPatch,
):
    cost, plan = _cost_fixture(monkeypatch)
    dry = runtime.build_reopen_initial_dry_artifact(
        code_commit_sha="b" * 40,
        cost_preflight=cost,
        plan=plan,
    )
    with pytest.raises(runtime.B4ReopenInitialRuntimeError, match="exactly equal"):
        runtime.build_reopen_initial_paid_authorization(
            cost_preflight=cost,
            dry_artifact=dry,
            plan=plan,
            approve_cost_artifact_hash=cost["artifact_hash"],
            approve_max_usd="8.99",
            owner_approval_id="OWNER-TEST",
            owner_approval_at_utc="2026-08-30T20:00:00Z",
            code_commit_sha="b" * 40,
            git_worktree_clean=True,
            created_at_utc="2026-08-30T20:00:01Z",
            run_id="RUN-TEST",
            receipt_journal_path=".aic-runtime/test.jsonl",
        )
    with pytest.raises(runtime.B4ReopenInitialRuntimeError, match="clean worktree"):
        runtime.build_reopen_initial_paid_authorization(
            cost_preflight=cost,
            dry_artifact=dry,
            plan=plan,
            approve_cost_artifact_hash=cost["artifact_hash"],
            approve_max_usd="9",
            owner_approval_id="OWNER-TEST",
            owner_approval_at_utc="2026-08-30T20:00:00Z",
            code_commit_sha="b" * 40,
            git_worktree_clean=False,
            created_at_utc="2026-08-30T20:00:01Z",
            run_id="RUN-TEST",
            receipt_journal_path=".aic-runtime/test.jsonl",
        )


def test_paid_authorization_keeps_later_stages_and_rerun_false(
    monkeypatch: pytest.MonkeyPatch,
):
    cost, plan = _cost_fixture(monkeypatch)
    dry = runtime.build_reopen_initial_dry_artifact(
        code_commit_sha="b" * 40,
        cost_preflight=cost,
        plan=plan,
    )
    auth = runtime.build_reopen_initial_paid_authorization(
        cost_preflight=cost,
        dry_artifact=dry,
        plan=plan,
        approve_cost_artifact_hash=cost["artifact_hash"],
        approve_max_usd="9",
        owner_approval_id="OWNER-TEST",
        owner_approval_at_utc="2026-08-30T20:00:00Z",
        code_commit_sha="b" * 40,
        git_worktree_clean=True,
        created_at_utc="2026-08-30T20:00:01Z",
        run_id="RUN-TEST",
        receipt_journal_path=".aic-runtime/test.jsonl",
    )
    assert auth["planned_paid_calls_max"] == 9
    assert auth["automatic_repair_calls_authorized"] is False
    assert auth["authorization_consumed_before_dispatch"] is False
    assert auth["authorization_consumption_rule"] == "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT"
    assert auth["owner_approval"]["rebuttal_authorized"] is False
    assert auth["owner_approval"]["judge_authorized"] is False
    assert auth["owner_approval"]["rerun_authorized"] is False


def test_dispatch_attempt_event_consumes_authority_before_provider_result():
    item = _fake_plan()[0]
    event = runtime.build_dispatch_attempt_event(
        run_id="RUN",
        item=item,
        authorization_hash="f" * 64,
        started_at_utc="2026-08-30T20:00:00Z",
    )
    assert event["event_type"] == "PROVIDER_DISPATCH_ATTEMPT"
    assert event["authorization_consumed_by_this_attempt"] is True
    assert event["automatic_repair_attempted"] is False
    assert event["event_hash"] == canonical_sha256(event, exclude_fields=("event_hash",))


def test_receipt_without_provider_response_is_unknown_and_incomplete(monkeypatch: pytest.MonkeyPatch):
    item = _fake_plan()[0]
    receipt = runtime.build_paid_call_receipt(
        run_id="RUN",
        item=item,
        authorization_hash="f" * 64,
        attempt_event_hash="e" * 64,
        started_at_utc="2026-08-30T20:00:00Z",
        finished_at_utc="2026-08-30T20:00:01Z",
        provider_response_received=False,
        raw_response=None,
        latency_ms=1000,
        processed_record=None,
        validation_error="UNKNOWN_PROVIDER_DISPATCH:TimeoutError",
        pricing={},
    )
    assert receipt["provider_dispatch_state_unknown"] is True
    assert receipt["cost_receipt_status"] == "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH"
    assert receipt["actual_cost_usd"] is None
    assert receipt["rerun_authorized"] is False
    assert receipt["receipt_hash"] == canonical_sha256(receipt, exclude_fields=("receipt_hash",))


def test_paid_runner_source_orders_durable_attempt_before_post_and_auth_before_key_load():
    source = Path("scripts/b4_reopen_run_initial_runtime_v01.py").read_text(encoding="utf-8")
    auth_write = source.index("_write_durable_fresh(args.authorization_output, authorization)")
    key_load = source.index("api_key = load_openai_api_key()")
    attempt_append = source.index("_append_event(args.receipt_journal, attempt)")
    provider_post = source.index("raw = transport.post")
    assert auth_write < key_load
    assert attempt_append < provider_post
    assert "--execute-paid-reopen-initial" in source
    assert "automatic_repair_calls_authorized" in source
    assert "rebuttal_authorized" in source
    assert "judge_authorized" in source
