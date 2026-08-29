from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import DECISION_LIFECYCLE_POLICY_V1, INVESTMENT_MANDATE_V1


DEFAULT_MANDATE_PATH = Path("config/event/investment_mandate_competition_v1.json")
DEFAULT_OPTIONS_POLICY_PATH = Path("config/event/competition_v1_options_policy.json")
DEFAULT_LIFECYCLE_POLICY_PATH = Path("config/event/decision_lifecycle_policy_competition_v1.json")

COMPETITION_MANDATE_VERSION = "ALPACA_COMPETITION_V1_2026_08_29"
COMPETITION_MANDATE_HASH = "9b8f55c8eedc3e6b5202a42cf9c8e47b3eba7b08607e15ac259b16c235ad5dce"
COMPETITION_OPTIONS_POLICY_HASH = "a4e5f95746cf1e928069454e23bd0bf76e92afe38208c4d8cc0c9cb7a16f00a6"
COMPETITION_LIFECYCLE_POLICY_HASH = "cceb58997b139b59039313d892ff330915ff49f2a5a236bcdb57141501bd98ce"
EXPECTED_UNIVERSE = ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "AMD")


class CompetitionPolicyError(ValueError):
    pass


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompetitionPolicyError(f"unable to read competition policy artifact: {path}") from exc
    if not isinstance(payload, dict):
        raise CompetitionPolicyError(f"competition policy root must be an object: {path}")
    return payload


def load_competition_options_policy(
    path: Path = DEFAULT_OPTIONS_POLICY_PATH,
) -> dict[str, Any]:
    payload = _read_json_object(path)
    actual_hash = payload.get("policy_hash")
    if actual_hash != COMPETITION_OPTIONS_POLICY_HASH:
        raise CompetitionPolicyError("competition options policy hash does not match owner freeze")
    if canonical_sha256(payload, exclude_fields=("policy_hash",)) != actual_hash:
        raise CompetitionPolicyError("competition options policy self-hash is invalid")
    if payload.get("version") != COMPETITION_MANDATE_VERSION:
        raise CompetitionPolicyError("competition options policy version drift")
    if payload.get("owner_id") != "MAYA" or payload.get("active") is not True:
        raise CompetitionPolicyError("competition options policy owner/active state drift")
    if payload.get("strategy_allowlist") != ["SINGLE_LEG_LONG_CALL_ONLY"]:
        raise CompetitionPolicyError("competition option strategy allowlist drift")
    selector = payload.get("selector")
    risk = payload.get("risk")
    order = payload.get("order")
    commit = payload.get("commit_revalidation")
    if not all(isinstance(item, dict) for item in (selector, risk, order, commit)):
        raise CompetitionPolicyError("competition options policy sections are incomplete")
    expected_selector = {
        "dte_min_calendar_days": 21,
        "dte_max_calendar_days": 49,
        "dte_target_calendar_days": 35,
        "delta_min": "0.45",
        "delta_max": "0.60",
        "delta_target": "0.50",
        "max_relative_spread": "0.10",
        "min_open_interest": 100,
        "selection_quote_max_age_seconds": 60,
        "standard_contract_size": 100,
    }
    for key, value in expected_selector.items():
        if selector.get(key) != value:
            raise CompetitionPolicyError(f"competition selector drift: {key}")
    expected_risk = {
        "max_new_position_premium_at_risk_fraction_of_equity": "0.03",
        "max_same_underlying_premium_at_risk_fraction_of_equity": "0.03",
        "max_aggregate_open_long_option_premium_at_risk_fraction_of_equity": "0.06",
        "min_post_proposal_equity_safety_reserve_fraction": "0.50",
        "max_contracts_per_new_order": 2,
    }
    for key, value in expected_risk.items():
        if risk.get(key) != value:
            raise CompetitionPolicyError(f"competition risk policy drift: {key}")
    if order.get("environment") != "PAPER" or order.get("order_type") != "LIMIT":
        raise CompetitionPolicyError("competition option order semantics drift")
    if order.get("human_approval_required") is not True or order.get("exactly_one_send") is not True:
        raise CompetitionPolicyError("competition approval/exactly-one-send invariant drift")
    if commit.get("quote_max_age_seconds") != 15:
        raise CompetitionPolicyError("competition commit quote freshness drift")
    if payload.get("live_execution") is not False or payload.get("broker_write_authority") is not False:
        raise CompetitionPolicyError("competition policy must not grant live/broker-write authority")
    return payload


def load_competition_investment_mandate(
    path: Path = DEFAULT_MANDATE_PATH,
    *,
    options_policy_path: Path = DEFAULT_OPTIONS_POLICY_PATH,
):
    payload = _read_json_object(path)
    mandate = INVESTMENT_MANDATE_V1.model_validate(payload)
    if mandate.version != COMPETITION_MANDATE_VERSION:
        raise CompetitionPolicyError("competition mandate version drift")
    if mandate.mandate_hash != COMPETITION_MANDATE_HASH:
        raise CompetitionPolicyError("competition mandate hash does not match owner freeze")
    if tuple(mandate.allowed_universe) != EXPECTED_UNIVERSE:
        raise CompetitionPolicyError("competition mandate universe drift")
    if mandate.execution_mode != "PAPER_APPROVAL_REQUIRED" or mandate.live_execution is not False:
        raise CompetitionPolicyError("competition mandate execution-mode drift")
    options_policy = load_competition_options_policy(options_policy_path)
    base_ref = (
        "ALPACA_COMPETITION_OPTIONS_POLICY:"
        f"{options_policy['policy_id']}:{options_policy['version']}:{options_policy['policy_hash']}"
    )
    expected_refs = {
        "risk_budget_policy_ref": base_ref + "#max_new_position_premium_at_risk",
        "max_single_name_policy_ref": base_ref + "#max_same_underlying_premium_at_risk",
        "concentration_policy_ref": base_ref + "#max_aggregate_open_long_option_premium_at_risk",
    }
    for field_name, expected in expected_refs.items():
        if getattr(mandate, field_name) != expected:
            raise CompetitionPolicyError(f"competition mandate policy lineage drift: {field_name}")
    return mandate


def load_competition_decision_lifecycle_policy(
    path: Path = DEFAULT_LIFECYCLE_POLICY_PATH,
):
    payload = _read_json_object(path)
    policy = DECISION_LIFECYCLE_POLICY_V1.model_validate(payload)
    if policy.version != COMPETITION_MANDATE_VERSION:
        raise CompetitionPolicyError("competition lifecycle policy version drift")
    if policy.policy_hash != COMPETITION_LIFECYCLE_POLICY_HASH:
        raise CompetitionPolicyError("competition lifecycle policy hash does not match owner freeze")
    if policy.active is not True or policy.decision_ttl_seconds != 7200:
        raise CompetitionPolicyError("competition lifecycle policy value drift")
    return policy
