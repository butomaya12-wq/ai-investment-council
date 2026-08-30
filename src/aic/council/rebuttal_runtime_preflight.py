from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .initial_runtime_cost_v02 import (
    load_initial_runtime_pricing,
    runtime_cost_upper_bound_usd,
)
from .model_policy import CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from .rebuttal_eval_preflight import EXPECTED_PRICING_HASH, EXPECTED_PRICING_VERSION
from .rebuttal_model_selection_v02 import verify_rebuttal_selected_model_authority_v02
from .rebuttal_preflight import (
    EXPECTED_PRODUCTION_REBUTTAL_CALLS,
    REBUTTAL_SOURCE_PREFLIGHT_STATUS,
)
from .rebuttal_schema_repair_v01 import (
    REBUTTAL_CLAIM_TYPE_CONTRACT_VERSION,
    REBUTTAL_OPPOSING_LANE_CONTRACT_VERSION,
    REBUTTAL_PROMOTION_SEMANTICS_CONTRACT_VERSION,
    REBUTTAL_SCHEMA_REPAIR_VERSION,
    REBUTTAL_SCHEMA_VERSION,
)


REBUTTAL_RUNTIME_REQUEST_PREFLIGHT_VERSION = "B4_REBUTTAL_RUNTIME_REQUEST_PREFLIGHT_v0_1"
REBUTTAL_RUNTIME_REQUEST_PREFLIGHT_STATUS = "PASS_ZERO_CALL_REBUTTAL_RUNTIME_REQUEST_PREFLIGHT"
REBUTTAL_RUNTIME_COST_PREFLIGHT_VERSION = "B4_REBUTTAL_RUNTIME_COST_PREFLIGHT_v0_1"
REBUTTAL_RUNTIME_COST_PREFLIGHT_STATUS = (
    "REQUIRES_EXPLICIT_OWNER_APPROVAL_BEFORE_PRODUCTION_REBUTTAL"
)
EXPECTED_INITIAL_FREEZE_HASH = (
    "ca7391e5e0c3a754eabc54fbf959b0f36e0986b552d405a06cf649116135361f"
)
EXPECTED_SELECTION_HASH = (
    "8db38779171e0dcfc2e0325581192116b17adf98a1140950ffcbe5ce4698a882"
)
EXPECTED_SOURCE_REQUEST_MANIFEST = (
    "1bbe906cc553150e64ed69c6414f6b337ce06196019c455e59813e2da42dd066"
)
EXPECTED_SELECTED = {
    "candidate_key": "R3",
    "model": "gpt-5.6-sol",
    "reasoning_effort": "medium",
    "ladder_position": 3,
}


class RebuttalRuntimePreflightError(ValueError):
    pass


