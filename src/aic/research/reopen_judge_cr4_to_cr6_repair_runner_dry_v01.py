from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_cr4_to_cr6_repair_preflight_runtime_fix_v03 as preflight_v03


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_RUNNER_DRY_v0_1"
READY_STATUS = "READY_FOR_EXPLICIT_OWNER_B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_AUTHORIZATION_V01"
NEXT_GATE = "EXPLICIT_OWNER_B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_AUTHORIZATION_V01"

EXPECTED_PREFLIGHT_CODE_SHA = "7d8e38ba4b14b8b5d2b6054e6cce87abddfc35ab"
EXPECTED_PREFLIGHT_HASH = "32fb34950fc02c30fd7a8b91a468041bc3b8c80f9e6e74de6d6c5389ad715e5d"
EXPECTED_REQUEST_MANIFEST_HASH = "c2a7dcfa8b65f7937619664f6ef32dfb29a85df47ac58ad2a7b1b1fed6c25077"
EXPECTED_CAPABILITY_PROBE_HASH = "01904e8da863ee951771f693507e21cbdd218752cffe5be029a7537e21b64f35"
EXPECTED_ALPACA_BINARY_SHA256 = "43ea82ad405529454c20336e88c68465049ba4c58b9e2ed0e05453c06757c0f0"
EXPECTED_ALPACA_BINARY_BYTES = 8_888_834
EXPECTED_ALPACA_VERSION = "0.0.13"
EXPECTED_REOPEN_CUTOFF_UTC = "2026-08-31T08:58:17Z"
EXPECTED_VALIDATION_SURFACE = "ALPACA_NEWS_REOPEN_TYPED_MODEL_VALIDATOR"
EXPECTED_SOURCE_RECONCILIATION_HASH = "07d0acc69e9f7806abf828354e7032611f72d3e960a76f6e8639994e5d857e07"
EXPECTED_SOURCE_ORIGINAL_RESULT_HASH = "45980cba660dff7df1e013808c760a7eae95456e830e734ecd1641021d0cdfc1"

BUNDLE_IDS = (
    "RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR",
    "RR2_DYNAMIC_MARKET_CONTEXT",
    "RR3_NVDA_NEWS_CONTINUATION",
)
DISPATCH_CEILING_BY_BUNDLE = {
    "RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR": 1,
    "RR2_DYNAMIC_MARKET_CONTEXT": 1,
    "RR3_NVDA_NEWS_CONTINUATION": 4,
}
MAX_DISPATCH_ATTEMPTS = 6
EXPECTED_TEMPLATE_HASHES = (
    "20b9e280807a054bb695b69db9be6a5a034804b94e2f4ae87a38f26ea296e272",
    "e8b235c2e6c43a86ed6702694d77a0390643d1bc515293fc03b26d16f0d25fc1",
    "0de13d267cbdb02757949f1bec07329eaee76bc1055972b7806749debcc2fdf2",
)


