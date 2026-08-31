from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_residual_external_read_continuation_plan_v01 as plan_v01


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_PREFLIGHT_v0_1"
PASS_STATUS = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_PREFLIGHT_ZERO_CALL_PASS"
NEXT_GATE = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_RUNNER_DRY_ZERO_CALL"

EXPECTED_PLAN_HASH = "af4e752cc243da09e6f98cf1b7dcf1b18efc171578afdf7b0f111d5dc4d43fef"
EXPECTED_PLAN_CODE_SHA = "3ec45e69bce34f7622f39bac5b665e0ce80a8ac7"
EXPECTED_ORIGINAL_PREFLIGHT_HASH = "610f12652f856166a0661ff92f135ea9e5ea60d263eb663720c479ee3fe5ff45"
EXPECTED_ORIGINAL_REQUEST_MANIFEST_HASH = "13578f74c1b34de0bbe33fc59b0e0648dce47a155900f82e04903c0ab7ffe379"
EXPECTED_REOPEN_CUTOFF_UTC = plan_v01.EXPECTED_REOPEN_CUTOFF_UTC

REUSED_TEMPLATE_HASHES = plan_v01.ORIGINAL_UNDISPATCHED_TEMPLATE_HASHES
BUNDLE_IDS = plan_v01.BUNDLE_IDS
TARGET_IDS = plan_v01.RESIDUAL_TARGET_IDS
PROVIDER_DISPATCH_ATTEMPTS_MAX = plan_v01.PROVIDER_DISPATCH_ATTEMPTS_MAX
NEWS_DISPATCH_ATTEMPTS_MAX = plan_v01.NEWS_DISPATCH_ATTEMPTS_MAX
NON_NEWS_DISPATCH_ATTEMPTS_MAX = plan_v01.NON_NEWS_DISPATCH_ATTEMPTS_MAX


class ResidualExternalReadContinuationPreflightError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ResidualExternalReadContinuationPreflightError(message)


def _self_hash(payload: Mapping[str, Any], *, label: str) -> str:
    observed = payload.get("artifact_hash")
    _need(
        isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        f"{label} artifact_hash missing",
    )
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    _need(observed == expected, f"{label} self-hash mismatch")
    return observed


def verify_plan(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload, label="continuation plan")
    _need(observed == EXPECTED_PLAN_HASH, "continuation plan hash drift")
    plan_v01.verify_plan(payload, expected_code_commit_sha=EXPECTED_PLAN_CODE_SHA)
    _need(payload.get("reopen_cutoff_utc") == EXPECTED_REOPEN_CUTOFF_UTC, "continuation cutoff drift")
    _need(payload.get("provider_dispatch_attempts_max") == PROVIDER_DISPATCH_ATTEMPTS_MAX, "continuation ceiling drift")
    _need(payload.get("provider_reads_authorized") is False, "continuation plan already authorizes reads")
    _need(payload.get("model_calls_authorized") is False, "continuation plan unexpectedly authorizes model calls")
    return observed


def verify_original_preflight(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload, label="original preflight")
    _need(observed == EXPECTED_ORIGINAL_PREFLIGHT_HASH, "original preflight hash drift")
    _need(payload.get("request_manifest_hash") == EXPECTED_ORIGINAL_REQUEST_MANIFEST_HASH, "original manifest drift")
    _need(payload.get("reopen_cutoff_utc") == EXPECTED_REOPEN_CUTOFF_UTC, "original cutoff drift")
    _need(payload.get("provider_reads_authorized") is False, "original preflight unexpectedly authorizes reads")
    _need(payload.get("owner_provider_read_approval_present") is False, "original preflight should predate approval")

    rows = payload.get("request_preflights")
    _need(isinstance(rows, list) and len(rows) == 6, "original request-preflight shape drift")
    by_bundle = {
        row.get("bundle_id"): row
        for row in rows
        if isinstance(row, Mapping)
    }
    mapping = {
        "CR1_MSFT_NEWS_REFRESH": "ER2_MSFT_NEWS_REFRESH",
        "CR2_META_NEWS_REFRESH": "ER3_META_NEWS_REFRESH",
        "CR3_CURRENT_PAPER_POSITIONS": "ER4_CURRENT_PAPER_POSITIONS",
        "CR4_CURRENT_PORTFOLIO_EQUITY": "ER5_CURRENT_PORTFOLIO_EQUITY",
        "CR5_DYNAMIC_MARKET_CONTEXT": "ER6_DYNAMIC_MARKET_CONTEXT",
    }
    for continuation_id, original_id in mapping.items():
        row = by_bundle.get(original_id)
        _need(isinstance(row, Mapping), f"original template missing: {original_id}")
        _need(
            row.get("request_template_hash") == REUSED_TEMPLATE_HASHES[continuation_id],
            f"original template hash drift: {original_id}",
        )
        _need(row.get("provider_read_authorized") is False, f"original template read authority drift: {original_id}")
        _need(row.get("model_call_authorized") is False, f"original template model authority drift: {original_id}")
    return observed


