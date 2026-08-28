from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .decimal_math import decimal_sum
from .screening import MetricDirection, ScreeningPolicy


APPROVED_UNIVERSE_ID = "DEMO_UNIVERSE_V1"
APPROVED_POLICY_VERSION = "SCREENING_POLICY_V1"
APPROVED_SYMBOLS = ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "AMD")
APPROVED_DIMENSIONS = (
    "return_20s",
    "max_drawdown_20s",
    "adv_20s",
    "annual_revenue_growth",
    "annual_operating_margin",
)
APPROVED_WEIGHT = Decimal("0.20")


class B2ConfigError(ValueError):
    pass


def _read_json_object(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B2ConfigError(f"cannot read JSON config: {source}") from exc
    if not isinstance(payload, dict):
        raise B2ConfigError("config root must be a JSON object")
    return payload


def load_demo_universe(path: str | Path) -> tuple[str, ...]:
    payload = _read_json_object(path)
    if set(payload) != {"universe_id", "symbols"}:
        raise B2ConfigError("demo universe config has unexpected fields")
    if payload["universe_id"] != APPROVED_UNIVERSE_ID:
        raise B2ConfigError("unexpected demo universe id")
    symbols = payload["symbols"]
    if not isinstance(symbols, list):
        raise B2ConfigError("symbols must be a JSON array")
    normalized: list[str] = []
    for value in symbols:
        if type(value) is not str:
            raise B2ConfigError("every symbol must be a JSON string")
        if not value or value != value.strip() or value != value.upper():
            raise B2ConfigError("symbols must be canonical uppercase tickers")
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise B2ConfigError("demo universe symbols must be unique")
    if tuple(normalized) != APPROVED_SYMBOLS:
        raise B2ConfigError("demo universe does not match owner-approved DEMO_UNIVERSE_V1")
    return tuple(normalized)


def load_screening_policy(path: str | Path) -> ScreeningPolicy:
    payload = _read_json_object(path)
    expected_fields = {
        "policy_version",
        "universe_ref",
        "required_dimensions",
        "metric_directions",
        "normalization_method",
        "weights",
        "missing_value_rule",
        "shortlist_size",
        "final_candidate_count",
    }
    if set(payload) != expected_fields:
        raise B2ConfigError("screening policy config has unexpected fields")
    weights = payload.get("weights")
    if not isinstance(weights, dict):
        raise B2ConfigError("weights must be a JSON object")
    parsed_weights: dict[str, Decimal] = {}
    for dimension, raw_weight in weights.items():
        if type(dimension) is not str or type(raw_weight) is not str:
            raise B2ConfigError("screening weights must be decimal strings")
        try:
            parsed_weights[dimension] = Decimal(raw_weight)
        except Exception as exc:
            raise B2ConfigError(f"invalid decimal weight for {dimension}") from exc

    normalized = dict(payload)
    normalized["weights"] = parsed_weights
    try:
        policy = ScreeningPolicy.model_validate(normalized)
    except Exception as exc:
        raise B2ConfigError("invalid screening policy") from exc

    expected_directions = {
        dimension: MetricDirection.HIGHER_IS_BETTER for dimension in APPROVED_DIMENSIONS
    }
    if policy.policy_version != APPROVED_POLICY_VERSION:
        raise B2ConfigError("unexpected screening policy version")
    if policy.universe_ref != APPROVED_UNIVERSE_ID:
        raise B2ConfigError("screening policy must bind DEMO_UNIVERSE_V1")
    if policy.required_dimensions != APPROVED_DIMENSIONS:
        raise B2ConfigError("screening dimensions differ from owner-approved policy")
    if dict(policy.metric_directions) != expected_directions:
        raise B2ConfigError("screening directions differ from owner-approved policy")
    if policy.weights is None or set(policy.weights) != set(APPROVED_DIMENSIONS):
        raise B2ConfigError("screening weights do not cover approved dimensions")
    if any(policy.weights[dimension] != APPROVED_WEIGHT for dimension in APPROVED_DIMENSIONS):
        raise B2ConfigError("screening weights differ from owner-approved 0.20 each")
    if decimal_sum(policy.weights.values()) != Decimal("1.00"):
        raise B2ConfigError("SCREENING_POLICY_V1 weights must sum exactly to 1.00")
    if policy.shortlist_size != 5 or policy.final_candidate_count != 3:
        raise B2ConfigError("screening shortlist/final counts differ from owner approval")
    return policy
