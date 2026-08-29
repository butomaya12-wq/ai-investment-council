from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from aic.council.eval_cost import (
    cost_upper_bound_usd,
    load_openai_text_pricing,
    load_stage_eval_plan,
)
from aic.council.model_policy import (
    INITIAL_MODEL_LADDER,
    OUTPUT_TOKEN_BUDGET_VERSION,
    STAGE_MAX_OUTPUT_TOKENS,
    CouncilModelStage,
)
from aic.domain.canonical import canonical_sha256


DEFAULT_REQUEST_PREFLIGHT = Path(".aic-runtime/b4_initial_request_preflight.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_initial_eval_cost_preflight.json")
ARTIFACT_VERSION = "B4_INITIAL_EVAL_COST_PREFLIGHT_ARTIFACT_v0_1"
RUN_CLASS = "B4_LOCAL_ZERO_CALL_INITIAL_MODEL_EVAL_COST_PREFLIGHT"
EXPECTED_REQUEST_PREFLIGHT_VERSION = "B4_INITIAL_REQUEST_PREFLIGHT_ARTIFACT_v0_1"
EVAL_INPUT_BYTE_BUDGET_MULTIPLIER = 2


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read B4 runtime artifact: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"B4 runtime artifact root must be object: {path}")
    return payload


def _verify_artifact_hash(payload: Mapping[str, Any]) -> str:
    actual = payload.get("artifact_hash")
    if not isinstance(actual, str) or len(actual) != 64:
        raise ValueError("B4 initial request preflight artifact_hash missing")
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    if actual != expected:
        raise ValueError("B4 initial request preflight artifact_hash mismatch")
    return actual


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("cost must be finite")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def main() -> int:
    try:
        request_preflight = _read_json(DEFAULT_REQUEST_PREFLIGHT)
        request_preflight_hash = _verify_artifact_hash(request_preflight)
        if request_preflight.get("artifact_version") != EXPECTED_REQUEST_PREFLIGHT_VERSION:
            raise ValueError("unexpected B4 initial request preflight version")
        if request_preflight.get("status") != "READY_FOR_INITIAL_STAGE_MODEL_EVAL_COST_PREFLIGHT":
            raise ValueError("B4 initial request preflight is not cost-preflight ready")
        if request_preflight.get("logical_call_count") != 9:
            raise ValueError("B4 initial request preflight logical-call count drift")
        if request_preflight.get("request_variant_count") != 36:
            raise ValueError("B4 initial request preflight variant count drift")
        if request_preflight.get("initial_model_ladder_count") != len(INITIAL_MODEL_LADDER):
            raise ValueError("B4 initial model ladder count drift")
        for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
            if request_preflight.get(field) != 0:
                raise ValueError(f"B4 request preflight {field} must be zero")
        if request_preflight.get("live_money") != "PROHIBITED":
            raise ValueError("B4 request preflight live-money invariant drift")
        if request_preflight.get("output_token_budget_version") != OUTPUT_TOKEN_BUDGET_VERSION:
            raise ValueError("B4 request preflight output-token budget version drift")

        variants = request_preflight.get("request_variants")
        if not isinstance(variants, list) or len(variants) != 36:
            raise ValueError("B4 initial request variants missing")
        initial_output_cap = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.INITIAL]
        byte_counts: list[int] = []
        for item in variants:
            if not isinstance(item, Mapping):
                raise ValueError("B4 request variant must be object")
            if item.get("max_output_tokens") != initial_output_cap:
                raise ValueError("B4 request variant output cap drift")
            value = item.get("request_body_utf8_bytes")
            if not isinstance(value, int) or value <= 0:
                raise ValueError("B4 request variant byte count invalid")
            byte_counts.append(value)
        max_real_request_body_utf8_bytes = max(byte_counts)
        eval_request_body_utf8_bytes_upper_bound = (
            max_real_request_body_utf8_bytes * EVAL_INPUT_BYTE_BUDGET_MULTIPLIER
        )
        input_tokens_upper_bound_per_call = eval_request_body_utf8_bytes_upper_bound

        eval_plan = load_stage_eval_plan()
        pricing = load_openai_text_pricing()
        stages = eval_plan["stages"]
        initial_plan = stages["INITIAL"]
        eval_case_ids = list(initial_plan["case_ids"])
        calls_per_model_candidate = len(eval_case_ids)
        planned_paid_calls_max = initial_plan["paid_call_count_max"]
        if planned_paid_calls_max != calls_per_model_candidate * len(INITIAL_MODEL_LADDER):
            raise ValueError("B4 initial eval planned paid-call count mismatch")

        long_context = pricing["long_context"]
        threshold = long_context["threshold_input_tokens_exclusive"]
        model_costs: list[dict[str, Any]] = []
        total_cost = Decimal(0)
        for candidate in INITIAL_MODEL_LADDER:
            cost = cost_upper_bound_usd(
                model=candidate.model,
                input_tokens_upper_bound=input_tokens_upper_bound_per_call,
                output_tokens_upper_bound=initial_output_cap,
                call_count=calls_per_model_candidate,
                pricing=pricing,
            )
            total_cost += cost
            model_costs.append(
                {
                    "candidate_key": candidate.candidate_key,
                    "model": candidate.model,
                    "reasoning_effort": candidate.reasoning_effort,
                    "eval_case_count": calls_per_model_candidate,
                    "paid_calls_max": calls_per_model_candidate,
                    "input_tokens_upper_bound_per_call": input_tokens_upper_bound_per_call,
                    "max_output_tokens_per_call": initial_output_cap,
                    "long_context_pricing_applied": input_tokens_upper_bound_per_call > threshold,
                    "cost_upper_bound_usd": _decimal_text(cost),
                }
            )

        sources = sorted(
            {record["source_url"] for record in pricing["models"].values()}
        )
        artifact: dict[str, Any] = {
            "artifact_version": ARTIFACT_VERSION,
            "run_class": RUN_CLASS,
            "status": "REQUIRES_OWNER_COST_APPROVAL_BEFORE_INITIAL_MODEL_EVAL",
            "request_preflight_artifact_hash": request_preflight_hash,
            "b4_input_freeze_artifact_hash": request_preflight["b4_input_freeze_artifact_hash"],
            "b3_reconciliation_artifact_hash": request_preflight["b3_reconciliation_artifact_hash"],
            "b2_handoff_hash": request_preflight["b2_handoff_hash"],
            "mandate_version": request_preflight["mandate_version"],
            "eval_plan_version": eval_plan["plan_version"],
            "eval_plan_hash": eval_plan["plan_hash"],
            "stage": "INITIAL",
            "eval_case_ids": eval_case_ids,
            "eval_case_count": len(eval_case_ids),
            "initial_model_ladder_count": len(INITIAL_MODEL_LADDER),
            "planned_paid_calls_max": planned_paid_calls_max,
            "output_token_budget_version": OUTPUT_TOKEN_BUDGET_VERSION,
            "max_output_tokens_per_call": initial_output_cap,
            "max_real_request_body_utf8_bytes": max_real_request_body_utf8_bytes,
            "eval_input_byte_budget_multiplier": EVAL_INPUT_BYTE_BUDGET_MULTIPLIER,
            "eval_request_body_utf8_bytes_upper_bound": eval_request_body_utf8_bytes_upper_bound,
            "input_tokens_upper_bound_per_call": input_tokens_upper_bound_per_call,
            "input_token_upper_bound_method": (
                "CONSERVATIVE: one input token per UTF-8 request-body byte; entire serialized request body "
                "is counted although not every HTTP JSON byte is model-billed input"
            ),
            "future_eval_dispatch_guard": (
                "FAIL_BEFORE_API_CALL_IF_SERIALIZED_EVAL_REQUEST_EXCEEDS_EVAL_REQUEST_BODY_UTF8_BYTES_UPPER_BOUND"
            ),
            "pricing_version": pricing["pricing_version"],
            "pricing_hash": pricing["pricing_hash"],
            "pricing_as_of_date": pricing["as_of_date"],
            "pricing_source_urls": sources,
            "cached_input_discount_assumed_for_upper_bound": False,
            "long_context_threshold_input_tokens_exclusive": threshold,
            "model_candidate_cost_upper_bounds": model_costs,
            "total_initial_model_eval_cost_upper_bound_usd": _decimal_text(total_cost),
            "owner_cost_approval_required": True,
            "model_calls": 0,
            "provider_reads": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "live_money": "PROHIBITED",
        }
        artifact["artifact_hash"] = canonical_sha256(artifact)
        DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUTPUT.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(json.dumps(artifact, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            f"B4 initial model-eval cost preflight failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