def _nvda_request_preflight(plan: Mapping[str, Any]) -> dict[str, Any]:
    bundles = plan.get("provider_read_bundles")
    _need(isinstance(bundles, list), "continuation bundles missing")
    rows = [row for row in bundles if isinstance(row, Mapping) and row.get("bundle_id") == "CR6_NVDA_NEWS_CONTINUATION"]
    _need(len(rows) == 1, "exactly one NVDA continuation bundle required")
    bundle = rows[0]
    contract = bundle.get("request_contract")
    _need(isinstance(contract, Mapping), "NVDA continuation contract missing")
    _need(contract.get("start_page_token") == plan_v01.EXPECTED_NVDA_TERMINAL_TOKEN, "NVDA start token drift")
    _need(contract.get("start_page_token_required") is True, "NVDA start token not required")
    _need(contract.get("replay_retained_pages") is False, "NVDA retained replay unexpectedly allowed")
    _need(contract.get("max_additional_pages") == plan_v01.NVDA_MAX_ADDITIONAL_PAGES, "NVDA continuation page ceiling drift")
    _need(contract.get("window_end_utc") == EXPECTED_REOPEN_CUTOFF_UTC, "NVDA continuation cutoff drift")

    row: dict[str, Any] = {
        "bundle_id": "CR6_NVDA_NEWS_CONTINUATION",
        "execution_order": 6,
        "provider": bundle.get("provider"),
        "auth_mode": bundle.get("auth_mode"),
        "existing_transport": bundle.get("existing_transport"),
        "required_new_primitive": bundle.get("required_new_primitive"),
        "target_ids": list(bundle.get("target_ids", [])),
        "max_dispatch_attempts": int(bundle.get("max_dispatch_attempts", 0)),
        "resolved_request_contract": dict(contract),
        "bounded_pagination_incomplete_policy": bundle.get("bounded_pagination_incomplete_policy"),
        "transport_or_validation_error_policy": bundle.get("transport_or_validation_error_policy"),
        "provider_read_authorized": False,
        "model_call_authorized": False,
        "automatic_retry_authorized": False,
    }
    row["request_template_hash"] = canonical_sha256(row)
    return row


