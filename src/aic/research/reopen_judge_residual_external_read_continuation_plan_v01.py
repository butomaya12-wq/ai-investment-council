from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_PLAN_v0_1"
PASS_STATUS = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_PLAN_ZERO_CALL_PASS"
NEXT_GATE = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_CONTINUATION_PREFLIGHT_ZERO_CALL"

EXPECTED_RECONCILIATION_HASH = "8dbe953083b7dc1ce859d4e6108a7405776fcd3f91d62aca148db7a961955f22"
EXPECTED_RECONCILIATION_CODE_SHA = "439ac0a397a7599a98cb94e8c9a1ba6ca73d5997"
EXPECTED_REOPEN_CUTOFF_UTC = "2026-08-31T08:58:17Z"
EXPECTED_NVDA_TERMINAL_TOKEN = "MTc4ODAwODQzNDAwMDAwMDAwMHw2MTUxMDk3Mw=="
EXPECTED_NVDA_RETAINED_HASH = "447ea804f62054b7ac87d07a9f929f5f748187e7ebe9cef3170e14e2284a211d"
EXPECTED_NVDA_AGGREGATE_HASH = "8c4f71f320f28be401c7b1fa68403d9724a595ff60bad64131ca38cd1d586348"
EXPECTED_NVDA_PAGE_HASHES = (
    "14c56923934b692f4dab3006a16ccc655573ef8b2aedcb6f752ffbc27ea133e8",
    "d14554b4a5660991bbb407f5c2fc07dff9acac3c32789914a143456523a3cb84",
)

# Exact undispatched request templates from the original frozen preflight.
ORIGINAL_UNDISPATCHED_TEMPLATE_HASHES = {
    "CR1_MSFT_NEWS_REFRESH": "43441e0f5c53299766c05cbd263e9548d0b76c98e947473716d8bc1b53cd094b",
    "CR2_META_NEWS_REFRESH": "c4760775cdf918ce9c59b2f07f12650cdf225e9f1a24855c22414e9ef2986c6e",
    "CR3_CURRENT_PAPER_POSITIONS": "195311f037e87aa18e8aefbf79acba9d51c3a45dc32fea64df763621e34f19cf",
    "CR4_CURRENT_PORTFOLIO_EQUITY": "0df40676efd6238e7abe615668d79cb60b88b83b4dee720896dbf0c377900a5a",
    "CR5_DYNAMIC_MARKET_CONTEXT": "ec92a3f22b0a3d91968ee55cc632115da6b162cf49631745cd01e14a7c605e44",
}

RESIDUAL_TARGET_IDS = (
    "NVDA_CURRENT_DEVELOPMENTS_Q4",
    "MSFT_VALUATION_CONTEXT_DEPTH",
    "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
    "META_CONDITION_001",
    "META_CONDITION_002",
    "META_CONDITION_003",
    "META_CONDITION_004",
)

BUNDLE_IDS = (
    "CR1_MSFT_NEWS_REFRESH",
    "CR2_META_NEWS_REFRESH",
    "CR3_CURRENT_PAPER_POSITIONS",
    "CR4_CURRENT_PORTFOLIO_EQUITY",
    "CR5_DYNAMIC_MARKET_CONTEXT",
    "CR6_NVDA_NEWS_CONTINUATION",
)

NEWS_PAGE_SIZE = 5
MSFT_META_MAX_PAGES = 2
NVDA_RETAINED_PAGES = 2
NVDA_MAX_ADDITIONAL_PAGES = 4
NVDA_TOTAL_PAGE_ENGINEERING_BOUND = 6
NEWS_DISPATCH_ATTEMPTS_MAX = 2 + 2 + 4
NON_NEWS_DISPATCH_ATTEMPTS_MAX = 3
PROVIDER_DISPATCH_ATTEMPTS_MAX = NEWS_DISPATCH_ATTEMPTS_MAX + NON_NEWS_DISPATCH_ATTEMPTS_MAX


class ResidualExternalReadContinuationPlanError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ResidualExternalReadContinuationPlanError(message)