class CR4ToCR6RepairRunnerDryError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise CR4ToCR6RepairRunnerDryError(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(
        isinstance(observed, str)
        and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        f"{field} missing",
    )
    _need(
        observed == canonical_sha256(payload, exclude_fields=(field,)),
        f"{field} self-hash mismatch",
    )
    return observed


def verify_preflight(payload: Mapping[str, Any]) -> str:
    try:
        observed = preflight_v03.verify_preflight(
            payload,
            expected_code_commit_sha=EXPECTED_PREFLIGHT_CODE_SHA,
        )
    except Exception as exc:
        raise CR4ToCR6RepairRunnerDryError(str(exc)) from exc

    _need(observed == EXPECTED_PREFLIGHT_HASH, "preflight artifact hash drift")
    exact = {
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "local_cli_capability_probe_hash": EXPECTED_CAPABILITY_PROBE_HASH,
        "source_wire_repair_v02_failure_reconciliation_hash": EXPECTED_SOURCE_RECONCILIATION_HASH,
        "source_original_provider_result_hash": EXPECTED_SOURCE_ORIGINAL_RESULT_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "original_result_validation_surface": EXPECTED_VALIDATION_SURFACE,
        "legacy_v01_json_dict_rehash_allowed": False,
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "provider_dispatch_ceiling_by_bundle": DISPATCH_CEILING_BY_BUNDLE,
        "remaining_provider_read_bundle_ids": list(BUNDLE_IDS),
        "completed_prior_bundle_rereads_allowed": False,
        "msft_news_reread_allowed": False,
        "meta_news_reread_allowed": False,
        "positions_reread_allowed": False,
        "owner_provider_read_approval_present": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "provider_reads": 0,
        "model_calls": 0,
        "model_synthesis_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd": "0",
        "live_money": "PROHIBITED",
        "next_gate": "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_RUNNER_DRY_ZERO_CALL",
    }
    for key, expected in exact.items():
        _need(payload.get(key) == expected, f"preflight drift: {key}")

    templates = payload.get("request_templates")
    _need(isinstance(templates, list) and len(templates) == 3, "request template count drift")
    _need(
        [row.get("request_template_hash") for row in templates] == list(EXPECTED_TEMPLATE_HASHES),
        "request template hash drift",
    )

    probe = payload.get("local_cli_capability_probe")
    _need(isinstance(probe, Mapping), "local CLI capability probe missing")
    _need(probe.get("capability_probe_hash") == EXPECTED_CAPABILITY_PROBE_HASH, "capability probe inner hash drift")
    _need(probe.get("alpaca_binary_sha256") == EXPECTED_ALPACA_BINARY_SHA256, "Alpaca binary hash drift in preflight")
    _need(probe.get("alpaca_binary_bytes") == EXPECTED_ALPACA_BINARY_BYTES, "Alpaca binary size drift in preflight")
    _need(probe.get("version_output_first_line") == EXPECTED_ALPACA_VERSION, "Alpaca version drift in preflight")
    _need(probe.get("portfolio_timeframe_1d_confirmed") is True, "portfolio 1D capability not frozen")
    _need(probe.get("provider_reads_during_probe") == 0, "provider read observed during capability probe")
    _need(probe.get("model_calls_during_probe") == 0, "model call observed during capability probe")
    return observed


def verify_installed_alpaca_binary(preflight: Mapping[str, Any]) -> dict[str, Any]:
    probe = preflight.get("local_cli_capability_probe")
    _need(isinstance(probe, Mapping), "local CLI capability probe missing")
    expected_path = probe.get("alpaca_executable_path")
    _need(isinstance(expected_path, str) and expected_path, "frozen Alpaca executable path missing")

    which_path = shutil.which("alpaca")
    _need(which_path is not None, "Alpaca CLI executable unavailable at dry gate")
    resolved = Path(which_path).resolve()
    frozen_resolved = Path(expected_path).resolve()
    _need(resolved == frozen_resolved, "installed Alpaca executable path drift")
    _need(resolved.is_file(), "installed Alpaca executable is not a file")

    raw = resolved.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    _need(len(raw) == EXPECTED_ALPACA_BINARY_BYTES, "installed Alpaca binary size drift")
    _need(sha == EXPECTED_ALPACA_BINARY_SHA256, "installed Alpaca binary SHA256 drift")
    return {
        "alpaca_executable_path": str(resolved),
        "alpaca_binary_sha256": sha,
        "alpaca_binary_bytes": len(raw),
        "alpaca_version_frozen": EXPECTED_ALPACA_VERSION,
        "binary_reverification_provider_reads": 0,
        "binary_reverification_model_calls": 0,
    }


def build_dry(*, preflight: Mapping[str, Any], code_commit_sha: str) -> dict[str, Any]:
    _need(
        isinstance(code_commit_sha, str)
        and re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None,
        "exact code SHA required",
    )
    preflight_hash = verify_preflight(preflight)
    binary = verify_installed_alpaca_binary(preflight)

    artifact = {
        "artifact_version": ARTIFACT_VERSION,
        "status": READY_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_preflight_code_commit_sha": EXPECTED_PREFLIGHT_CODE_SHA,
        "source_preflight_artifact_hash": preflight_hash,
        "source_wire_repair_v02_failure_reconciliation_hash": EXPECTED_SOURCE_RECONCILIATION_HASH,
        "source_original_provider_result_hash": EXPECTED_SOURCE_ORIGINAL_RESULT_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "capability_probe_hash": EXPECTED_CAPABILITY_PROBE_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "original_result_validation_surface": EXPECTED_VALIDATION_SURFACE,
        "legacy_v01_json_dict_rehash_allowed": False,
        "alpaca_executable_path": binary["alpaca_executable_path"],
        "alpaca_binary_sha256": binary["alpaca_binary_sha256"],
        "alpaca_binary_bytes": binary["alpaca_binary_bytes"],
        "alpaca_version": EXPECTED_ALPACA_VERSION,
        "installed_cli_binary_reverified_at_dry_gate": True,
        "installed_cli_binary_sha256_match": True,
        "binary_reverification_provider_reads": 0,
        "binary_reverification_model_calls": 0,
        "request_template_hashes": list(EXPECTED_TEMPLATE_HASHES),
        "logical_provider_read_bundle_ids": list(BUNDLE_IDS),
        "provider_dispatch_ceiling_by_bundle": dict(DISPATCH_CEILING_BY_BUNDLE),
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "completed_prior_bundle_rereads_allowed": False,
        "msft_news_reread_allowed": False,
        "meta_news_reread_allowed": False,
        "positions_reread_allowed": False,
        "remaining_bundles_independent": True,
        "bundle_failure_policy": "DURABLY_RECORD_FAILURE_AND_CONTINUE_ONLY_ALREADY_FROZEN_INDEPENDENT_READ_BUNDLES",
        "raw_response_snapshot_before_parse_required": True,
        "nonzero_cli_stdout_stderr_snapshot_before_raise_required": True,
        "nvda_retained_pages_replay_allowed": False,
        "nvda_continuation_start_token_required": True,
        "single_authorized_repair_provider_read_pass_only": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "conditional_followup_reads_authorized": False,
        "unplanned_provider_reads_authorized": False,
        "pagination_beyond_frozen_bounds_authorized": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "provider_reads": 0,
        "model_calls": 0,
        "model_synthesis_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd": "0",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_dry(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload)
    exact = {
        "artifact_version": ARTIFACT_VERSION,
        "status": READY_STATUS,
        "code_commit_sha": expected_code_commit_sha,
        "source_preflight_code_commit_sha": EXPECTED_PREFLIGHT_CODE_SHA,
        "source_preflight_artifact_hash": EXPECTED_PREFLIGHT_HASH,
        "source_wire_repair_v02_failure_reconciliation_hash": EXPECTED_SOURCE_RECONCILIATION_HASH,
        "source_original_provider_result_hash": EXPECTED_SOURCE_ORIGINAL_RESULT_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "capability_probe_hash": EXPECTED_CAPABILITY_PROBE_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "original_result_validation_surface": EXPECTED_VALIDATION_SURFACE,
        "legacy_v01_json_dict_rehash_allowed": False,
        "alpaca_binary_sha256": EXPECTED_ALPACA_BINARY_SHA256,
        "alpaca_binary_bytes": EXPECTED_ALPACA_BINARY_BYTES,
        "alpaca_version": EXPECTED_ALPACA_VERSION,
        "installed_cli_binary_reverified_at_dry_gate": True,
        "installed_cli_binary_sha256_match": True,
        "binary_reverification_provider_reads": 0,
        "binary_reverification_model_calls": 0,
        "request_template_hashes": list(EXPECTED_TEMPLATE_HASHES),
        "logical_provider_read_bundle_ids": list(BUNDLE_IDS),
        "provider_dispatch_ceiling_by_bundle": DISPATCH_CEILING_BY_BUNDLE,
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "completed_prior_bundle_rereads_allowed": False,
        "msft_news_reread_allowed": False,
        "meta_news_reread_allowed": False,
        "positions_reread_allowed": False,
        "remaining_bundles_independent": True,
        "raw_response_snapshot_before_parse_required": True,
        "nonzero_cli_stdout_stderr_snapshot_before_raise_required": True,
        "nvda_retained_pages_replay_allowed": False,
        "nvda_continuation_start_token_required": True,
        "single_authorized_repair_provider_read_pass_only": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "conditional_followup_reads_authorized": False,
        "unplanned_provider_reads_authorized": False,
        "pagination_beyond_frozen_bounds_authorized": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "model_synthesis_authorized": False,
        "provider_reads": 0,
        "model_calls": 0,
        "model_synthesis_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd": "0",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
    }
    for key, expected in exact.items():
        _need(payload.get(key) == expected, f"runner dry drift: {key}")
    return observed