def build_preflight(
    *,
    plan: Mapping[str, Any],
    original_preflight: Mapping[str, Any],
    code_commit_sha: str,
) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha or "") is not None, "exact continuation-preflight code SHA required")
    plan_hash = verify_plan(plan)
    original_preflight_hash = verify_original_preflight(original_preflight)

    nvda = _nvda_request_preflight(plan)
    reused = [
        {
            "bundle_id": bundle_id,
            "execution_order": index,
            "request_template_source": "ORIGINAL_FROZEN_UNDISPATCHED_PREFLIGHT",
            "source_original_bundle_id": original_id,
            "request_template_hash": REUSED_TEMPLATE_HASHES[bundle_id],
            "provider_read_authorized": False,
            "model_call_authorized": False,
            "automatic_retry_authorized": False,
        }
        for index, (bundle_id, original_id) in enumerate(
            (
                ("CR1_MSFT_NEWS_REFRESH", "ER2_MSFT_NEWS_REFRESH"),
                ("CR2_META_NEWS_REFRESH", "ER3_META_NEWS_REFRESH"),
                ("CR3_CURRENT_PAPER_POSITIONS", "ER4_CURRENT_PAPER_POSITIONS"),
                ("CR4_CURRENT_PORTFOLIO_EQUITY", "ER5_CURRENT_PORTFOLIO_EQUITY"),
                ("CR5_DYNAMIC_MARKET_CONTEXT", "ER6_DYNAMIC_MARKET_CONTEXT"),
            ),
            start=1,
        )
    ]
    requests = reused + [nvda]
    template_hashes = [row["request_template_hash"] for row in requests]
    _need(len(template_hashes) == 6, "continuation request-template count drift")
    _need(sum([2, 2, 1, 1, 1, int(nvda["max_dispatch_attempts"])]) == PROVIDER_DISPATCH_ATTEMPTS_MAX, "continuation ceiling arithmetic drift")

    manifest_payload = {
        "source_original_preflight_hash": original_preflight_hash,
        "source_original_request_manifest_hash": EXPECTED_ORIGINAL_REQUEST_MANIFEST_HASH,
        "source_continuation_plan_hash": plan_hash,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "logical_provider_read_bundle_ids": list(BUNDLE_IDS),
        "request_template_hashes": template_hashes,
        "provider_dispatch_attempts_max": PROVIDER_DISPATCH_ATTEMPTS_MAX,
    }
    continuation_manifest_hash = canonical_sha256(manifest_payload)

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_continuation_plan_hash": plan_hash,
        "source_continuation_plan_code_sha": EXPECTED_PLAN_CODE_SHA,
        "source_reconciliation_v02_hash": plan_v01.EXPECTED_RECONCILIATION_HASH,
        "source_original_preflight_hash": original_preflight_hash,
        "source_original_request_manifest_hash": EXPECTED_ORIGINAL_REQUEST_MANIFEST_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "reopen_cutoff_reused_from_original_preflight": True,
        "residual_external_read_target_count": len(TARGET_IDS),
        "residual_external_read_target_ids": list(TARGET_IDS),
        "logical_provider_read_bundle_count": len(BUNDLE_IDS),
        "logical_provider_read_bundle_ids": list(BUNDLE_IDS),
        "provider_dispatch_attempts_max": PROVIDER_DISPATCH_ATTEMPTS_MAX,
        "provider_response_reads_max": PROVIDER_DISPATCH_ATTEMPTS_MAX,
        "news_dispatch_attempts_max": NEWS_DISPATCH_ATTEMPTS_MAX,
        "non_news_dispatch_attempts_max": NON_NEWS_DISPATCH_ATTEMPTS_MAX,
        "request_preflights": requests,
        "request_template_hashes": template_hashes,
        "continuation_request_manifest_hash": continuation_manifest_hash,
        "reused_original_template_count": 5,
        "new_continuation_template_count": 1,
        "new_nvda_continuation_request_template_hash": nvda["request_template_hash"],
        "nvda_start_page_token_hash": canonical_sha256({"page_token": plan_v01.EXPECTED_NVDA_TERMINAL_TOKEN}),
        "nvda_replay_retained_pages_allowed": False,
        "pagination_incomplete_continue_policy": "RETAIN_PARTIAL_AND_CONTINUE_TO_LATER_BUNDLES",
        "transport_or_validation_error_policy": "STOP_IMMEDIATELY",
        "single_authorized_continuation_read_pass_only": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "conditional_followup_reads_authorized": False,
        "owner_approval_required_before_provider_read": True,
        "owner_provider_read_approval_present": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "broad_b3_rerun_authorized": False,
        "judge_rerun_authorized": False,
        "rebuttal_rerun_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "execution_authority": False,
        "provider_reads": 0,
        "model_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd": "0",
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_preflight(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload, label="continuation preflight")
    exact = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": expected_code_commit_sha,
        "source_continuation_plan_hash": EXPECTED_PLAN_HASH,
        "source_continuation_plan_code_sha": EXPECTED_PLAN_CODE_SHA,
        "source_reconciliation_v02_hash": plan_v01.EXPECTED_RECONCILIATION_HASH,
        "source_original_preflight_hash": EXPECTED_ORIGINAL_PREFLIGHT_HASH,
        "source_original_request_manifest_hash": EXPECTED_ORIGINAL_REQUEST_MANIFEST_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "reopen_cutoff_reused_from_original_preflight": True,
        "residual_external_read_target_count": len(TARGET_IDS),
        "residual_external_read_target_ids": list(TARGET_IDS),
        "logical_provider_read_bundle_count": len(BUNDLE_IDS),
        "logical_provider_read_bundle_ids": list(BUNDLE_IDS),
        "provider_dispatch_attempts_max": PROVIDER_DISPATCH_ATTEMPTS_MAX,
        "news_dispatch_attempts_max": NEWS_DISPATCH_ATTEMPTS_MAX,
        "non_news_dispatch_attempts_max": NON_NEWS_DISPATCH_ATTEMPTS_MAX,
        "reused_original_template_count": 5,
        "new_continuation_template_count": 1,
        "nvda_replay_retained_pages_allowed": False,
        "pagination_incomplete_continue_policy": "RETAIN_PARTIAL_AND_CONTINUE_TO_LATER_BUNDLES",
        "transport_or_validation_error_policy": "STOP_IMMEDIATELY",
        "single_authorized_continuation_read_pass_only": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "conditional_followup_reads_authorized": False,
        "owner_approval_required_before_provider_read": True,
        "owner_provider_read_approval_present": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd": "0",
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
    }
    for key, expected in exact.items():
        _need(payload.get(key) == expected, f"continuation preflight drift: {key}")

    hashes = payload.get("request_template_hashes")
    _need(isinstance(hashes, list) and len(hashes) == 6, "continuation request-template hashes malformed")
    _need(hashes[:5] == list(REUSED_TEMPLATE_HASHES.values()), "reused template hash order drift")
    _need(hashes[5] == payload.get("new_nvda_continuation_request_template_hash"), "NVDA continuation template hash drift")
    _need(re.fullmatch(r"[0-9a-f]{64}", str(payload.get("continuation_request_manifest_hash"))) is not None, "continuation manifest hash missing")
    return observed
