from __future__ import annotations

from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_cr4_to_cr6_repair_preflight_runtime_fix_v02 as v02_fix
from aic.research import reopen_judge_cr4_to_cr6_repair_preflight_v01 as base
from aic.research import reopen_judge_durable_provider_read_failure_reconciliation_v02 as durable_v02


RUNTIME_FIX_VERSION = "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_PREFLIGHT_RUNTIME_FIX_v0_3"
ORIGINAL_RESULT_VALIDATION_SURFACE = "ALPACA_NEWS_REOPEN_TYPED_MODEL_VALIDATOR"
LEGACY_V01_JSON_DICT_REHASH_ALLOWED = False


class CR4ToCR6RepairPreflightRuntimeFixV03Error(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise CR4ToCR6RepairPreflightRuntimeFixV03Error(message)


def verify_original_result_v03(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the original provider result on the same typed surface as production.

    The legacy CR4->CR6 preflight delegated to the V01 failure reconciler, which
    re-hashed an already JSON-serialized datetime-bearing dict and reproduced the
    known V01 false negative. V03 deliberately routes through the corrected V02
    typed-model verifier instead.
    """

    summary = durable_v02.verify_result_v02(payload)
    _need(
        summary.get("result_artifact_hash") == base.EXPECTED_ORIGINAL_RESULT_HASH,
        "original result hash drift",
    )
    _need(
        summary.get("nvda_terminal_next_page_token") == base.EXPECTED_NVDA_CONTINUATION_TOKEN,
        "NVDA continuation token drift",
    )
    _need(
        summary.get("nvda_retained_article_count") == base.EXPECTED_NVDA_RETAINED_ARTICLE_COUNT,
        "NVDA retained article count drift",
    )
    _need(
        summary.get("nvda_aggregate_validation_surface") == ORIGINAL_RESULT_VALIDATION_SURFACE,
        "original result validation surface drift",
    )
    return summary


def probe_local_alpaca_cli() -> dict[str, Any]:
    # Preserve the V02 correction for the real `alpaca version` output shape.
    return v02_fix.probe_local_alpaca_cli()


def build_preflight(
    *,
    reconciliation: Mapping[str, Any],
    original_result: Mapping[str, Any],
    code_commit_sha: str,
    capability_probe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    probe = dict(capability_probe or probe_local_alpaca_cli())

    # `base.build_preflight` is otherwise the frozen deterministic builder we
    # want to preserve. Its only invalid dependency is the legacy
    # `verify_original_result`. Replace that dependency only for this synchronous
    # zero-call build and restore it unconditionally afterwards.
    previous_verifier = base.verify_original_result
    try:
        base.verify_original_result = verify_original_result_v03
        artifact = base.build_preflight(
            reconciliation=reconciliation,
            original_result=original_result,
            code_commit_sha=code_commit_sha,
            capability_probe=probe,
        )
    finally:
        base.verify_original_result = previous_verifier

    artifact["preflight_runtime_fix_version"] = RUNTIME_FIX_VERSION
    artifact["original_result_validation_surface"] = ORIGINAL_RESULT_VALIDATION_SURFACE
    artifact["legacy_v01_json_dict_rehash_allowed"] = LEGACY_V01_JSON_DICT_REHASH_ALLOWED
    artifact["artifact_hash"] = canonical_sha256(
        artifact,
        exclude_fields=("artifact_hash",),
    )
    return artifact


def verify_preflight(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = base.verify_preflight(
        payload,
        expected_code_commit_sha=expected_code_commit_sha,
    )
    _need(
        payload.get("preflight_runtime_fix_version") == RUNTIME_FIX_VERSION,
        "preflight runtime-fix version drift",
    )
    _need(
        payload.get("original_result_validation_surface") == ORIGINAL_RESULT_VALIDATION_SURFACE,
        "preflight original-result validation surface drift",
    )
    _need(
        payload.get("legacy_v01_json_dict_rehash_allowed") is False,
        "legacy V01 JSON-dict aggregate rehash unexpectedly allowed",
    )
    return observed


ARTIFACT_VERSION = base.ARTIFACT_VERSION
PASS_STATUS = base.PASS_STATUS
NEXT_GATE = base.NEXT_GATE
EXPECTED_RECONCILIATION_HASH = base.EXPECTED_RECONCILIATION_HASH
EXPECTED_ORIGINAL_RESULT_HASH = base.EXPECTED_ORIGINAL_RESULT_HASH
EXPECTED_REOPEN_CUTOFF_UTC = base.EXPECTED_REOPEN_CUTOFF_UTC
EXPECTED_NVDA_CONTINUATION_TOKEN = base.EXPECTED_NVDA_CONTINUATION_TOKEN
BUNDLE_IDS = base.BUNDLE_IDS
PROVIDER_DISPATCH_CEILING_BY_BUNDLE = base.PROVIDER_DISPATCH_CEILING_BY_BUNDLE
PROVIDER_DISPATCH_ATTEMPTS_MAX = base.PROVIDER_DISPATCH_ATTEMPTS_MAX
