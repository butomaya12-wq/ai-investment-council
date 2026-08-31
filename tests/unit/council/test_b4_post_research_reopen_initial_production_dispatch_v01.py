from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from aic.council import post_research_reopen_initial_production_dispatch_v01 as runtime
from aic.council.model_selection import InitialSelectedModelAuthority
from aic.domain.canonical import canonical_sha256


HEAD = "a" * 40
PREFLIGHT_PATH = Path(".aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
AUTHORITY_PATH = Path("config/event/b4_initial_selected_model_v1.json")


def _inputs() -> tuple[dict, InitialSelectedModelAuthority]:
    return (
        json.loads(PREFLIGHT_PATH.read_text(encoding="utf-8")),
        InitialSelectedModelAuthority.model_validate(json.loads(AUTHORITY_PATH.read_text(encoding="utf-8"))),
    )


def _approval(preflight: dict, *, code_sha: str = HEAD) -> dict:
    payload = {
        "artifact_version": runtime.OWNER_APPROVAL_VERSION,
        "owner_approval_granted": True,
        "owner_approval_id": "OWNER-POST-RESEARCH-INITIAL-TEST",
        "owner_approval_at_utc": "2026-08-31T12:00:00Z",
        "cost_preflight_artifact_hash": runtime.EXPECTED_PREFLIGHT_HASH,
        "model": runtime.EXPECTED_MODEL,
        "reasoning_effort": runtime.EXPECTED_REASONING_EFFORT,
        "planned_call_count": 9,
        "call_count_ceiling": 9,
        "max_output_tokens_per_call": 4096,
        "approved_max_estimated_cost_usd": "5.726043",
        "request_set_hash": preflight["request_set_hash"],
        "request_hashes": [row["request_hash"] for row in preflight["initial_requests"]],
        "automatic_retries": 0,
        "approved_dispatch_code_commit_sha": code_sha,
    }
    payload["artifact_hash"] = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    return payload


def test_zero_call_contract_binds_exact_new_preflight_and_preserves_no_authority() -> None:
    preflight, authority = _inputs()
    artifact = runtime.build_zero_call_dispatch_preflight(
        code_commit_sha=HEAD, cost_preflight=preflight, authority=authority
    )
    assert artifact["source_cost_preflight_hash"] == runtime.EXPECTED_PREFLIGHT_HASH
    assert artifact["model"] == "gpt-5.6-terra"
    assert artifact["reasoning_effort"] == "low"
    assert artifact["call_count"] == 9
    assert artifact["max_output_tokens_per_call"] == 4096
    assert artifact["owner_approval_status"] == "NOT_GRANTED"
    assert artifact["model_calls_authorized"] is False
    assert artifact["provider_reads_authorized"] is False
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert runtime.verify_zero_call_dispatch_preflight(artifact, expected_code_commit_sha=HEAD) == artifact["artifact_hash"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("approved_max_estimated_cost_usd", "5.726044", "cost ceiling"),
        ("planned_call_count", 8, "call count"),
        ("max_output_tokens_per_call", 4097, "output cap"),
        ("automatic_retries", 1, "permits retries"),
    ],
)
def test_owner_approval_rejects_expanded_or_mismatched_authority(field: str, value: object, message: str) -> None:
    preflight, _authority = _inputs()
    approval = _approval(preflight)
    approval[field] = value
    approval["artifact_hash"] = canonical_sha256(approval, exclude_fields=("artifact_hash",))
    with pytest.raises(runtime.PostResearchReopenInitialProductionDispatchError, match=message):
        runtime.verify_owner_approval(approval, cost_preflight=preflight, dispatch_code_commit_sha=HEAD)


def test_owner_approval_rejects_request_hash_tamper_and_missing_explicit_grant() -> None:
    preflight, _authority = _inputs()
    approval = _approval(preflight)
    approval["request_hashes"][0] = "0" * 64
    approval["artifact_hash"] = canonical_sha256(approval, exclude_fields=("artifact_hash",))
    with pytest.raises(runtime.PostResearchReopenInitialProductionDispatchError, match="request hashes"):
        runtime.verify_owner_approval(approval, cost_preflight=preflight, dispatch_code_commit_sha=HEAD)
    approval = _approval(preflight)
    approval["owner_approval_granted"] = False
    approval["artifact_hash"] = canonical_sha256(approval, exclude_fields=("artifact_hash",))
    with pytest.raises(runtime.PostResearchReopenInitialProductionDispatchError, match="not explicitly granted"):
        runtime.verify_owner_approval(approval, cost_preflight=preflight, dispatch_code_commit_sha=HEAD)


def test_cost_preflight_tamper_and_partial_unknown_dispatch_fail_closed() -> None:
    preflight, _authority = _inputs()
    tampered = deepcopy(preflight)
    tampered["initial_requests"][0]["request_payload"]["max_output_tokens"] = 4097
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(Exception):
        runtime.verify_cost_preflight_for_dispatch(tampered)
    ledger = runtime.build_not_dispatched_ledger(preflight)
    ledger[0]["state"] = runtime.DISPATCH_STARTED_UNKNOWN
    with pytest.raises(runtime.PostResearchReopenInitialProductionDispatchError, match="fail-closed"):
        runtime.assert_ledger_safe_before_dispatch(ledger)


def test_production_output_is_exclusive_and_historical_outputs_are_not_reusable(tmp_path: Path) -> None:
    preflight, authority = _inputs()
    artifact = runtime.build_zero_call_dispatch_preflight(
        code_commit_sha=HEAD, cost_preflight=preflight, authority=authority
    )
    assert artifact["historical_b4_model_outputs_reusable_as_fresh_outputs"] is False
    output = tmp_path / "fresh.json"
    output.write_text("existing", encoding="utf-8")
    with pytest.raises(runtime.PostResearchReopenInitialProductionDispatchError, match="already exists"):
        runtime.assert_exclusive_output(output)


def test_pre_dispatch_environment_requires_exact_owner_bound_code_and_no_fresh_result() -> None:
    preflight, authority = _inputs()
    approval = _approval(preflight)
    assert runtime.verify_pre_dispatch_environment(
        branch=runtime.EXPECTED_BRANCH,
        code_commit_sha=HEAD,
        git_worktree_clean=True,
        cost_preflight=preflight,
        authority=authority,
        owner_approval=approval,
        fresh_initial_result_exists=False,
    ) == approval["artifact_hash"]
    with pytest.raises(runtime.PostResearchReopenInitialProductionDispatchError, match="already exists"):
        runtime.verify_pre_dispatch_environment(
            branch=runtime.EXPECTED_BRANCH,
            code_commit_sha=HEAD,
            git_worktree_clean=True,
            cost_preflight=preflight,
            authority=authority,
            owner_approval=approval,
            fresh_initial_result_exists=True,
        )


def test_zero_call_runner_has_no_model_provider_or_broker_execution_surface() -> None:
    text = Path("scripts/b4_post_research_reopen_initial_production_dispatch_zero_call_v01.py").read_text(encoding="utf-8").lower()
    assert "responses.create" not in text
    assert "chat.completions" not in text
    assert "alpaca.data" not in text
    assert "alpaca.trading" not in text
    assert "--execute" not in text
    assert "model_calls_this_step=0" in text
    assert "provider_reads_this_step=0" in text
