from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aic.domain.contracts import DecimalWithUnit
from aic.domain import schema_runtime


@pytest.mark.parametrize("value", ["01", "1.", "+1", " 1", "1 ", "1e2", "1E2"])
def test_canonical_decimal_rejects_noncanonical_raw_lexemes(value: str) -> None:
    with pytest.raises(ValidationError):
        DecimalWithUnit(value=value, unit="USD")


@pytest.mark.parametrize("value", ["0", "1", "1.0", "-0"])
def test_canonical_decimal_accepts_authoritative_raw_lexemes(value: str) -> None:
    parsed = DecimalWithUnit(value=value, unit="USD")
    assert parsed.value == Decimal(value)


@pytest.mark.parametrize("value", [Decimal("1"), 1, 1.0, True])
def test_canonical_decimal_does_not_accept_non_string_direct_input(value: object) -> None:
    with pytest.raises(ValidationError):
        DecimalWithUnit(value=value, unit="USD")


@pytest.mark.parametrize("value", ["01", "1.", "+1", " 1", "1 ", "1e2", "1E2"])
def test_canonical_decimal_model_validate_json_keeps_raw_lexical_rejection(value: str) -> None:
    with pytest.raises(ValidationError):
        DecimalWithUnit.model_validate_json(f'{{"value": "{value}", "unit": "USD"}}')


def test_canonical_decimal_parser_uses_activated_pattern_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    original_resources = schema_runtime.RESOURCES
    original_snapshot = deepcopy(original_resources)
    authority = original_resources["CanonicalDecimal"]
    assert authority["pattern"] == "^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?$"
    disposable_resources = deepcopy(original_resources)
    disposable_resources["CanonicalDecimal"]["pattern"] = "^1$"
    with monkeypatch.context() as context:
        context.setattr(schema_runtime, "RESOURCES", disposable_resources)
        assert schema_runtime._parse_decimal("1") == Decimal("1")
        with pytest.raises(ValueError):
            schema_runtime._parse_decimal("1.0")
    assert schema_runtime.RESOURCES is original_resources
    assert original_resources == original_snapshot


def test_validate_assignment_cannot_create_a_canonical_decimal_bypass() -> None:
    value = DecimalWithUnit(value="1", unit="USD")
    assert value.model_config.get("validate_assignment") is True
    with pytest.raises(ValidationError) as exc_info:
        value.value = "01"
    assert exc_info.value.errors()[0]["type"] == "frozen_instance"
    assert value.value == Decimal("1")


def test_raw_schema_validation_precedes_decimal_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    real_validate_resource = schema_runtime.validate_resource
    observed_payloads: list[dict[str, object]] = []

    def spy_validate_resource(resource_name: str, value: object) -> None:
        if resource_name == "DecimalWithUnit":
            assert isinstance(value, dict)
            observed_payloads.append(value.copy())
        real_validate_resource(resource_name, value)

    monkeypatch.setattr(schema_runtime, "validate_resource", spy_validate_resource)

    direct = DecimalWithUnit(value="1.0", unit="USD")
    assert observed_payloads[-1]["value"] == "1.0"
    assert isinstance(observed_payloads[-1]["value"], str)
    assert direct.value == Decimal("1.0")

    from_json = DecimalWithUnit.model_validate_json('{"value": "1.0", "unit": "USD"}')
    assert observed_payloads[-1]["value"] == "1.0"
    assert isinstance(observed_payloads[-1]["value"], str)
    assert from_json.value == Decimal("1.0")
