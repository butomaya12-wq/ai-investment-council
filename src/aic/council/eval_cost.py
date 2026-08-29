from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .model_policy import MODEL_LADDERS, CouncilModelStage


DEFAULT_EVAL_PLAN_PATH = Path("config/event/b4_stage_eval_plan_v1.json")
DEFAULT_PRICING_PATH = Path("config/event/openai_text_pricing_2026_08_29.json")
EXPECTED_EVAL_PLAN_VERSION = "B4_STAGE_EVAL_PLAN_v0_1"
EXPECTED_PRICING_VERSION = "OPENAI_TEXT_PRICING_2026_08_29"
EXPECTED_REPRESENTATIVE_CASES = tuple(f"E{i}" for i in range(1, 17))


class B4EvalCostAuthorityError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4EvalCostAuthorityError(f"unable to read B4 authority: {path}") from exc
    if not isinstance(payload, dict):
        raise B4EvalCostAuthorityError(f"B4 authority root must be object: {path}")
    return payload


def _verify_self_hash(payload: Mapping[str, Any], field_name: str) -> str:
    actual = payload.get(field_name)
    if not isinstance(actual, str) or len(actual) != 64:
        raise B4EvalCostAuthorityError(f"{field_name} missing")
    expected = canonical_sha256(payload, exclude_fields=(field_name,))
    if actual != expected:
        raise B4EvalCostAuthorityError(f"{field_name} mismatch")
    return actual


def load_stage_eval_plan(path: Path = DEFAULT_EVAL_PLAN_PATH) -> dict[str, Any]:
    payload = _read_json(path)
    _verify_self_hash(payload, "plan_hash")
    if payload.get("plan_version") != EXPECTED_EVAL_PLAN_VERSION:
        raise B4EvalCostAuthorityError("unexpected B4 stage eval plan version")
    if tuple(payload.get("representative_eval_case_ids", ())) != EXPECTED_REPRESENTATIVE_CASES:
        raise B4EvalCostAuthorityError("B4 representative eval case surface drift")
    if payload.get("selection_rule") != "LOWEST_COST_PASSING_CONFIG_THEN_LATENCY_THEN_TOKENS":
        raise B4EvalCostAuthorityError("B4 model-selection rule drift")
    if payload.get("owner_threshold_required") is not False:
        raise B4EvalCostAuthorityError("B4 eval plan must not invent an owner threshold")

    stages = payload.get("stages")
    if not isinstance(stages, Mapping) or set(stages) != {"INITIAL", "REBUTTAL", "JUDGE"}:
        raise B4EvalCostAuthorityError("B4 stage eval plan requires exact three stages")
    paid_total = 0
    covered: set[str] = set()
    for stage in CouncilModelStage:
        item = stages.get(stage.value)
        if not isinstance(item, Mapping):
            raise B4EvalCostAuthorityError(f"B4 eval stage missing: {stage.value}")
        case_ids = item.get("case_ids")
        candidate_keys = item.get("candidate_keys")
        if not isinstance(case_ids, list) or not case_ids or len(set(case_ids)) != len(case_ids):
            raise B4EvalCostAuthorityError(f"{stage.value} eval case IDs invalid")
        if any(case_id not in EXPECTED_REPRESENTATIVE_CASES for case_id in case_ids):
            raise B4EvalCostAuthorityError(f"{stage.value} eval case outside E1-E16")
        expected_keys = [candidate.candidate_key for candidate in MODEL_LADDERS[stage]]
        if candidate_keys != expected_keys:
            raise B4EvalCostAuthorityError(f"{stage.value} eval candidate ladder drift")
        expected_calls = len(case_ids) * len(candidate_keys)
        if item.get("paid_call_count_max") != expected_calls:
            raise B4EvalCostAuthorityError(f"{stage.value} paid-call count mismatch")
        paid_total += expected_calls
        covered.update(case_ids)
    if covered != set(EXPECTED_REPRESENTATIVE_CASES):
        raise B4EvalCostAuthorityError("B4 stage eval plan does not cover E1-E16")
    if payload.get("full_eval_paid_call_count_max") != paid_total:
        raise B4EvalCostAuthorityError("B4 full eval paid-call total mismatch")
    return payload


