from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.council.model_selection import InitialSelectedModelAuthority
from aic.domain.canonical import canonical_sha256

from . import post_research_reopen_initial_request_cost_preflight_v01 as cost_v01


ZERO_CALL_ARTIFACT_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_PRODUCTION_DISPATCH_ZERO_CALL_PREFLIGHT_v0_1"
ZERO_CALL_PASS_STATUS = "B4_POST_RESEARCH_REOPEN_INITIAL_PRODUCTION_DISPATCH_ZERO_CALL_PREFLIGHT_PASS"
OWNER_APPROVAL_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_OWNER_APPROVAL_v0_1"
NEXT_GATE = "B4_POST_RESEARCH_REOPEN_INITIAL_EXPLICIT_OWNER_APPROVAL_THEN_PRODUCTION_DISPATCH"
EXPECTED_BRANCH = "hackathon/alpaca-2026"
EXPECTED_PREFLIGHT_HASH = "ea048c61099f501ce3b08d7a3869f2d41db9606bdd58f5ddaa7e152ff3996d11"
EXPECTED_PREFLIGHT_CODE_SHA = "f3b460f735b6fcdad451d98a4ea908415f599b7b"
EXPECTED_SOURCE_VERDICT_HASH = "fb8fb489ee31e1e3fb7763aee6499d1c95e7e7f02f4ac22a2d8ead2f479fde4d"
EXPECTED_B3_CLOSURE_HASH = "ad1f477df9094e40c26186a045c6ca5794cc1cf4c415929bb1453cd09b1e4149"
EXPECTED_INITIAL_SELECTION_HASH = "0554900c0e7c1b696a681301d249d011f6d500331fe53751998024477269d1e0"
EXPECTED_MODEL = "gpt-5.6-terra"
EXPECTED_REASONING_EFFORT = "low"
EXPECTED_CALL_COUNT = 9
EXPECTED_MAX_OUTPUT_TOKENS = 4096
EXPECTED_MAX_COST_USD = "5.726043"
NOT_DISPATCHED = "NOT_DISPATCHED"
DISPATCH_STARTED_UNKNOWN = "DISPATCH_STARTED_UNKNOWN"
COMPLETED = "COMPLETED"
FAILED_BEFORE_PROVIDER_ACCEPTANCE = "FAILED_BEFORE_PROVIDER_ACCEPTANCE"


