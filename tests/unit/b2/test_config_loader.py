import json
from decimal import Decimal

import pytest

from aic.b2.config_loader import B2ConfigError, load_demo_universe, load_screening_policy


def test_owner_approved_event_configs_load_from_repo() -> None:
    universe = load_demo_universe("config/event/demo_universe_v1.json")
    policy = load_screening_policy("config/b2/screening_policy_v1.json")
    assert universe == ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "AMD")
    assert policy.policy_version == "SCREENING_POLICY_V1"
    assert policy.universe_ref == "DEMO_UNIVERSE_V1"
    assert policy.weights is not None
    assert set(policy.weights.values()) == {Decimal("0.20")}
    assert sum(policy.weights.values(), start=Decimal("0")) == Decimal("1.00")
    assert policy.shortlist_size == 5
    assert policy.final_candidate_count == 3


def test_screening_policy_rejects_numeric_json_weight(tmp_path) -> None:
    payload = json.loads(open("config/b2/screening_policy_v1.json", encoding="utf-8").read())
    payload["weights"]["return_20s"] = 0.2
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(B2ConfigError, match="decimal strings"):
        load_screening_policy(path)


def test_screening_policy_rejects_changed_owner_weight(tmp_path) -> None:
    payload = json.loads(open("config/b2/screening_policy_v1.json", encoding="utf-8").read())
    payload["weights"]["return_20s"] = "0.30"
    payload["weights"]["max_drawdown_20s"] = "0.10"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(B2ConfigError, match="owner-approved"):
        load_screening_policy(path)


def test_universe_rejects_noncanonical_symbol(tmp_path) -> None:
    path = tmp_path / "universe.json"
    path.write_text('{"universe_id":"DEMO_UNIVERSE_V1","symbols":["aapl"]}', encoding="utf-8")
    with pytest.raises(B2ConfigError, match="canonical uppercase"):
        load_demo_universe(path)