def _self_hash(payload: Mapping[str, Any]) -> str:
    observed = payload.get("artifact_hash")
    _need(
        isinstance(observed, str)
        and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        "reconciliation artifact_hash missing",
    )
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    _need(observed == expected, "reconciliation self-hash mismatch")
    return observed


def verify_reconciliation(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_RECONCILIATION_HASH, "reconciliation hash drift")
    _need(
        payload.get("status")
        == "B3_RESEARCH_REOPEN_DURABLE_PROVIDER_READ_FAILURE_RECONCILIATION_V02_ZERO_CALL_PASS",
        "reconciliation status drift",
    )
    _need(
        payload.get("code_commit_sha") == EXPECTED_RECONCILIATION_CODE_SHA,
        "reconciliation code SHA drift",
    )
    _need(payload.get("authority_consumed") is True, "old authority not consumed")
    _need(payload.get("original_authority_reusable") is False, "old authority unexpectedly reusable")
    _need(
        payload.get("original_production_read_pass_rerun_allowed") is False,
        "old production pass unexpectedly rerunnable",
    )
    _need(payload.get("provider_dispatch_attempts_observed") == 2, "observed dispatch count drift")
    _need(payload.get("provider_response_receipts_observed") == 2, "observed receipt count drift")
    _need(payload.get("retained_partial_evidence_hash") == EXPECTED_NVDA_RETAINED_HASH, "NVDA retained hash drift")
    _need(payload.get("nvda_aggregate_payload_hash") == EXPECTED_NVDA_AGGREGATE_HASH, "NVDA aggregate hash drift")
    _need(tuple(payload.get("nvda_retained_page_raw_payload_hashes", ())) == EXPECTED_NVDA_PAGE_HASHES, "NVDA retained page hashes drift")
    _need(payload.get("nvda_terminal_next_page_token") == EXPECTED_NVDA_TERMINAL_TOKEN, "NVDA terminal token drift")
    _need(payload.get("nvda_retained_page_count") == NVDA_RETAINED_PAGES, "NVDA retained page count drift")
    _need(payload.get("nvda_retained_article_count") == 10, "NVDA retained article count drift")
    _need(payload.get("nvda_continuation_must_start_from_terminal_token") is True, "NVDA continuation rule drift")
    _need(payload.get("nvda_replay_of_retained_pages_allowed") is False, "NVDA replay unexpectedly allowed")
    _need(payload.get("nvda_max_additional_pages_without_expanding_original_total_bound") == NVDA_MAX_ADDITIONAL_PAGES, "NVDA additional page bound drift")
    _need(payload.get("residual_external_read_target_count") == len(RESIDUAL_TARGET_IDS), "residual target count drift")
    _need(tuple(payload.get("residual_external_read_target_ids", ())) == RESIDUAL_TARGET_IDS, "residual target identity drift")
    _need(payload.get("provider_reads_this_step") == 0, "reconciliation unexpectedly read provider")
    _need(payload.get("model_calls_this_step") == 0, "reconciliation unexpectedly called model")
    return observed


def _reused_bundle(*, bundle_id: str, original_bundle_id: str, order: int, max_dispatch_attempts: int, target_ids: list[str], pagination_partial_allowed: bool = False) -> dict[str, Any]:
    return {
        "bundle_id": bundle_id,
        "original_bundle_id": original_bundle_id,
        "execution_order": order,
        "request_template_reuse": "EXACT_ORIGINAL_UNDISPATCHED_TEMPLATE",
        "source_request_template_hash": ORIGINAL_UNDISPATCHED_TEMPLATE_HASHES[bundle_id],
        "target_ids": target_ids,
        "max_dispatch_attempts": max_dispatch_attempts,
        "provider_read_authorized": False,
        "bounded_pagination_incomplete_policy": (
            "RETAIN_PARTIAL_AND_CONTINUE_TO_NEXT_BUNDLE"
            if pagination_partial_allowed
            else "NOT_APPLICABLE"
        ),
        "transport_or_validation_error_policy": "STOP_IMMEDIATELY",
    }


