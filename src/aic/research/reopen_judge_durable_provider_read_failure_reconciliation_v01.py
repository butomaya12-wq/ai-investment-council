from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_DURABLE_PROVIDER_READ_FAILURE_RECONCILIATION_v0_1"
PASS_STATUS = "B3_RESEARCH_REOPEN_DURABLE_PROVIDER_READ_FAILURE_RECONCILIATION_ZERO_CALL_PASS"
NEXT_GATE = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_PLAN_ZERO_CALL"

EXPECTED_SOURCE_CODE_SHA = "6150b40a7b45e64939124333b32c8673a8a97702"
EXPECTED_LOCAL_REPLAY_HASH = "74a539f68fda0102918039f96e4a3ec28bfc5468f17fcb24400e9eecaf875c29"
EXPECTED_PREFLIGHT_HASH = "610f12652f856166a0661ff92f135ea9e5ea60d263eb663720c479ee3fe5ff45"
EXPECTED_REQUEST_MANIFEST_HASH = "13578f74c1b34de0bbe33fc59b0e0648dce47a155900f82e04903c0ab7ffe379"
EXPECTED_RUNNER_DRY_HASH = "f30208098f0663e4982c9d68a54fd60e3e585542c6ce69ede9e42ee90fc4258f"
EXPECTED_AUTHORIZATION_HASH = "a5b85346bbf7d3aa15e17f98a6edb38e23b2e8f57d30a1d6fbd939a6b1b5a4aa"
EXPECTED_RESULT_HASH = "45980cba660dff7df1e013808c760a7eae95456e830e734ecd1641021d0cdfc1"
EXPECTED_REOPEN_CUTOFF_UTC = "2026-08-31T08:58:17Z"
EXPECTED_OWNER_APPROVAL_ID = "OWNER-B3-RESEARCH-REOPEN-RESIDUAL-EXTERNAL-READ-V01"
EXPECTED_OWNER_APPROVAL_AT_UTC = "2026-08-31T09:28:13Z"
EXPECTED_MAX_DISPATCH_ATTEMPTS = 9
EXPECTED_OBSERVED_DISPATCH_ATTEMPTS = 2
EXPECTED_OBSERVED_RESPONSE_RECEIPTS = 2
EXPECTED_FIRST_DISPATCH_EVENT_HASH = "59498466d106089c83cb93a7b2beba09244380d17635a11664c1cf95dc29402f"
EXPECTED_LAST_DISPATCH_EVENT_HASH = "03f70dccf2442a587290d52b8aa9f124445c5fdc9e1155b748955eeaa89500d5"
EXPECTED_FAILED_BUNDLE_ID = "ER1_NVDA_NEWS_REFRESH"
EXPECTED_FAILURE_REASON = "NEWS_PAGINATION_NOT_TERMINAL_WITHIN_BOUND"
EXPECTED_PARTIAL_RESPONSE_HASH = "447ea804f62054b7ac87d07a9f929f5f748187e7ebe9cef3170e14e2284a211d"
EXPECTED_NVDA_AGGREGATE_HASH = "8c4f71f320f28be401c7b1fa68403d9724a595ff60bad64131ca38cd1d586348"
EXPECTED_NVDA_PAGE_HASHES = (
    "14c56923934b692f4dab3006a16ccc655573ef8b2aedcb6f752ffbc27ea133e8",
    "d14554b4a5660991bbb407f5c2fc07dff9acac3c32789914a143456523a3cb84",
)
EXPECTED_NVDA_TERMINAL_NEXT_PAGE_TOKEN = "MTc4ODAwODQzNDAwMDAwMDAwMHw2MTUxMDk3Mw=="
EXPECTED_NVDA_ARTICLE_COUNT = 10
EXPECTED_NVDA_PAGE_COUNT = 2
EXPECTED_NVDA_PAGE_SIZE = 5
EXPECTED_NVDA_MAX_PAGES = 2
EXPECTED_NVDA_WINDOW_START = "2026-08-28T17:34:00Z"
EXPECTED_NVDA_WINDOW_END = EXPECTED_REOPEN_CUTOFF_UTC

