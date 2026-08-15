from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from aic.domain.contracts import (
    B5OpenOrderExposureRecord,
    B5PortfolioPositionRecord,
    CANONICAL_MODELS,
    DECISION_TTL_V1,
    MODELS,
)
from aic.domain.schema_export import export_canonical_schemas, resource_sha256
from aic.domain.schema_runtime import BUNDLE, CANONICAL_NAMES, RESOURCES


def test_b1_vc_003_every_canonical_model_forbids_extra_fields() -> None:
    assert len(CANONICAL_MODELS) == 59
    for name, model in CANONICAL_MODELS.items():
        assert model.model_config.get("extra") == "forbid", name


def test_b1_vc_004_hash_bound_model_is_frozen_and_self_hash_checked() -> None:
    ttl = DECISION_TTL_V1.from_unhashed(
        source_policy_value_id="pv-1",
        source_policy_value_hash="0" * 64,
        duration_seconds=3600,
        unit="SECONDS",
    )
    assert len(ttl.ttl_hash) == 64
    with pytest.raises(ValidationError):
        ttl.duration_seconds = 7200
    with pytest.raises(ValidationError):
        DECISION_TTL_V1(
            source_policy_value_id="pv-1",
            source_policy_value_hash="0" * 64,
            duration_seconds=3600,
            unit="SECONDS",
            ttl_hash="1" * 64,
        )


def test_b1_vc_005_exact_59_schema_exports_match_activated_resources(tmp_path: Path) -> None:
    manifest = export_canonical_schemas(tmp_path)
    assert len(manifest) == 59
    assert len(list(tmp_path.glob("*.json"))) == 59
    assert set(CANONICAL_NAMES) == set(BUNDLE["x-aic-schema-manifest"])
    for filename, expected_hash in manifest.items():
        loaded = json.loads((tmp_path / filename).read_text(encoding="utf-8"))
        name = loaded["x-aic-canonical-name"]
        assert resource_sha256(name) == expected_hash
        assert BUNDLE["x-aic-schema-manifest"][name]["schema_sha256"] == expected_hash


def test_b1_vc_006_all_canonical_names_exist_and_approval_record_absent() -> None:
    assert len(CANONICAL_NAMES) == 59
    assert set(CANONICAL_NAMES) == set(CANONICAL_MODELS)
    assert "ApprovalRecord" not in MODELS
    assert "APPROVAL_RECORD" not in MODELS


def test_b1_vc_007_b5_material_numerics_are_decimal_with_explicit_units() -> None:
    valid_position = B5PortfolioPositionRecord(
        qty={"value": "10", "unit": "SHARES"},
        market_value={"value": "1234.56", "unit": "USD"},
        cost_basis={"value": "1000", "unit": "USD"},
        current_price={"value": "123.456", "unit": "USD_PER_SHARE"},
    )
    assert valid_position.qty.value == Decimal("10")
    assert valid_position.market_value.value == Decimal("1234.56")

    bad_values = [
        {"qty": "10", "market_value": None, "cost_basis": None, "current_price": None},
        {"qty": 10, "market_value": None, "cost_basis": None, "current_price": None},
        {"qty": {"value": "10"}, "market_value": None, "cost_basis": None, "current_price": None},
        {
            "qty": {"value": "10", "unit": "SHARES"},
            "market_value": None,
            "cost_basis": None,
            "current_price": None,
            "symbol": "AAPL",
        },
    ]
    for value in bad_values:
        with pytest.raises(ValidationError):
            B5PortfolioPositionRecord(**value)

    valid_order = B5OpenOrderExposureRecord(
        state="accepted",
        side="buy",
        notional={"value": "500", "unit": "USD"},
        qty=None,
    )
    assert valid_order.notional.value == Decimal("500")
    with pytest.raises(ValidationError):
        B5OpenOrderExposureRecord(state="accepted", side="buy", notional="500", qty=None)
    with pytest.raises(ValidationError):
        B5OpenOrderExposureRecord(state="accepted", side="buy", notional=None, qty="4")
    with pytest.raises(ValidationError):
        B5OpenOrderExposureRecord(state="accepted", side="buy", notional=None, qty=None, order_id="x")


def test_activated_bundle_has_no_jsonvalue_reachable_from_b5_risk_snapshot() -> None:
    id_to_name = {schema.get("$id"): name for name, schema in RESOURCES.items()}
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        node = RESOURCES[name]

        def walk(value):
            if isinstance(value, dict):
                ref = value.get("$ref")
                if ref in id_to_name:
                    visit(id_to_name[ref])
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)

        walk(node)

    visit("B5_RISK_INPUT_SNAPSHOT_V1")
    assert "JsonValue" not in seen


def test_external_registry_matches_all_83_bundle_resources() -> None:
    from aic.domain.schema_runtime import REGISTRY_BASELINE

    expected = REGISTRY_BASELINE["resource_registry"]
    assert len(expected) == len(RESOURCES) == 83
    assert set(expected) == set(RESOURCES)
    for name in sorted(RESOURCES):
        assert expected[name]["expected_resource_sha256"] == resource_sha256(name), name