def _bundles() -> list[dict[str, Any]]:
    return [
        _reused_bundle(
            bundle_id="CR1_MSFT_NEWS_REFRESH",
            original_bundle_id="ER2_MSFT_NEWS_REFRESH",
            order=1,
            max_dispatch_attempts=2,
            target_ids=[
                "MSFT_VALUATION_CONTEXT_DEPTH",
                "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
            ],
            pagination_partial_allowed=True,
        ),
        _reused_bundle(
            bundle_id="CR2_META_NEWS_REFRESH",
            original_bundle_id="ER3_META_NEWS_REFRESH",
            order=2,
            max_dispatch_attempts=2,
            target_ids=[
                "META_CONDITION_001",
                "META_CONDITION_002",
                "META_CONDITION_003",
            ],
            pagination_partial_allowed=True,
        ),
        _reused_bundle(
            bundle_id="CR3_CURRENT_PAPER_POSITIONS",
            original_bundle_id="ER4_CURRENT_PAPER_POSITIONS",
            order=3,
            max_dispatch_attempts=1,
            target_ids=["META_CONDITION_004"],
        ),
        _reused_bundle(
            bundle_id="CR4_CURRENT_PORTFOLIO_EQUITY",
            original_bundle_id="ER5_CURRENT_PORTFOLIO_EQUITY",
            order=4,
            max_dispatch_attempts=1,
            target_ids=["META_CONDITION_004"],
        ),
        _reused_bundle(
            bundle_id="CR5_DYNAMIC_MARKET_CONTEXT",
            original_bundle_id="ER6_DYNAMIC_MARKET_CONTEXT",
            order=5,
            max_dispatch_attempts=1,
            target_ids=["MSFT_VALUATION_CONTEXT_DEPTH", "META_CONDITION_004"],
        ) | {"depends_on_bundle_ids": ["CR3_CURRENT_PAPER_POSITIONS"]},
        {
            "bundle_id": "CR6_NVDA_NEWS_CONTINUATION",
            "original_bundle_id": "ER1_NVDA_NEWS_REFRESH",
            "execution_order": 6,
            "provider": "ALPACA_MARKET_DATA_NEWS",
            "existing_transport": "ReopenAlpacaCliNewsTransport",
            "required_new_primitive": "AIC_ALPACA_NEWS_REOPEN_CONTINUATION_FROM_SAVED_TOKEN_v0_1",
            "auth_mode": "CLI_PROFILE:paper",
            "target_ids": ["NVDA_CURRENT_DEVELOPMENTS_Q4"],
            "max_dispatch_attempts": NVDA_MAX_ADDITIONAL_PAGES,
            "provider_read_authorized": False,
            "request_contract": {
                "symbol": "NVDA",
                "window_start_utc": "2026-08-28T17:34:00Z",
                "window_end_utc": EXPECTED_REOPEN_CUTOFF_UTC,
                "page_size": NEWS_PAGE_SIZE,
                "start_page_token": EXPECTED_NVDA_TERMINAL_TOKEN,
                "start_page_token_required": True,
                "retained_partial_evidence_hash": EXPECTED_NVDA_RETAINED_HASH,
                "retained_aggregate_payload_hash": EXPECTED_NVDA_AGGREGATE_HASH,
                "retained_page_count": NVDA_RETAINED_PAGES,
                "retained_article_count": 10,
                "retained_page_raw_payload_hashes": list(EXPECTED_NVDA_PAGE_HASHES),
                "replay_retained_pages": False,
                "max_additional_pages": NVDA_MAX_ADDITIONAL_PAGES,
                "total_page_engineering_bound_including_retained": NVDA_TOTAL_PAGE_ENGINEERING_BOUND,
                "max_total_articles_including_retained": 30,
                "duplicate_article_id_policy": "ALLOW_ONLY_IF_CONTENT_HASH_IDENTICAL_TO_RETAINED_ARTICLE; OTHERWISE FAIL_CLOSED",
                "pagination_completion_rule": "TERMINAL_TOKEN_NULL_MEANS_COMPLETE; NONTERMINAL_AFTER_4_ADDITIONAL_PAGES_IS_PARTIAL_NOT_TRANSPORT_ERROR",
            },
            "bounded_pagination_incomplete_policy": "RETAIN_PARTIAL_AND_CONTINUE_TO_RECONCILIATION",
            "transport_or_validation_error_policy": "STOP_IMMEDIATELY",
        },
    ]