RESIDUAL_TARGET_IDS = (
    "NVDA_CURRENT_DEVELOPMENTS_Q4",
    "MSFT_VALUATION_CONTEXT_DEPTH",
    "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
    "META_CONDITION_001",
    "META_CONDITION_002",
    "META_CONDITION_003",
    "META_CONDITION_004",
)
UNDISPATCHED_BUNDLE_IDS = (
    "ER2_MSFT_NEWS_REFRESH",
    "ER3_META_NEWS_REFRESH",
    "ER4_CURRENT_PAPER_POSITIONS",
    "ER5_CURRENT_PORTFOLIO_EQUITY",
    "ER6_DYNAMIC_MARKET_CONTEXT",
)


class DurableProviderReadFailureReconciliationError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise DurableProviderReadFailureReconciliationError(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(
        isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        f"{field} missing",
    )
    expected = canonical_sha256(payload, exclude_fields=(field,))
    _need(observed == expected, f"{field} self-hash mismatch")
    return observed


def verify_local_replay(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_LOCAL_REPLAY_HASH, "local replay hash drift")
    _need(payload.get("status") == "B3_RESEARCH_REOPEN_LOCAL_REPLAY_ZERO_CALL_PASS", "local replay status drift")
    _need(payload.get("residual_external_read_target_count") == len(RESIDUAL_TARGET_IDS), "residual target count drift")
    _need(tuple(payload.get("residual_external_read_target_ids", ())) == RESIDUAL_TARGET_IDS, "residual target ids drift")
    _need(payload.get("provider_reads_authorized") is False, "local replay unexpectedly authorizes reads")
    _need(payload.get("model_calls_authorized") is False, "local replay unexpectedly authorizes model calls")
    return observed


def verify_authorization(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_AUTHORIZATION_HASH, "authorization hash drift")
    _need(payload.get("status") == "AUTHORIZED_EXACTLY_ONE_BOUNDED_PROVIDER_READ_PASS", "authorization status drift")
    _need(payload.get("code_commit_sha") == EXPECTED_SOURCE_CODE_SHA, "authorization code SHA drift")
    _need(payload.get("source_runner_dry_hash") == EXPECTED_RUNNER_DRY_HASH, "authorization dry lineage drift")
    _need(payload.get("source_preflight_hash") == EXPECTED_PREFLIGHT_HASH, "authorization preflight lineage drift")
    _need(payload.get("request_manifest_hash") == EXPECTED_REQUEST_MANIFEST_HASH, "authorization manifest drift")
    _need(payload.get("reopen_cutoff_utc") == EXPECTED_REOPEN_CUTOFF_UTC, "authorization cutoff drift")
    _need(payload.get("provider_dispatch_attempts_max") == EXPECTED_MAX_DISPATCH_ATTEMPTS, "authorization dispatch ceiling drift")
    _need(payload.get("owner_approval_id") == EXPECTED_OWNER_APPROVAL_ID, "authorization owner id drift")
    _need(payload.get("owner_approval_at_utc") == EXPECTED_OWNER_APPROVAL_AT_UTC, "authorization owner timestamp drift")
    _need(payload.get("authority_consumption_rule") == "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT", "authorization consumption rule drift")
    _need(payload.get("single_authorized_read_pass_only") is True, "authorization single-pass rule drift")
    _need(payload.get("automatic_retries") == 0, "authorization retry policy drift")
    _need(payload.get("conditional_followup_reads_authorized") is False, "authorization followup policy drift")
    _need(payload.get("model_calls_authorized") is False, "authorization model authority drift")
    _need(payload.get("model_synthesis_authorized") is False, "authorization synthesis authority drift")
    _need(payload.get("broker_writes_authorized") is False, "authorization broker authority drift")
    _need(payload.get("alpaca_orders_authorized") is False, "authorization order authority drift")
    _need(payload.get("live_money") == "PROHIBITED", "authorization live-money policy drift")
    return observed


def verify_journal(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    _need(len(rows) == 4, "journal must contain exactly four durable events")
    for row in rows:
        _self_hash(row, field="event_hash")
        _need(row.get("authorization_artifact_hash") == EXPECTED_AUTHORIZATION_HASH, "journal authorization lineage drift")
        _need(row.get("bundle_id") == EXPECTED_FAILED_BUNDLE_ID, "journal contains unexpected bundle")

    expected_types = (
        "PROVIDER_DISPATCH_ATTEMPT",
        "PROVIDER_RESPONSE_RECEIPT",
        "PROVIDER_DISPATCH_ATTEMPT",
        "PROVIDER_RESPONSE_RECEIPT",
    )
    _need(tuple(row.get("event_type") for row in rows) == expected_types, "journal event sequence drift")

    attempts = [row for row in rows if row.get("event_type") == "PROVIDER_DISPATCH_ATTEMPT"]
    receipts = [row for row in rows if row.get("event_type") == "PROVIDER_RESPONSE_RECEIPT"]
    _need(len(attempts) == EXPECTED_OBSERVED_DISPATCH_ATTEMPTS, "dispatch attempt count drift")
    _need(len(receipts) == EXPECTED_OBSERVED_RESPONSE_RECEIPTS, "response receipt count drift")
    _need([row.get("global_dispatch_index") for row in attempts] == [1, 2], "global dispatch indexes drift")
    _need([row.get("dispatch_index_within_bundle") for row in attempts] == [1, 2], "bundle dispatch indexes drift")
    _need([row.get("dispatch_index_within_bundle") for row in receipts] == [1, 2], "receipt dispatch indexes drift")
    _need(attempts[0].get("event_hash") == EXPECTED_FIRST_DISPATCH_EVENT_HASH, "first dispatch event hash drift")
    _need(attempts[-1].get("event_hash") == EXPECTED_LAST_DISPATCH_EVENT_HASH, "last dispatch event hash drift")

    return {
        "journal_event_count": len(rows),
        "provider_dispatch_attempt_count": len(attempts),
        "provider_response_receipt_count": len(receipts),
        "first_dispatch_event_hash": attempts[0]["event_hash"],
        "last_dispatch_event_hash": attempts[-1]["event_hash"],
    }


def verify_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_RESULT_HASH, "result hash drift")
    _need(payload.get("artifact_version") == "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_RESULT_v0_1", "result version drift")
    _need(payload.get("status") == "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_BLOCKED", "result status drift")
    _need(payload.get("authorization_artifact_hash") == EXPECTED_AUTHORIZATION_HASH, "result authorization lineage drift")
    _need(payload.get("source_preflight_hash") == EXPECTED_PREFLIGHT_HASH, "result preflight lineage drift")
    _need(payload.get("request_manifest_hash") == EXPECTED_REQUEST_MANIFEST_HASH, "result manifest drift")
    _need(payload.get("reopen_cutoff_utc") == EXPECTED_REOPEN_CUTOFF_UTC, "result cutoff drift")
    _need(payload.get("provider_dispatch_attempts") == EXPECTED_OBSERVED_DISPATCH_ATTEMPTS, "result dispatch count drift")
    _need(payload.get("provider_dispatch_attempts_max") == EXPECTED_MAX_DISPATCH_ATTEMPTS, "result dispatch ceiling drift")
    _need(payload.get("failed_bundle_id") == EXPECTED_FAILED_BUNDLE_ID, "failed bundle drift")
    _need(payload.get("failure_reason") == EXPECTED_FAILURE_REASON, "failure reason drift")
    _need(payload.get("automatic_retries") == 0, "result retry drift")
    _need(payload.get("model_calls") == 0, "result model call drift")
    _need(payload.get("model_synthesis_calls") == 0, "result synthesis call drift")
    _need(payload.get("broker_writes") == 0, "result broker write drift")
    _need(payload.get("alpaca_orders") == 0, "result order drift")
    _need(payload.get("live_money") == "PROHIBITED", "result live-money drift")
    _need(payload.get("final_decision_created") is False, "result FinalDecision drift")
    _need(payload.get("b5_handoff_created") is False, "result B5 drift")
    _need(payload.get("next_gate") == "ZERO_CALL_DURABLE_PROVIDER_READ_FAILURE_RECONCILIATION", "result next gate drift")

    bundle_results = payload.get("bundle_results")
    _need(isinstance(bundle_results, list) and len(bundle_results) == 1, "exactly one partial bundle result required")
    bundle = bundle_results[0]
    _need(isinstance(bundle, Mapping), "partial bundle result malformed")
    _need(bundle.get("bundle_id") == EXPECTED_FAILED_BUNDLE_ID, "partial bundle id drift")
    _need(bundle.get("status") == "PARTIAL_STOP", "partial bundle status drift")
    _need(bundle.get("provider_dispatch_attempts") == EXPECTED_OBSERVED_DISPATCH_ATTEMPTS, "partial bundle dispatch count drift")
    _need(bundle.get("response_artifact_hash") == EXPECTED_PARTIAL_RESPONSE_HASH, "partial response artifact hash drift")

    response = bundle.get("response_artifact")
    _need(isinstance(response, Mapping), "partial response artifact missing")
    _need(canonical_sha256(response) == EXPECTED_PARTIAL_RESPONSE_HASH, "partial response canonical hash mismatch")
    _need(response.get("aggregate_payload_hash") == EXPECTED_NVDA_AGGREGATE_HASH, "NVDA aggregate hash drift")
    _need(canonical_sha256(response, exclude_fields=("aggregate_payload_hash",)) == EXPECTED_NVDA_AGGREGATE_HASH, "NVDA aggregate self-hash mismatch")
    _need(response.get("symbol") == "NVDA", "NVDA partial symbol drift")
    _need(response.get("window_start") == EXPECTED_NVDA_WINDOW_START, "NVDA window start drift")
    _need(response.get("window_end") == EXPECTED_NVDA_WINDOW_END, "NVDA window end drift")
    _need(response.get("page_size") == EXPECTED_NVDA_PAGE_SIZE, "NVDA page size drift")
    _need(response.get("page_count") == EXPECTED_NVDA_PAGE_COUNT, "NVDA page count drift")
    _need(response.get("max_pages") == EXPECTED_NVDA_MAX_PAGES, "NVDA max pages drift")
    _need(response.get("pagination_complete") is False, "NVDA pagination unexpectedly complete")
    _need(response.get("terminal_next_page_token") == EXPECTED_NVDA_TERMINAL_NEXT_PAGE_TOKEN, "NVDA terminal next page token drift")
    _need(tuple(response.get("page_raw_payload_hashes", ())) == EXPECTED_NVDA_PAGE_HASHES, "NVDA page hashes drift")
    articles = response.get("articles")
    _need(isinstance(articles, list) and len(articles) == EXPECTED_NVDA_ARTICLE_COUNT, "NVDA article count drift")
    _need(len({article.get("article_id") for article in articles if isinstance(article, Mapping)}) == EXPECTED_NVDA_ARTICLE_COUNT, "NVDA article ids are not unique")

    return {
        "result_artifact_hash": observed,
        "partial_response_artifact_hash": EXPECTED_PARTIAL_RESPONSE_HASH,
        "nvda_aggregate_payload_hash": EXPECTED_NVDA_AGGREGATE_HASH,
        "nvda_page_raw_payload_hashes": list(EXPECTED_NVDA_PAGE_HASHES),
        "nvda_terminal_next_page_token": EXPECTED_NVDA_TERMINAL_NEXT_PAGE_TOKEN,
        "nvda_retained_article_count": EXPECTED_NVDA_ARTICLE_COUNT,
    }


def build_reconciliation(
    *,
    local_replay: Mapping[str, Any],
    authorization: Mapping[str, Any],
    journal_rows: Sequence[Mapping[str, Any]],
    result: Mapping[str, Any],
    code_commit_sha: str,
) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha or "") is not None, "exact reconciliation code SHA required")
    local_replay_hash = verify_local_replay(local_replay)
    authorization_hash = verify_authorization(authorization)
    journal_summary = verify_journal(journal_rows)
    result_summary = verify_result(result)

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_runtime_code_commit_sha": EXPECTED_SOURCE_CODE_SHA,
        "source_local_replay_hash": local_replay_hash,
        "source_preflight_hash": EXPECTED_PREFLIGHT_HASH,
        "source_request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "source_runner_dry_hash": EXPECTED_RUNNER_DRY_HASH,
        "source_authorization_artifact_hash": authorization_hash,
        "source_result_artifact_hash": result_summary["result_artifact_hash"],
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "failure_class": "BOUNDED_NEWS_PAGINATION_NOT_TERMINAL",
        "failed_bundle_id": EXPECTED_FAILED_BUNDLE_ID,
        "failure_reason": EXPECTED_FAILURE_REASON,
        "authority_consumed": True,
        "authority_consumed_on_first_dispatch": True,
        "original_authority_reusable": False,
        "original_production_read_pass_rerun_allowed": False,
        "provider_dispatch_attempts_observed": journal_summary["provider_dispatch_attempt_count"],
        "provider_response_receipts_observed": journal_summary["provider_response_receipt_count"],
        "provider_dispatch_attempts_original_ceiling": EXPECTED_MAX_DISPATCH_ATTEMPTS,
        "first_dispatch_event_hash": journal_summary["first_dispatch_event_hash"],
        "last_dispatch_event_hash": journal_summary["last_dispatch_event_hash"],
        "partial_provider_bundle_count": 1,
        "completed_provider_bundle_count": 0,
        "undispatched_provider_bundle_count": len(UNDISPATCHED_BUNDLE_IDS),
        "undispatched_provider_bundle_ids": list(UNDISPATCHED_BUNDLE_IDS),
        "retained_partial_evidence_bundle_id": EXPECTED_FAILED_BUNDLE_ID,
        "retained_partial_evidence_hash": result_summary["partial_response_artifact_hash"],
        "retained_partial_evidence_usable": True,
        "retained_partial_evidence_complete": False,
        "nvda_retained_article_count": result_summary["nvda_retained_article_count"],
        "nvda_retained_page_count": EXPECTED_NVDA_PAGE_COUNT,
        "nvda_retained_page_raw_payload_hashes": result_summary["nvda_page_raw_payload_hashes"],
        "nvda_aggregate_payload_hash": result_summary["nvda_aggregate_payload_hash"],
        "nvda_terminal_next_page_token": result_summary["nvda_terminal_next_page_token"],
        "nvda_continuation_must_start_from_terminal_token": True,
        "nvda_replay_of_retained_pages_allowed": False,
        "nvda_original_total_page_engineering_bound": 6,
        "nvda_max_additional_pages_without_expanding_original_total_bound": 4,
        "residual_external_read_target_count": len(RESIDUAL_TARGET_IDS),
        "residual_external_read_target_ids": list(RESIDUAL_TARGET_IDS),
        "resolved_target_count_this_step": 0,
        "provider_reads_this_step": 0,
        "provider_reads_authorized_this_step": False,
        "model_calls_this_step": 0,
        "model_calls_authorized_this_step": False,
        "model_synthesis_calls_this_step": 0,
        "automatic_retries_this_step": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
