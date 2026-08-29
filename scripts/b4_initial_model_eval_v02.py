from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from aic.council import initial_eval_runtime as initial_runtime
from aic.domain.canonical import canonical_sha256
from aic.research.runtime import parse_responses_payload


EVAL_VERSION = "B4_INITIAL_MODEL_EVAL_v0_2"
ARTIFACT_VERSION = "B4_INITIAL_MODEL_EVAL_ARTIFACT_v0_3"
PAID_AUTHORIZATION_ARTIFACT_VERSION = "B4_INITIAL_PAID_AUTHORIZATION_ARTIFACT_v0_2"
PAID_CALL_RECEIPT_VERSION = "B4_INITIAL_PAID_CALL_RECEIPT_v0_2"
DEFAULT_OUTPUT = Path(".aic-runtime/b4_initial_model_eval_v02.json")
DEFAULT_AUTHORIZATION_OUTPUT = Path(
    ".aic-runtime/b4_initial_model_eval_paid_authorization_v02.json"
)
DEFAULT_RECEIPT_JOURNAL = Path(
    ".aic-runtime/b4_initial_model_eval_paid_receipts_v02.jsonl"
)


def _load_legacy_runner():
    path = Path(__file__).with_name("b4_initial_model_eval.py")
    spec = importlib.util.spec_from_file_location("_aic_b4_initial_model_eval_v01", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load frozen B4 Initial eval v0.1 runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


legacy = _load_legacy_runner()
_legacy_build_paid_call_receipt = legacy._build_paid_call_receipt


class ReplayableDispatchTrackingTransport:
    """Track dispatch and retain only the current provider response in process memory.

    The durable receipt stores the parsed structured output JSON, not the raw provider
    response object or provider output-text envelope. This is sufficient for deterministic
    semantic rescore while keeping the persisted evidence surface bounded.
    """

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.dispatch_attempts = 0
        self.provider_responses = 0
        self.last_provider_response: Mapping[str, Any] | None = None

    def post(self, *, payload: Mapping[str, Any], api_key: str) -> Mapping[str, Any]:
        self.dispatch_attempts += 1
        result = self.delegate.post(payload=payload, api_key=api_key)
        self.provider_responses += 1
        if isinstance(result, Mapping):
            self.last_provider_response = result
        return result


def _dry_run_manifest_v02() -> dict[str, Any]:
    manifest = dict(initial_runtime.dry_run_manifest())
    manifest["eval_version"] = EVAL_VERSION
    manifest.pop("manifest_hash", None)
    manifest["manifest_hash"] = canonical_sha256(manifest)
    return manifest


def _structured_output_evidence(
    *,
    tracker: ReplayableDispatchTrackingTransport,
    candidate: Any,
    run: Any,
) -> tuple[str, dict[str, Any] | None, str | None]:
    if tracker.provider_responses != 1 or tracker.last_provider_response is None:
        return "NO_PROVIDER_RESPONSE", None, None
    try:
        call = parse_responses_payload(
            tracker.last_provider_response,
            requested_model=candidate.model,
            latency_ms=run.latency_ms,
        )
        value = json.loads(call.output_text)
        if not isinstance(value, dict):
            return "INVALID_STRUCTURED_OUTPUT_ROOT", None, None
        return "COMPLETE", value, canonical_sha256(value)
    except Exception as exc:
        return f"INCOMPLETE_{type(exc).__name__}", None, None


def _build_paid_call_receipt_v02(**kwargs: Any) -> dict[str, Any]:
    receipt = _legacy_build_paid_call_receipt(**kwargs)
    receipt.pop("receipt_hash", None)
    tracker = kwargs["tracker"]
    candidate = kwargs["candidate"]
    run = kwargs["run"]
    replay_status, structured_output, structured_output_hash = _structured_output_evidence(
        tracker=tracker,
        candidate=candidate,
        run=run,
    )
    receipt["receipt_version"] = PAID_CALL_RECEIPT_VERSION
    receipt["semantic_replay_status"] = replay_status
    receipt["structured_output"] = structured_output
    receipt["structured_output_hash"] = structured_output_hash
    receipt["raw_provider_response_persisted"] = False
    receipt["provider_output_text_persisted"] = False
    receipt["semantic_rescore_requires_new_model_call"] = replay_status != "COMPLETE"
    receipt["receipt_hash"] = canonical_sha256(receipt)
    return receipt


def _patch_runner() -> None:
    # v0.2 is a model-facing semantic clarification plus a stronger evidence contract.
    # The frozen case surface/model ladder/call ceiling are unchanged.
    legacy.INITIAL_EVAL_VERSION = EVAL_VERSION
    legacy.ARTIFACT_VERSION = ARTIFACT_VERSION
    legacy.PAID_AUTHORIZATION_ARTIFACT_VERSION = PAID_AUTHORIZATION_ARTIFACT_VERSION
    legacy.PAID_CALL_RECEIPT_VERSION = PAID_CALL_RECEIPT_VERSION
    legacy.DEFAULT_OUTPUT = DEFAULT_OUTPUT
    legacy.DEFAULT_AUTHORIZATION_OUTPUT = DEFAULT_AUTHORIZATION_OUTPUT
    legacy.DEFAULT_RECEIPT_JOURNAL = DEFAULT_RECEIPT_JOURNAL
    legacy.DispatchTrackingTransport = ReplayableDispatchTrackingTransport
    legacy._build_paid_call_receipt = _build_paid_call_receipt_v02
    legacy.dry_run_manifest = _dry_run_manifest_v02


_patch_runner()


def main() -> int:
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