def build_plan(*, reconciliation: Mapping[str, Any], code_commit_sha: str) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha or "") is not None, "exact continuation-plan code SHA required")
    source_hash = verify_reconciliation(reconciliation)
    bundles = _bundles()
    _need(tuple(row["bundle_id"] for row in bundles) == BUNDLE_IDS, "continuation bundle order drift")
    _need(sum(int(row["max_dispatch_attempts"]) for row in bundles) == PROVIDER_DISPATCH_ATTEMPTS_MAX, "continuation dispatch ceiling arithmetic drift")
    _need(all(row["provider_read_authorized"] is False for row in bundles), "continuation plan cannot authorize provider reads")

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_reconciliation_v02_hash": source_hash,
        "source_reconciliation_code_sha": EXPECTED_RECONCILIATION_CODE_SHA,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "old_provider_authority_consumed": True,
        "old_provider_authority_reusable": False,
        "old_production_read_pass_rerun_allowed": False,
        "residual_external_read_target_count": len(RESIDUAL_TARGET_IDS),
        "residual_external_read_target_ids": list(RESIDUAL_TARGET_IDS),
        "logical_provider_read_bundle_count": len(BUNDLE_IDS),
        "logical_provider_read_bundle_ids": list(BUNDLE_IDS),
        "provider_read_bundles": bundles,
        "provider_dispatch_attempts_max": PROVIDER_DISPATCH_ATTEMPTS_MAX,
        "news_dispatch_attempts_max": NEWS_DISPATCH_ATTEMPTS_MAX,
        "non_news_dispatch_attempts_max": NON_NEWS_DISPATCH_ATTEMPTS_MAX,
        "nvda_retained_dispatch_attempts": 2,
        "nvda_retained_page_count": NVDA_RETAINED_PAGES,
        "nvda_retained_article_count": 10,
        "nvda_max_additional_pages": NVDA_MAX_ADDITIONAL_PAGES,
        "nvda_total_page_engineering_bound_including_retained": NVDA_TOTAL_PAGE_ENGINEERING_BOUND,
        "nvda_start_page_token": EXPECTED_NVDA_TERMINAL_TOKEN,
        "nvda_replay_retained_pages_allowed": False,
        "reused_original_undispatched_template_hashes": ORIGINAL_UNDISPATCHED_TEMPLATE_HASHES,
        "pagination_incomplete_is_transport_error": False,
        "pagination_incomplete_continue_policy": "RETAIN_PARTIAL_AND_CONTINUE_TO_LATER_BUNDLES",
        "transport_or_validation_error_policy": "STOP_IMMEDIATELY",
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "conditional_followup_reads_authorized": False,
        "owner_approval_required_before_provider_read": True,
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
        "live_money": "PROHIBITED",
        "post_read_rule": "FREEZE_ALL_COMPLETE_OR_PARTIAL_RESULTS_THEN_RUN_ZERO_CALL_RECONCILIATION; NO_AUTOMATIC_SECOND_PASS",
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_plan(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == ARTIFACT_VERSION, "continuation plan version drift")
    _need(payload.get("status") == PASS_STATUS, "continuation plan status drift")
    _need(payload.get("code_commit_sha") == expected_code_commit_sha, "continuation plan code SHA drift")
    _need(payload.get("source_reconciliation_v02_hash") == EXPECTED_RECONCILIATION_HASH, "continuation plan reconciliation lineage drift")
    _need(payload.get("provider_dispatch_attempts_max") == PROVIDER_DISPATCH_ATTEMPTS_MAX, "continuation plan dispatch ceiling drift")
    _need(payload.get("provider_reads_authorized") is False, "continuation plan unexpectedly authorizes reads")
    _need(payload.get("model_calls_authorized") is False, "continuation plan unexpectedly authorizes model calls")
    _need(payload.get("nvda_start_page_token") == EXPECTED_NVDA_TERMINAL_TOKEN, "continuation plan NVDA token drift")
    _need(payload.get("nvda_replay_retained_pages_allowed") is False, "continuation plan allows NVDA replay")
    _need(payload.get("next_gate") == NEXT_GATE, "continuation plan next gate drift")
    return observed
