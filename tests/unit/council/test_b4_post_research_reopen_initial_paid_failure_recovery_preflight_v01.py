from __future__ import annotations

import json
from pathlib import Path

from aic.council import post_research_reopen_initial_paid_failure_recovery_preflight_v01 as recovery
from aic.council import post_research_reopen_initial_production_dispatch_v01 as dispatch


LEDGER = Path(".aic-runtime/b4_post_research_reopen_initial_paid_dispatch_ledger_v0_1.json")
COST = Path(".aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")


def _inputs() -> tuple[dict[str, object], dict[str, object], str]:
    return (
        json.loads(LEDGER.read_text(encoding="utf-8")),
        json.loads(COST.read_text(encoding="utf-8")),
        recovery.file_sha256(LEDGER),
    )


def test_real_paid_failure_ledger_is_preserved_and_bound_to_the_recovery_preflight() -> None:
    before = LEDGER.read_bytes()
    ledger, cost, ledger_file_sha256 = _inputs()
    artifact = recovery.build_failure_recovery_preflight(
        ledger=ledger,
        ledger_file_sha256=ledger_file_sha256,
        cost_preflight=cost,
        raw_response_dir_exists=False,
        raw_response_file_count=0,
        fresh_result_exists=False,
    )
    assert LEDGER.read_bytes() == before
    assert ledger_file_sha256 == recovery.EXPECTED_LEDGER_FILE_SHA256
    assert artifact["existing_ledger_hash"] == recovery.EXPECTED_LEDGER_HASH
    assert recovery.verify_failure_recovery_preflight(
        artifact, ledger=ledger, ledger_file_sha256=ledger_file_sha256, cost_preflight=cost
    ) == artifact["artifact_hash"]


def test_recovery_preflight_is_fail_closed_and_requires_new_owner_exception_for_any_full_recovery() -> None:
    ledger, cost, ledger_file_sha256 = _inputs()
    artifact = recovery.build_failure_recovery_preflight(
        ledger=ledger,
        ledger_file_sha256=ledger_file_sha256,
        cost_preflight=cost,
        raw_response_dir_exists=False,
        raw_response_file_count=0,
        fresh_result_exists=False,
    )
    request_1 = artifact["request_1"]
    remaining = artifact["requests_2_to_9"]
    options = artifact["recovery_options"]
    assert request_1["recovery_state"] == recovery.RECOVERY_CLASSIFICATION
    assert request_1["provider_acceptance"] == "CONFIRMED_BY_CONTROL_FLOW"
    assert request_1["provider_response_returned"] is True
    assert request_1["response_content_recoverable_locally"] is False
    assert request_1["automatic_resend_permitted"] is False
    assert request_1["estimated_max_cost_usd"] == "0.636487"
    assert request_1["actual_cost_usd"] == "UNKNOWN"
    assert remaining["state"] == dispatch.NOT_DISPATCHED
    assert remaining["estimated_max_cost_usd"] == "5.089556"
    assert artifact["original_total_max_cost_usd"] == "5.726043"
    assert options["option_a_abort_current_initial"]["model_calls_authorized"] is False
    full = options["option_b_full_initial_recovery"]
    assert full["additional_provider_calls_required"] == 9
    assert full["total_provider_call_lineage"] == 10
    assert full["existing_call_count_ceiling"] == 9
    assert full["existing_approval_valid"] is False
    assert full["authorization_status"] == "OWNER_EXCEPTION_REQUIRED"
    assert full["automatic_retry"] is False
    assert artifact["store_false_no_normal_saved_response_retrieval"] is True
    assert artifact["speculative_provider_read_authorized"] is False
    assert artifact["model_calls_this_step"] == 0
    assert artifact["provider_reads_this_step"] == 0


def test_recovery_runner_is_zero_call_and_has_no_dispatcher() -> None:
    script = Path("scripts/b4_post_research_reopen_initial_paid_failure_recovery_preflight_zero_call_v01.py").read_text(encoding="utf-8")
    assert "execute_paid_initial" not in script
    assert "transport" not in script
    assert "MODEL_CALLS_THIS_STEP=0" in script
    assert "PROVIDER_READS_THIS_STEP=0" in script