class PostResearchReopenInitialProductionDispatchError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise PostResearchReopenInitialProductionDispatchError(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(
        isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        f"{field} missing",
    )
    _need(
        observed == canonical_sha256(payload, exclude_fields=(field,)),
        f"{field} self-hash mismatch",
    )
    return observed


def _request_hashes(cost_preflight: Mapping[str, Any]) -> tuple[str, ...]:
    rows = cost_preflight.get("initial_requests")
    _need(isinstance(rows, list) and len(rows) == EXPECTED_CALL_COUNT, "frozen request set missing")
    hashes: list[str] = []
    for index, row in enumerate(rows, start=1):
        _need(isinstance(row, Mapping), f"frozen request {index} malformed")
        request_hash = row.get("request_hash")
        payload = row.get("request_payload")
        _need(isinstance(request_hash, str) and re.fullmatch(r"[0-9a-f]{64}", request_hash) is not None, f"frozen request {index} hash missing")
        _need(isinstance(payload, Mapping), f"frozen request {index} payload missing")
        _need(
            row.get("request_payload_canonical_hash") == canonical_sha256(payload),
            f"frozen request {index} payload hash drift",
        )
        _need(row.get("maximum_output_tokens") == EXPECTED_MAX_OUTPUT_TOKENS, f"frozen request {index} output cap drift")
        hashes.append(request_hash)
    _need(len(set(hashes)) == EXPECTED_CALL_COUNT, "frozen request hashes are not unique")
    return tuple(hashes)


def verify_cost_preflight_for_dispatch(cost_preflight: Mapping[str, Any]) -> tuple[str, ...]:
    observed = cost_v01.verify_initial_request_cost_preflight(
        cost_preflight, expected_code_commit_sha=EXPECTED_PREFLIGHT_CODE_SHA
    )
    _need(observed == EXPECTED_PREFLIGHT_HASH, "Initial cost-preflight hash drift")
    _need(cost_preflight.get("source_verdict_preflight_hash") == EXPECTED_SOURCE_VERDICT_HASH, "source verdict lineage drift")
    _need(cost_preflight.get("source_b3_closure_hash") == EXPECTED_B3_CLOSURE_HASH, "source B3 closure lineage drift")
    _need(cost_preflight.get("model") == EXPECTED_MODEL, "selected model drift")
    _need(cost_preflight.get("reasoning_effort") == EXPECTED_REASONING_EFFORT, "reasoning effort drift")
    _need(cost_preflight.get("call_count_planned") == EXPECTED_CALL_COUNT, "planned call count drift")
    _need(cost_preflight.get("call_count_ceiling") == EXPECTED_CALL_COUNT, "call-count ceiling drift")
    _need(cost_preflight.get("maximum_output_tokens_per_request") == EXPECTED_MAX_OUTPUT_TOKENS, "output-token ceiling drift")
    _need(cost_preflight.get("estimated_max_cost_usd") == EXPECTED_MAX_COST_USD, "cost ceiling drift")
    _need(cost_preflight.get("model_calls_authorized") is False, "cost preflight grants model authority")
    _need(cost_preflight.get("provider_reads_authorized") is False, "cost preflight grants provider authority")
    _need(cost_preflight.get("automatic_retries") == 0, "automatic retries drift")
    return _request_hashes(cost_preflight)


def verify_initial_selected_model_authority(authority: InitialSelectedModelAuthority) -> None:
    selected = authority.selected_candidate
    _need(authority.selection_hash == EXPECTED_INITIAL_SELECTION_HASH, "Initial selected-model hash drift")
    _need(
        selected.candidate_key == "L2"
        and selected.stage.value == "INITIAL"
        and selected.model == EXPECTED_MODEL
        and selected.reasoning_effort == EXPECTED_REASONING_EFFORT
        and selected.ladder_position == 2,
        "Initial selected-model identity drift",
    )


def verify_owner_approval(
    approval: Mapping[str, Any], *, cost_preflight: Mapping[str, Any], dispatch_code_commit_sha: str
) -> str:
    request_hashes = verify_cost_preflight_for_dispatch(cost_preflight)
    observed = _self_hash(approval)
    _need(approval.get("artifact_version") == OWNER_APPROVAL_VERSION, "owner approval version drift")
    _need(approval.get("owner_approval_granted") is True, "owner approval is not explicitly granted")
    _need(approval.get("cost_preflight_artifact_hash") == EXPECTED_PREFLIGHT_HASH, "owner approval preflight hash drift")
    _need(approval.get("model") == EXPECTED_MODEL, "owner approval model drift")
    _need(approval.get("reasoning_effort") == EXPECTED_REASONING_EFFORT, "owner approval effort drift")
    _need(approval.get("planned_call_count") == EXPECTED_CALL_COUNT, "owner approval call count drift")
    _need(approval.get("call_count_ceiling") == EXPECTED_CALL_COUNT, "owner approval call ceiling drift")
    _need(approval.get("max_output_tokens_per_call") == EXPECTED_MAX_OUTPUT_TOKENS, "owner approval output cap drift")
    _need(approval.get("approved_max_estimated_cost_usd") == EXPECTED_MAX_COST_USD, "owner approval cost ceiling drift")
    _need(approval.get("request_set_hash") == cost_preflight.get("request_set_hash"), "owner approval request-set hash drift")
    _need(approval.get("request_hashes") == list(request_hashes), "owner approval request hashes drift")
    _need(approval.get("automatic_retries") == 0, "owner approval permits retries")
    _need(approval.get("approved_dispatch_code_commit_sha") == dispatch_code_commit_sha, "owner approval dispatch code authority drift")
    for field in ("owner_approval_id", "owner_approval_at_utc"):
        _need(isinstance(approval.get(field), str) and bool(approval[field].strip()), f"{field} missing")
    return observed


def build_not_dispatched_ledger(cost_preflight: Mapping[str, Any]) -> list[dict[str, Any]]:
    request_hashes = verify_cost_preflight_for_dispatch(cost_preflight)
    rows = cost_preflight["initial_requests"]
    return [
        {
            "dispatch_index": index,
            "request_hash": request_hash,
            "candidate": rows[index - 1]["candidate"],
            "council_role": rows[index - 1]["council_role"],
            "state": NOT_DISPATCHED,
            "automatic_retry_permitted": False,
        }
        for index, request_hash in enumerate(request_hashes, start=1)
    ]


def assert_ledger_safe_before_dispatch(ledger: Sequence[Mapping[str, Any]]) -> None:
    _need(len(ledger) == EXPECTED_CALL_COUNT, "dispatch ledger call-count drift")
    for index, row in enumerate(ledger, start=1):
        _need(isinstance(row, Mapping), f"dispatch ledger row {index} malformed")
        state = row.get("state")
        _need(
            state in {
                NOT_DISPATCHED,
                DISPATCH_STARTED_UNKNOWN,
                COMPLETED,
                FAILED_BEFORE_PROVIDER_ACCEPTANCE,
            },
            f"dispatch ledger state {index} invalid",
        )
        _need(state == NOT_DISPATCHED, f"dispatch ledger requires fail-closed recovery: {state}")
        _need(row.get("automatic_retry_permitted") is False, "dispatch ledger permits automatic retry")


def assert_exclusive_output(path: Path) -> None:
    _need(not path.exists(), f"production output already exists: {path}")


def verify_pre_dispatch_environment(
    *,
    branch: str,
    code_commit_sha: str,
    git_worktree_clean: bool,
    cost_preflight: Mapping[str, Any],
    authority: InitialSelectedModelAuthority,
    owner_approval: Mapping[str, Any],
    fresh_initial_result_exists: bool,
) -> str:
    """Validate all authority predicates before any future transport is constructed."""

    _need(branch == EXPECTED_BRANCH, "production dispatch branch mismatch")
    _need(git_worktree_clean is True, "production dispatch requires clean worktree")
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha or "") is not None, "production dispatch code SHA invalid")
    _need(not fresh_initial_result_exists, "fresh Initial production result already exists")
    verify_initial_selected_model_authority(authority)
    return verify_owner_approval(
        owner_approval,
        cost_preflight=cost_preflight,
        dispatch_code_commit_sha=code_commit_sha,
    )


