from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from aic.council import initial_runtime_authorization as authorization_runtime
from aic.council.initial_runtime_cost_v02 import (
    actual_cost_usd,
    load_initial_runtime_pricing,
    process_initial_provider_response,
    verify_initial_runtime_cost_preflight,
)
from aic.domain.canonical import canonical_sha256


PAID_AUTHORIZATION_VERSION = "B4_INITIAL_RUNTIME_PAID_AUTHORIZATION_ARTIFACT_v0_2"
PAID_RECEIPT_VERSION = "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_2"
BILLING_CONTRACT_VERSION = "B4_INITIAL_RUNTIME_CACHE_WRITE_BILLING_v0_1"
DEFAULT_COST_PREFLIGHT = Path(
    ".aic-runtime/b4_initial_runtime_cost_preflight_v0_2.json"
)
DEFAULT_OUTPUT = Path(".aic-runtime/b4_initial_council_freeze_v0_2.json")
DEFAULT_AUTHORIZATION_OUTPUT = Path(
    ".aic-runtime/b4_initial_runtime_paid_authorization_v0_2.json"
)
DEFAULT_RECEIPT_JOURNAL = Path(
    ".aic-runtime/b4_initial_runtime_paid_receipts_v0_2.jsonl"
)


def _load_legacy_runner():
    path = Path(__file__).with_name("b4_run_initial_runtime.py")
    spec = importlib.util.spec_from_file_location(
        "_aic_b4_run_initial_runtime_v01", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load frozen B4 Initial production runner v0.1")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


legacy = _load_legacy_runner()
_legacy_build_receipt = legacy._build_receipt
_legacy_blocked_artifact = legacy._blocked_artifact


def _cache_write_tokens(raw: Mapping[str, Any] | None) -> int | None:
    if not isinstance(raw, Mapping):
        return None
    usage = raw.get("usage")
    if not isinstance(usage, Mapping):
        return None
    details = usage.get("input_tokens_details")
    if not isinstance(details, Mapping):
        return None
    value = details.get("cache_write_tokens")
    return value if type(value) is int and value >= 0 else None


def _build_receipt_v02(**kwargs: Any) -> dict[str, Any]:
    receipt = _legacy_build_receipt(**kwargs)
    receipt.pop("receipt_hash", None)
    tracker = kwargs["tracker"]
    raw = tracker.last_response
    cache_write_tokens = _cache_write_tokens(raw)
    receipt["receipt_version"] = PAID_RECEIPT_VERSION
    receipt["billing_contract_version"] = BILLING_CONTRACT_VERSION
    receipt["cache_write_tokens"] = cache_write_tokens

    if receipt.get("provider_response_received") is True and isinstance(raw, Mapping):
        try:
            corrected = actual_cost_usd(
                raw,
                model=receipt["requested_model"],
                pricing=kwargs["pricing"],
            )
        except Exception as exc:
            receipt["actual_cost_usd"] = None
            receipt["cost_receipt_status"] = "INCOMPLETE_CACHE_WRITE_USAGE"
            receipt["validation_status"] = "FAIL"
            existing = receipt.get("validation_error")
            detail = f"{type(exc).__name__}: {exc}"
            receipt["validation_error"] = (
                detail if not existing else f"{existing}; {detail}"
            )
        else:
            receipt["actual_cost_usd"] = str(corrected)
            receipt["cost_receipt_status"] = "COMPLETE"

    receipt["receipt_hash"] = canonical_sha256(receipt)
    return receipt


def _blocked_artifact_v02(**kwargs: Any) -> dict[str, Any]:
    artifact = _legacy_blocked_artifact(**kwargs)
    reason = str(kwargs.get("reason", ""))
    if "cache_write" in reason or "input_tokens_details" in reason:
        artifact["cost_receipt_status"] = "INCOMPLETE"
    artifact["billing_contract_version"] = BILLING_CONTRACT_VERSION
    artifact.pop("artifact_hash", None)
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def _patch_runner() -> None:
    authorization_runtime.verify_initial_runtime_cost_preflight = (
        verify_initial_runtime_cost_preflight
    )
    authorization_runtime.INITIAL_RUNTIME_PAID_AUTHORIZATION_VERSION = (
        PAID_AUTHORIZATION_VERSION
    )
    authorization_runtime.INITIAL_RUNTIME_PAID_RECEIPT_VERSION = (
        PAID_RECEIPT_VERSION
    )

    legacy.DEFAULT_COST_PREFLIGHT = DEFAULT_COST_PREFLIGHT
    legacy.DEFAULT_OUTPUT = DEFAULT_OUTPUT
    legacy.DEFAULT_AUTHORIZATION_OUTPUT = DEFAULT_AUTHORIZATION_OUTPUT
    legacy.DEFAULT_RECEIPT_JOURNAL = DEFAULT_RECEIPT_JOURNAL
    legacy.verify_initial_runtime_cost_preflight = (
        verify_initial_runtime_cost_preflight
    )
    legacy.load_openai_text_pricing = load_initial_runtime_pricing
    legacy.process_initial_provider_response = process_initial_provider_response
    legacy.INITIAL_RUNTIME_PAID_RECEIPT_VERSION = PAID_RECEIPT_VERSION
    legacy._build_receipt = _build_receipt_v02
    legacy._blocked_artifact = _blocked_artifact_v02


_patch_runner()


def main() -> int:
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
