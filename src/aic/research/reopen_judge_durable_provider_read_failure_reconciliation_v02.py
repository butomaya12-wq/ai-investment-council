from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from aic.data.providers.alpaca_news_reopen import AlpacaNewsReopenRead
from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_durable_provider_read_failure_reconciliation_v01 as v01


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_DURABLE_PROVIDER_READ_FAILURE_RECONCILIATION_v0_2"
PASS_STATUS = "B3_RESEARCH_REOPEN_DURABLE_PROVIDER_READ_FAILURE_RECONCILIATION_V02_ZERO_CALL_PASS"
NEXT_GATE = v01.NEXT_GATE


class DurableProviderReadFailureReconciliationV02Error(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise DurableProviderReadFailureReconciliationV02Error(message)


def verify_result_v02(payload: Mapping[str, Any]) -> dict[str, Any]:
    observed = v01._self_hash(payload)
    _need(observed == v01.EXPECTED_RESULT_HASH, "result hash drift")
    _need(
        payload.get("artifact_version") == "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_RESULT_v0_1",
        "result version drift",
    )
    _need(
        payload.get("status") == "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_BLOCKED",
        "result status drift",
    )
    _need(
        payload.get("authorization_artifact_hash") == v01.EXPECTED_AUTHORIZATION_HASH,
        "result authorization lineage drift",
    )
    _need(
        payload.get("source_preflight_hash") == v01.EXPECTED_PREFLIGHT_HASH,
        "result preflight lineage drift",
    )
    _need(
        payload.get("request_manifest_hash") == v01.EXPECTED_REQUEST_MANIFEST_HASH,
        "result manifest drift",
    )
    _need(
        payload.get("reopen_cutoff_utc") == v01.EXPECTED_REOPEN_CUTOFF_UTC,
        "result cutoff drift",
    )
    _need(
        payload.get("provider_dispatch_attempts") == v01.EXPECTED_OBSERVED_DISPATCH_ATTEMPTS,
        "result dispatch count drift",
    )
    _need(
        payload.get("provider_dispatch_attempts_max") == v01.EXPECTED_MAX_DISPATCH_ATTEMPTS,
        "result dispatch ceiling drift",
    )
    _need(payload.get("failed_bundle_id") == v01.EXPECTED_FAILED_BUNDLE_ID, "failed bundle drift")
    _need(payload.get("failure_reason") == v01.EXPECTED_FAILURE_REASON, "failure reason drift")
    _need(payload.get("automatic_retries") == 0, "result retry drift")
    _need(payload.get("model_calls") == 0, "result model call drift")
    _need(payload.get("model_synthesis_calls") == 0, "result synthesis call drift")
    _need(payload.get("broker_writes") == 0, "result broker write drift")
    _need(payload.get("alpaca_orders") == 0, "result order drift")
    _need(payload.get("live_money") == "PROHIBITED", "result live-money drift")
    _need(payload.get("final_decision_created") is False, "result FinalDecision drift")
    _need(payload.get("b5_handoff_created") is False, "result B5 drift")
    _need(
        payload.get("next_gate") == "ZERO_CALL_DURABLE_PROVIDER_READ_FAILURE_RECONCILIATION",
        "result next gate drift",
    )

    bundle_results = payload.get("bundle_results")
    _need(
        isinstance(bundle_results, list) and len(bundle_results) == 1,
        "exactly one partial bundle result required",
    )
    bundle = bundle_results[0]
    _need(isinstance(bundle, Mapping), "partial bundle result malformed")
    _need(bundle.get("bundle_id") == v01.EXPECTED_FAILED_BUNDLE_ID, "partial bundle id drift")
    _need(bundle.get("status") == "PARTIAL_STOP", "partial bundle status drift")
    _need(
        bundle.get("provider_dispatch_attempts") == v01.EXPECTED_OBSERVED_DISPATCH_ATTEMPTS,
        "partial bundle dispatch count drift",
    )
    _need(
        bundle.get("response_artifact_hash") == v01.EXPECTED_PARTIAL_RESPONSE_HASH,
        "partial response artifact hash drift",
    )

    response = bundle.get("response_artifact")
    _need(isinstance(response, Mapping), "partial response artifact missing")
    _need(
        canonical_sha256(response) == v01.EXPECTED_PARTIAL_RESPONSE_HASH,
        "partial response canonical hash mismatch",
    )
    _need(
        response.get("aggregate_payload_hash") == v01.EXPECTED_NVDA_AGGREGATE_HASH,
        "NVDA aggregate hash drift",
    )

    # Critical V02 correction: aggregate_payload_hash belongs to the typed
    # AlpacaNewsReopenRead contract. Reconstruct the exact model so its native
    # validator performs the same hash validation as production. Re-hashing the
    # already JSON-serialized response dict is not an equivalent operation for
    # typed datetime-bearing payloads and caused the V01 false negative.
    try:
        typed_response = AlpacaNewsReopenRead.model_validate(dict(response))
    except ValidationError as exc:
        raise DurableProviderReadFailureReconciliationV02Error(
            "NVDA typed aggregate validation failed"
        ) from exc
    _need(
        typed_response.aggregate_payload_hash == v01.EXPECTED_NVDA_AGGREGATE_HASH,
        "NVDA typed aggregate hash drift",
    )

    _need(response.get("symbol") == "NVDA", "NVDA partial symbol drift")
    _need(response.get("window_start") == v01.EXPECTED_NVDA_WINDOW_START, "NVDA window start drift")
    _need(response.get("window_end") == v01.EXPECTED_NVDA_WINDOW_END, "NVDA window end drift")
    _need(response.get("page_size") == v01.EXPECTED_NVDA_PAGE_SIZE, "NVDA page size drift")
    _need(response.get("page_count") == v01.EXPECTED_NVDA_PAGE_COUNT, "NVDA page count drift")
    _need(response.get("max_pages") == v01.EXPECTED_NVDA_MAX_PAGES, "NVDA max pages drift")
    _need(response.get("pagination_complete") is False, "NVDA pagination unexpectedly complete")
    _need(
        response.get("terminal_next_page_token") == v01.EXPECTED_NVDA_TERMINAL_NEXT_PAGE_TOKEN,
        "NVDA terminal next page token drift",
    )
    _need(
        tuple(response.get("page_raw_payload_hashes", ())) == v01.EXPECTED_NVDA_PAGE_HASHES,
        "NVDA page hashes drift",
    )
    articles = response.get("articles")
    _need(
        isinstance(articles, list) and len(articles) == v01.EXPECTED_NVDA_ARTICLE_COUNT,
        "NVDA article count drift",
    )
    _need(
        len(
            {
                article.get("article_id")
                for article in articles
                if isinstance(article, Mapping)
            }
        )
        == v01.EXPECTED_NVDA_ARTICLE_COUNT,
        "NVDA article ids are not unique",
    )

    return {
        "result_artifact_hash": observed,
        "partial_response_artifact_hash": v01.EXPECTED_PARTIAL_RESPONSE_HASH,
        "nvda_aggregate_payload_hash": typed_response.aggregate_payload_hash,
        "nvda_page_raw_payload_hashes": list(v01.EXPECTED_NVDA_PAGE_HASHES),
        "nvda_terminal_next_page_token": v01.EXPECTED_NVDA_TERMINAL_NEXT_PAGE_TOKEN,
        "nvda_retained_article_count": v01.EXPECTED_NVDA_ARTICLE_COUNT,
        "nvda_aggregate_validation_surface": "ALPACA_NEWS_REOPEN_TYPED_MODEL_VALIDATOR",
    }


def build_reconciliation_v02(
    *,
    local_replay: Mapping[str, Any],
    authorization: Mapping[str, Any],
    journal_rows: Sequence[Mapping[str, Any]],
    result: Mapping[str, Any],
    code_commit_sha: str,
) -> dict[str, Any]:
    _need(
        re.fullmatch(r"[0-9a-f]{40}", code_commit_sha or "") is not None,
        "exact reconciliation code SHA required",
    )
    local_replay_hash = v01.verify_local_replay(local_replay)
    authorization_hash = v01.verify_authorization(authorization)
    journal_summary = v01.verify_journal(journal_rows)
    result_summary = verify_result_v02(result)

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_runtime_code_commit_sha": v01.EXPECTED_SOURCE_CODE_SHA,
        "source_local_replay_hash": local_replay_hash,
        "source_preflight_hash": v01.EXPECTED_PREFLIGHT_HASH,
        "source_request_manifest_hash": v01.EXPECTED_REQUEST_MANIFEST_HASH,
        "source_runner_dry_hash": v01.EXPECTED_RUNNER_DRY_HASH,
        "source_authorization_artifact_hash": authorization_hash,
        "source_result_artifact_hash": result_summary["result_artifact_hash"],
        "reopen_cutoff_utc": v01.EXPECTED_REOPEN_CUTOFF_UTC,
        "failure_class": "BOUNDED_NEWS_PAGINATION_NOT_TERMINAL",
        "failed_bundle_id": v01.EXPECTED_FAILED_BUNDLE_ID,
        "failure_reason": v01.EXPECTED_FAILURE_REASON,
        "authority_consumed": True,
        "authority_consumed_on_first_dispatch": True,
        "original_authority_reusable": False,
        "original_production_read_pass_rerun_allowed": False,
        "provider_dispatch_attempts_observed": journal_summary["provider_dispatch_attempt_count"],
        "provider_response_receipts_observed": journal_summary["provider_response_receipt_count"],
        "provider_dispatch_attempts_original_ceiling": v01.EXPECTED_MAX_DISPATCH_ATTEMPTS,
        "first_dispatch_event_hash": journal_summary["first_dispatch_event_hash"],
        "last_dispatch_event_hash": journal_summary["last_dispatch_event_hash"],
        "partial_provider_bundle_count": 1,
        "completed_provider_bundle_count": 0,
        "undispatched_provider_bundle_count": len(v01.UNDISPATCHED_BUNDLE_IDS),
        "undispatched_provider_bundle_ids": list(v01.UNDISPATCHED_BUNDLE_IDS),
        "retained_partial_evidence_bundle_id": v01.EXPECTED_FAILED_BUNDLE_ID,
        "retained_partial_evidence_hash": result_summary["partial_response_artifact_hash"],
        "retained_partial_evidence_usable": True,
        "retained_partial_evidence_complete": False,
        "nvda_retained_article_count": result_summary["nvda_retained_article_count"],
        "nvda_retained_page_count": v01.EXPECTED_NVDA_PAGE_COUNT,
        "nvda_retained_page_raw_payload_hashes": result_summary["nvda_page_raw_payload_hashes"],
        "nvda_aggregate_payload_hash": result_summary["nvda_aggregate_payload_hash"],
        "nvda_aggregate_validation_surface": result_summary["nvda_aggregate_validation_surface"],
        "nvda_terminal_next_page_token": result_summary["nvda_terminal_next_page_token"],
        "nvda_continuation_must_start_from_terminal_token": True,
        "nvda_replay_of_retained_pages_allowed": False,
        "nvda_original_total_page_engineering_bound": 6,
        "nvda_max_additional_pages_without_expanding_original_total_bound": 4,
        "residual_external_read_target_count": len(v01.RESIDUAL_TARGET_IDS),
        "residual_external_read_target_ids": list(v01.RESIDUAL_TARGET_IDS),
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