def _require_sha(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise RebuttalRuntimePreflightError(f"{label} must be lowercase sha256")
    return value


def _verify_source_preflight(
    source: Mapping[str, Any],
    *,
    code_commit_sha: str,
) -> str:
    artifact_hash = _require_sha(source.get("artifact_hash"), label="source artifact_hash")
    if artifact_hash != canonical_sha256(source, exclude_fields=("artifact_hash",)):
        raise RebuttalRuntimePreflightError("Rebuttal source-preflight canonical hash mismatch")
    if source.get("status") != REBUTTAL_SOURCE_PREFLIGHT_STATUS:
        raise RebuttalRuntimePreflightError("Rebuttal source preflight is not PASS")
    if source.get("code_commit_sha") != code_commit_sha:
        raise RebuttalRuntimePreflightError("Rebuttal source preflight HEAD drift")
    if source.get("initial_council_freeze_artifact_hash") != EXPECTED_INITIAL_FREEZE_HASH:
        raise RebuttalRuntimePreflightError("Rebuttal source preflight Initial-freeze drift")
    if source.get("request_manifest_hash") != EXPECTED_SOURCE_REQUEST_MANIFEST:
        raise RebuttalRuntimePreflightError("Rebuttal source request manifest drift")
    if source.get("production_rebuttal_calls_after_selection") != EXPECTED_PRODUCTION_REBUTTAL_CALLS:
        raise RebuttalRuntimePreflightError("production Rebuttal topology drift")
    if source.get("request_variant_count") != 9:
        raise RebuttalRuntimePreflightError("source preflight must retain 3x3 request surface")
    if source.get("schema_repair_version") != REBUTTAL_SCHEMA_REPAIR_VERSION:
        raise RebuttalRuntimePreflightError("Rebuttal schema repair version drift")
    if source.get("schema_version") != REBUTTAL_SCHEMA_VERSION:
        raise RebuttalRuntimePreflightError("Rebuttal schema version drift")
    if (
        source.get("promotion_semantics_contract_version")
        != REBUTTAL_PROMOTION_SEMANTICS_CONTRACT_VERSION
    ):
        raise RebuttalRuntimePreflightError("Rebuttal promotion semantics drift")
    if source.get("opposing_lane_contract_version") != REBUTTAL_OPPOSING_LANE_CONTRACT_VERSION:
        raise RebuttalRuntimePreflightError("Rebuttal opposing-lane contract drift")
    if source.get("claim_type_contract_version") != REBUTTAL_CLAIM_TYPE_CONTRACT_VERSION:
        raise RebuttalRuntimePreflightError("Rebuttal claim-type contract drift")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if source.get(field) != 0:
            raise RebuttalRuntimePreflightError(f"source preflight zero-call invariant violated: {field}")
    if source.get("live_money") != "PROHIBITED":
        raise RebuttalRuntimePreflightError("source preflight live-money invariant drift")
    if source.get("production_rebuttal_authorized") is not False:
        raise RebuttalRuntimePreflightError("source preflight unexpectedly authorizes production Rebuttal")
    if source.get("judge_authorized") is not False:
        raise RebuttalRuntimePreflightError("source preflight unexpectedly authorizes Judge")
    return artifact_hash


def build_rebuttal_runtime_request_preflight(
    *,
    source_preflight: Mapping[str, Any],
    selection_authority: Mapping[str, Any],
    code_commit_sha: str,
) -> dict[str, Any]:
    if (
        len(code_commit_sha) != 40
        or any(ch not in "0123456789abcdef" for ch in code_commit_sha)
    ):
        raise RebuttalRuntimePreflightError("runtime preflight requires exact lowercase git SHA")
    source_hash = _verify_source_preflight(source_preflight, code_commit_sha=code_commit_sha)
    selection_hash = verify_rebuttal_selected_model_authority_v02(selection_authority)
    if selection_hash != EXPECTED_SELECTION_HASH:
        raise RebuttalRuntimePreflightError("Rebuttal selected-model authority hash drift")
    if selection_authority.get("selected_candidate") != EXPECTED_SELECTED:
        raise RebuttalRuntimePreflightError("production Rebuttal selected candidate is not frozen R3")
    if selection_authority.get("model_eval_artifact_hash") != (
        "1533a224f9a0c85abb77f42526aeed24e76c7e0453bc85cc5c8f8881669ae414"
    ):
        raise RebuttalRuntimePreflightError("selected-model authority eval binding drift")
    if selection_authority.get("production_rebuttal_authorized") is not False:
        raise RebuttalRuntimePreflightError("selection authority unexpectedly authorizes production")
    if selection_authority.get("judge_authorized") is not False:
        raise RebuttalRuntimePreflightError("selection authority unexpectedly authorizes Judge")

    candidate_order = source_preflight.get("candidate_order")
    if candidate_order != ["NVDA", "MSFT", "META"]:
        raise RebuttalRuntimePreflightError("production Rebuttal candidate order drift")
    raw_variants = source_preflight.get("request_variants")
    if not isinstance(raw_variants, list) or len(raw_variants) != 9:
        raise RebuttalRuntimePreflightError("source request variants missing")
    selected: list[dict[str, Any]] = []
    for candidate_id in candidate_order:
        matches = [
            item
            for item in raw_variants
            if isinstance(item, Mapping)
            and item.get("candidate") == candidate_id
            and item.get("candidate_key") == EXPECTED_SELECTED["candidate_key"]
        ]
        if len(matches) != 1:
            raise RebuttalRuntimePreflightError(
                f"{candidate_id} must have exactly one frozen R3 production request"
            )
        row = dict(matches[0])
        if row.get("model") != EXPECTED_SELECTED["model"]:
            raise RebuttalRuntimePreflightError("selected production model drift")
        if row.get("reasoning_effort") != EXPECTED_SELECTED["reasoning_effort"]:
            raise RebuttalRuntimePreflightError("selected reasoning effort drift")
        if row.get("max_output_tokens") != STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.REBUTTAL]:
            raise RebuttalRuntimePreflightError("production Rebuttal output-token cap drift")
        _require_sha(row.get("request_hash"), label=f"{candidate_id} request_hash")
        byte_count = row.get("request_body_utf8_bytes")
        if type(byte_count) is not int or byte_count <= 0:
            raise RebuttalRuntimePreflightError("production request byte count invalid")
        selected.append(row)

    if len(selected) != EXPECTED_PRODUCTION_REBUTTAL_CALLS:
        raise RebuttalRuntimePreflightError("production Rebuttal requires exactly three selected requests")
    manifest_hash = canonical_sha256(
        {
            "variants": [
                {
                    "candidate": row["candidate"],
                    "candidate_key": row["candidate_key"],
                    "request_hash": row["request_hash"],
                    "request_body_utf8_bytes": row["request_body_utf8_bytes"],
                }
                for row in selected
            ]
        }
    )
    artifact: dict[str, Any] = {
        "artifact_version": REBUTTAL_RUNTIME_REQUEST_PREFLIGHT_VERSION,
        "status": REBUTTAL_RUNTIME_REQUEST_PREFLIGHT_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_request_preflight_artifact_hash": source_hash,
        "source_request_manifest_hash": source_preflight["request_manifest_hash"],
        "initial_council_freeze_artifact_hash": EXPECTED_INITIAL_FREEZE_HASH,
        "selected_model_authority_selection_hash": selection_hash,
        "selected_model_eval_artifact_hash": selection_authority["model_eval_artifact_hash"],
        "selected_candidate": dict(EXPECTED_SELECTED),
        "candidate_order": list(candidate_order),
        "logical_call_count": EXPECTED_PRODUCTION_REBUTTAL_CALLS,
        "planned_paid_calls_max": EXPECTED_PRODUCTION_REBUTTAL_CALLS,
        "automatic_repair_calls_authorized": False,
        "selected_request_variants": selected,
        "request_manifest_hash": manifest_hash,
        "max_request_body_utf8_bytes": max(row["request_body_utf8_bytes"] for row in selected),
        "max_output_tokens_per_call": STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.REBUTTAL],
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "production_rebuttal_authorized": False,
        "judge_authorized": False,
        "rerun_authorized": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_rebuttal_runtime_request_preflight(payload: Mapping[str, Any]) -> str:
    artifact_hash = _require_sha(payload.get("artifact_hash"), label="runtime request artifact_hash")
    if artifact_hash != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise RebuttalRuntimePreflightError("runtime request-preflight hash mismatch")
    if payload.get("artifact_version") != REBUTTAL_RUNTIME_REQUEST_PREFLIGHT_VERSION:
        raise RebuttalRuntimePreflightError("unexpected runtime request-preflight version")
    if payload.get("status") != REBUTTAL_RUNTIME_REQUEST_PREFLIGHT_STATUS:
        raise RebuttalRuntimePreflightError("runtime request preflight is not PASS")
    if payload.get("selected_model_authority_selection_hash") != EXPECTED_SELECTION_HASH:
        raise RebuttalRuntimePreflightError("runtime request selected-model authority drift")
    if payload.get("selected_candidate") != EXPECTED_SELECTED:
        raise RebuttalRuntimePreflightError("runtime request selected candidate drift")
    if payload.get("planned_paid_calls_max") != EXPECTED_PRODUCTION_REBUTTAL_CALLS:
        raise RebuttalRuntimePreflightError("runtime request paid-call ceiling drift")
    if payload.get("automatic_repair_calls_authorized") is not False:
        raise RebuttalRuntimePreflightError("runtime request automatic repair unexpectedly authorized")
    variants = payload.get("selected_request_variants")
    if not isinstance(variants, list) or len(variants) != EXPECTED_PRODUCTION_REBUTTAL_CALLS:
        raise RebuttalRuntimePreflightError("runtime request must contain exactly three variants")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise RebuttalRuntimePreflightError(f"runtime request zero-call invariant violated: {field}")
    if payload.get("live_money") != "PROHIBITED":
        raise RebuttalRuntimePreflightError("runtime request live-money invariant drift")
    if payload.get("production_rebuttal_authorized") is not False:
        raise RebuttalRuntimePreflightError("runtime request unexpectedly authorizes production")
    if payload.get("judge_authorized") is not False or payload.get("rerun_authorized") is not False:
        raise RebuttalRuntimePreflightError("runtime request unexpectedly authorizes Judge/rerun")
    return artifact_hash


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def build_rebuttal_runtime_cost_preflight(
    request_preflight: Mapping[str, Any],
    *,
    pricing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    request_hash = verify_rebuttal_runtime_request_preflight(request_preflight)
    pricing = dict(pricing or load_initial_runtime_pricing())
    pricing_hash = _require_sha(pricing.get("pricing_hash"), label="pricing_hash")
    if pricing.get("pricing_version") != EXPECTED_PRICING_VERSION or pricing_hash != EXPECTED_PRICING_HASH:
        raise RebuttalRuntimePreflightError("production Rebuttal pricing authority drift")
    if pricing_hash != canonical_sha256(pricing, exclude_fields=("pricing_hash",)):
        raise RebuttalRuntimePreflightError("production Rebuttal pricing hash mismatch")
    cache_write = pricing.get("cache_write")
    if not isinstance(cache_write, Mapping) or cache_write.get("input_rate_multiplier") != "1.25":
        raise RebuttalRuntimePreflightError("production Rebuttal cache-write pricing drift")

    variants = request_preflight.get("selected_request_variants")
    if not isinstance(variants, list) or len(variants) != EXPECTED_PRODUCTION_REBUTTAL_CALLS:
        raise RebuttalRuntimePreflightError("production Rebuttal request variants missing")
    output_cap = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.REBUTTAL]
    rows: list[dict[str, Any]] = []
    total = Decimal("0")
    for item in variants:
        if not isinstance(item, Mapping):
            raise RebuttalRuntimePreflightError("production request variant malformed")
        model = item.get("model")
        byte_count = item.get("request_body_utf8_bytes")
        if model != EXPECTED_SELECTED["model"] or type(byte_count) is not int or byte_count <= 0:
            raise RebuttalRuntimePreflightError("production cost inputs invalid")
        cost = runtime_cost_upper_bound_usd(
            model=model,
            input_tokens_upper_bound=byte_count,
            output_tokens_upper_bound=output_cap,
            call_count=1,
            pricing=pricing,
        )
        total += cost
        rows.append(
            {
                "candidate": item["candidate"],
                "model": model,
                "request_body_utf8_bytes": byte_count,
                "input_tokens_upper_bound": byte_count,
                "max_output_tokens": output_cap,
                "cost_upper_bound_usd": _decimal_text(cost),
            }
        )

    artifact: dict[str, Any] = {
        "artifact_version": REBUTTAL_RUNTIME_COST_PREFLIGHT_VERSION,
        "status": REBUTTAL_RUNTIME_COST_PREFLIGHT_STATUS,
        "code_commit_sha": request_preflight["code_commit_sha"],
        "runtime_request_preflight_artifact_hash": request_hash,
        "runtime_request_manifest_hash": request_preflight["request_manifest_hash"],
        "source_request_preflight_artifact_hash": request_preflight["source_request_preflight_artifact_hash"],
        "source_request_manifest_hash": request_preflight["source_request_manifest_hash"],
        "initial_council_freeze_artifact_hash": EXPECTED_INITIAL_FREEZE_HASH,
        "selected_model_authority_selection_hash": EXPECTED_SELECTION_HASH,
        "selected_candidate": dict(EXPECTED_SELECTED),
        "planned_paid_calls_max": EXPECTED_PRODUCTION_REBUTTAL_CALLS,
        "automatic_repair_calls_authorized": False,
        "max_request_body_utf8_bytes": request_preflight["max_request_body_utf8_bytes"],
        "input_token_upper_bound_method": (
            "CONSERVATIVE: one input token per UTF-8 serialized request-body byte; "
            "all input tokens additionally assumed eligible for GPT-5.6 cache-write billing"
        ),
        "max_output_tokens_per_call": output_cap,
        "pricing_version": pricing["pricing_version"],
        "pricing_hash": pricing_hash,
        "pricing_as_of_date": pricing["as_of_date"],
        "cache_write_input_rate_multiplier": cache_write["input_rate_multiplier"],
        "cache_write_usage_field": cache_write["usage_field"],
        "worst_case_all_input_tokens_as_cache_write_assumed": True,
        "cached_input_discount_assumed_for_upper_bound": False,
        "per_call_cost_upper_bounds": rows,
        "total_rebuttal_runtime_cost_upper_bound_usd": _decimal_text(total),
        "owner_cost_approval_required": True,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "production_rebuttal_authorized": False,
        "judge_authorized": False,
        "rerun_authorized": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_rebuttal_runtime_cost_preflight(payload: Mapping[str, Any]) -> str:
    artifact_hash = _require_sha(payload.get("artifact_hash"), label="runtime cost artifact_hash")
    if artifact_hash != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise RebuttalRuntimePreflightError("runtime cost-preflight hash mismatch")
    if payload.get("artifact_version") != REBUTTAL_RUNTIME_COST_PREFLIGHT_VERSION:
        raise RebuttalRuntimePreflightError("unexpected runtime cost-preflight version")
    if payload.get("status") != REBUTTAL_RUNTIME_COST_PREFLIGHT_STATUS:
        raise RebuttalRuntimePreflightError("runtime cost preflight status drift")
    if payload.get("selected_model_authority_selection_hash") != EXPECTED_SELECTION_HASH:
        raise RebuttalRuntimePreflightError("runtime cost selected-model authority drift")
    if payload.get("planned_paid_calls_max") != EXPECTED_PRODUCTION_REBUTTAL_CALLS:
        raise RebuttalRuntimePreflightError("runtime cost paid-call ceiling drift")
    if payload.get("owner_cost_approval_required") is not True:
        raise RebuttalRuntimePreflightError("runtime cost owner approval requirement missing")
    if payload.get("automatic_repair_calls_authorized") is not False:
        raise RebuttalRuntimePreflightError("runtime cost automatic repair unexpectedly authorized")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise RebuttalRuntimePreflightError(f"runtime cost zero-call invariant violated: {field}")
    if payload.get("live_money") != "PROHIBITED":
        raise RebuttalRuntimePreflightError("runtime cost live-money invariant drift")
    if payload.get("production_rebuttal_authorized") is not False:
        raise RebuttalRuntimePreflightError("runtime cost unexpectedly authorizes production")
    if payload.get("judge_authorized") is not False or payload.get("rerun_authorized") is not False:
        raise RebuttalRuntimePreflightError("runtime cost unexpectedly authorizes Judge/rerun")
    return artifact_hash