def _decimal(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise B4EvalCostAuthorityError(f"{field_name} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise B4EvalCostAuthorityError(f"{field_name} invalid decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise B4EvalCostAuthorityError(f"{field_name} must be finite and non-negative")
    return parsed


def load_openai_text_pricing(path: Path = DEFAULT_PRICING_PATH) -> dict[str, Any]:
    payload = _read_json(path)
    _verify_self_hash(payload, "pricing_hash")
    if payload.get("pricing_version") != EXPECTED_PRICING_VERSION:
        raise B4EvalCostAuthorityError("unexpected OpenAI pricing version")
    if payload.get("unit") != "USD_PER_1M_TEXT_TOKENS":
        raise B4EvalCostAuthorityError("OpenAI pricing unit drift")
    if payload.get("upper_bound_cached_input_discount_assumed") is not False:
        raise B4EvalCostAuthorityError("cost upper bound must not assume cached-input discount")
    models = payload.get("models")
    if not isinstance(models, Mapping):
        raise B4EvalCostAuthorityError("OpenAI pricing models missing")
    required_models = {candidate.model for ladder in MODEL_LADDERS.values() for candidate in ladder}
    if not required_models.issubset(set(models)):
        raise B4EvalCostAuthorityError("OpenAI pricing does not cover frozen B4 model ladders")
    for model in required_models:
        item = models.get(model)
        if not isinstance(item, Mapping):
            raise B4EvalCostAuthorityError(f"pricing record missing for {model}")
        _decimal(item.get("input"), field_name=f"{model}.input")
        _decimal(item.get("cached_input"), field_name=f"{model}.cached_input")
        _decimal(item.get("output"), field_name=f"{model}.output")
        url = item.get("source_url")
        if not isinstance(url, str) or url != f"https://developers.openai.com/api/docs/models/{model}":
            raise B4EvalCostAuthorityError(f"official pricing source URL drift for {model}")
    long_context = payload.get("long_context")
    if not isinstance(long_context, Mapping):
        raise B4EvalCostAuthorityError("long-context pricing authority missing")
    threshold = long_context.get("threshold_input_tokens_exclusive")
    if not isinstance(threshold, int) or threshold <= 0:
        raise B4EvalCostAuthorityError("long-context threshold invalid")
    _decimal(long_context.get("input_multiplier"), field_name="long_context.input_multiplier")
    _decimal(long_context.get("output_multiplier"), field_name="long_context.output_multiplier")
    return payload


def cost_upper_bound_usd(
    *,
    model: str,
    input_tokens_upper_bound: int,
    output_tokens_upper_bound: int,
    call_count: int,
    pricing: Mapping[str, Any],
) -> Decimal:
    if input_tokens_upper_bound < 0 or output_tokens_upper_bound < 0 or call_count < 0:
        raise B4EvalCostAuthorityError("cost upper-bound counters must be non-negative")
    models = pricing.get("models")
    if not isinstance(models, Mapping) or not isinstance(models.get(model), Mapping):
        raise B4EvalCostAuthorityError(f"missing pricing for {model}")
    record = models[model]
    input_rate = _decimal(record.get("input"), field_name=f"{model}.input")
    output_rate = _decimal(record.get("output"), field_name=f"{model}.output")

    long_context = pricing.get("long_context")
    if not isinstance(long_context, Mapping):
        raise B4EvalCostAuthorityError("long-context pricing authority missing")
    threshold = long_context.get("threshold_input_tokens_exclusive")
    if not isinstance(threshold, int):
        raise B4EvalCostAuthorityError("long-context threshold invalid")
    if input_tokens_upper_bound > threshold:
        input_rate *= _decimal(long_context.get("input_multiplier"), field_name="long_context.input_multiplier")
        output_rate *= _decimal(long_context.get("output_multiplier"), field_name="long_context.output_multiplier")

    million = Decimal(1_000_000)
    per_call = (
        Decimal(input_tokens_upper_bound) * input_rate
        + Decimal(output_tokens_upper_bound) * output_rate
    ) / million
    return Decimal(call_count) * per_call