def build_zero_call_dispatch_preflight(
    *, code_commit_sha: str, cost_preflight: Mapping[str, Any], authority: InitialSelectedModelAuthority
) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha or "") is not None, "exact code SHA required")
    request_hashes = verify_cost_preflight_for_dispatch(cost_preflight)
    verify_initial_selected_model_authority(authority)
    ledger = build_not_dispatched_ledger(cost_preflight)
    assert_ledger_safe_before_dispatch(ledger)
    artifact: dict[str, Any] = {
        "artifact_version": ZERO_CALL_ARTIFACT_VERSION,
        "status": ZERO_CALL_PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_cost_preflight_hash": EXPECTED_PREFLIGHT_HASH,
        "source_verdict_preflight_hash": EXPECTED_SOURCE_VERDICT_HASH,
        "source_b3_final_closure_hash": EXPECTED_B3_CLOSURE_HASH,
        "initial_selected_model_selection_hash": EXPECTED_INITIAL_SELECTION_HASH,
        "model": EXPECTED_MODEL,
        "reasoning_effort": EXPECTED_REASONING_EFFORT,
        "call_count": EXPECTED_CALL_COUNT,
        "call_count_ceiling": EXPECTED_CALL_COUNT,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "approved_max_estimated_cost_usd_required": EXPECTED_MAX_COST_USD,
        "request_set_hash": cost_preflight["request_set_hash"],
        "request_hashes": list(request_hashes),
        "owner_approval_required": True,
        "owner_approval_status": "NOT_GRANTED",
        "production_dispatch_authorized": False,
        "automatic_retries": 0,
        "dispatch_ledger_initial_state": ledger,
        "partial_dispatch_policy": {
            "durable_state_before_provider_call": DISPATCH_STARTED_UNKNOWN,
            "unknown_outcome_action": "STOP_FAIL_CLOSED_NO_AUTOMATIC_RESEND",
            "automatic_retry_permitted": False,
            "accepted_terminal_states": [COMPLETED, FAILED_BEFORE_PROVIDER_ACCEPTANCE],
        },
        "historical_b4_model_outputs_reusable_as_fresh_outputs": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_calls_this_step": 0,
        "provider_reads_this_step": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd_this_step": "0",
        "live_money": "PROHIBITED",
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    return artifact


def verify_zero_call_dispatch_preflight(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == ZERO_CALL_ARTIFACT_VERSION, "zero-call artifact version drift")
    _need(payload.get("status") == ZERO_CALL_PASS_STATUS, "zero-call artifact status drift")
    _need(payload.get("code_commit_sha") == expected_code_commit_sha, "zero-call artifact code SHA drift")
    expected = {
        "source_cost_preflight_hash": EXPECTED_PREFLIGHT_HASH,
        "source_verdict_preflight_hash": EXPECTED_SOURCE_VERDICT_HASH,
        "source_b3_final_closure_hash": EXPECTED_B3_CLOSURE_HASH,
        "initial_selected_model_selection_hash": EXPECTED_INITIAL_SELECTION_HASH,
        "model": EXPECTED_MODEL,
        "reasoning_effort": EXPECTED_REASONING_EFFORT,
        "call_count": EXPECTED_CALL_COUNT,
        "call_count_ceiling": EXPECTED_CALL_COUNT,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "approved_max_estimated_cost_usd_required": EXPECTED_MAX_COST_USD,
        "owner_approval_required": True,
        "owner_approval_status": "NOT_GRANTED",
        "production_dispatch_authorized": False,
        "automatic_retries": 0,
        "historical_b4_model_outputs_reusable_as_fresh_outputs": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_calls_this_step": 0,
        "provider_reads_this_step": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd_this_step": "0",
        "live_money": "PROHIBITED",
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    for key, value in expected.items():
        _need(payload.get(key) == value, f"zero-call artifact drift: {key}")
    _need(isinstance(payload.get("request_hashes"), list) and len(payload["request_hashes"]) == EXPECTED_CALL_COUNT, "zero-call request hashes drift")
    ledger = payload.get("dispatch_ledger_initial_state")
    _need(isinstance(ledger, list), "zero-call dispatch ledger missing")
    assert_ledger_safe_before_dispatch(ledger)
    return observed


def load_and_build_zero_call_dispatch_preflight(
    *, code_commit_sha: str, cost_preflight_path: str | Path, initial_authority_path: str | Path
) -> dict[str, Any]:
    try:
        cost_preflight = json.loads(Path(cost_preflight_path).read_text(encoding="utf-8"))
        authority_raw = json.loads(Path(initial_authority_path).read_text(encoding="utf-8"))
        authority = InitialSelectedModelAuthority.model_validate(authority_raw)
    except Exception as exc:
        raise PostResearchReopenInitialProductionDispatchError("unable to load immutable dispatch inputs") from exc
    _need(isinstance(cost_preflight, dict), "cost preflight root must be object")
    return build_zero_call_dispatch_preflight(
        code_commit_sha=code_commit_sha, cost_preflight=cost_preflight, authority=authority
    )
