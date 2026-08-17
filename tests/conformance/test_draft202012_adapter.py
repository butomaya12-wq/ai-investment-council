from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing.exceptions import NoSuchResource, Unresolvable

from aic.domain.contracts import (
    OWNER_ACCOUNT_V1,
    POLICY_REFERENCE_V1,
    POLICY_VALUE_V1,
    SCHEMA_REGISTRY_ENTRY_V1,
)
from aic.domain.errors import ContractValidationError
from aic.domain.schema_runtime import (
    ACTIVE_FORMAT_OCCURRENCES,
    ACTIVE_FORMATS,
    OFFLINE_REGISTRY,
    PROFILE_FORMAT_CHECKER,
    RESOURCES,
    _build_offline_registry,
    _build_profile_format_checker,
)


def _valid_policy_value_date() -> POLICY_VALUE_V1:
    policy_ref = POLICY_REFERENCE_V1.from_unhashed(
        policy_name="fixture-policy",
        policy_id="fixture-policy-1",
        version="v1",
        policy_hash="0" * 64,
        policy_reference_id="0" * 64,
    )
    return POLICY_VALUE_V1.from_unhashed(
        policy_value_id="pv-date-1",
        policy_ref=policy_ref.model_dump(mode="json"),
        value_key="effective_date",
        value_type="DATE",
        value_decimal=None,
        value_integer=None,
        value_duration_seconds=None,
        value_datetime=None,
        value_date="2024-02-29",
        value_boolean=None,
        value_enum_string=None,
        unit=None,
    )


def _valid_owner_account() -> OWNER_ACCOUNT_V1:
    return OWNER_ACCOUNT_V1.from_unhashed(
        owner_id="123e4567-e89b-12d3-a456-426614174000",
        login_name="fixture-owner",
        password_hash_argon2id="$argon2id$fixture",
        password_hash_parameters_ref="params-1",
        auth_version=0,
        created_at="2024-02-29T12:00:00Z",
        password_changed_at="2024-02-29T12:00:00Z",
        status="ACTIVE",
        all_sessions_revoked_after=None,
    )


def _capture_real_validator_errors(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    captured_errors: list[object] = []
    original_iter_errors = Draft202012Validator.iter_errors

    def tee_iter_errors(self, instance, *args, **kwargs):
        for error in original_iter_errors(self, instance, *args, **kwargs):
            captured_errors.append(error)
            yield error

    monkeypatch.setattr(Draft202012Validator, "iter_errors", tee_iter_errors)
    return captured_errors


def _walk_error_context(errors: list[object]):
    for error in errors:
        yield error
        yield from _walk_error_context(error.context)


def _assert_captured_date_format_error(errors: list[object]) -> None:
    assert any(
        error.validator == "format"
        and error.validator_value == "date"
        and error.json_path == "$.value_date"
        and "is not a 'date'" in error.message
        for error in _walk_error_context(errors)
    )


def test_activated_resources_are_checked_and_registered_in_closed_world() -> None:
    resource_ids = [schema["$id"] for schema in RESOURCES.values()]
    assert len(RESOURCES) == len(resource_ids) == 83
    assert len(set(resource_ids)) == 83
    for schema in RESOURCES.values():
        Draft202012Validator.check_schema(schema)
    assert len(OFFLINE_REGISTRY) == 83
    assert set(OFFLINE_REGISTRY) == set(resource_ids)


def test_registry_rejects_non_83_resource_universe_before_acceptance() -> None:
    reduced_resources = dict(RESOURCES)
    reduced_resources.pop(next(iter(reduced_resources)))
    with pytest.raises(ContractValidationError, match="activated resource count must equal 83"):
        _build_offline_registry(reduced_resources)


def test_reviewed_format_profile_is_authority_derived_and_checker_backed() -> None:
    assert ACTIVE_FORMATS == {"date", "date-time", "uuid"}
    assert sum(ACTIVE_FORMAT_OCCURRENCES.values()) == 109
    assert dict(ACTIVE_FORMAT_OCCURRENCES) == {"date-time": 97, "uuid": 11, "date": 1}
    assert ACTIVE_FORMATS <= PROFILE_FORMAT_CHECKER.checkers.keys()


def test_unreviewed_active_format_fails_before_instance_validation() -> None:
    altered_resources = deepcopy(RESOURCES)
    altered_resources["CanonicalDecimal"]["format"] = "email"
    with pytest.raises(ContractValidationError, match="unreviewed active JSON Schema format"):
        _build_profile_format_checker(altered_resources)


def test_internal_ref_is_resolved_from_offline_registry() -> None:
    schema = {"$ref": RESOURCES["CanonicalDecimal"]["$id"]}
    validator = Draft202012Validator(
        schema,
        registry=OFFLINE_REGISTRY,
        format_checker=PROFILE_FORMAT_CHECKER,
    )
    assert list(validator.iter_errors("1.0")) == []


def test_unregistered_ref_fails_visibly_without_retrieval_callback() -> None:
    missing_ref = "urn:aic:b1.2:schema:unregistered:v0.1"
    with pytest.raises(NoSuchResource):
        OFFLINE_REGISTRY.get_or_retrieve(missing_ref)

    validator = Draft202012Validator(
        {"$ref": missing_ref},
        registry=OFFLINE_REGISTRY,
        format_checker=PROFILE_FORMAT_CHECKER,
    )
    with pytest.raises(Unresolvable):
        next(validator.iter_errors("value"))


def test_policy_value_date_rejects_invalid_lexeme(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _valid_policy_value_date().model_dump(mode="json")
    payload["value_date"] = "NOT-A-DATE"
    captured_errors = _capture_real_validator_errors(monkeypatch)

    with pytest.raises(ValidationError, match=r"POLICY_VALUE_V1: \$\.value_date: .*not valid under any") as exc_info:
        POLICY_VALUE_V1.model_validate(payload, context={"skip_self_hash": True})

    assert "self hash" not in str(exc_info.value)
    _assert_captured_date_format_error(captured_errors)


def test_policy_value_date_rejects_invalid_calendar_date(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _valid_policy_value_date().model_dump(mode="json")
    payload["value_date"] = "2024-02-30"
    captured_errors = _capture_real_validator_errors(monkeypatch)

    with pytest.raises(ValidationError, match=r"POLICY_VALUE_V1: \$\.value_date: .*not valid under any") as exc_info:
        POLICY_VALUE_V1.model_validate(payload, context={"skip_self_hash": True})

    assert "self hash" not in str(exc_info.value)
    _assert_captured_date_format_error(captured_errors)


def test_policy_value_date_accepts_valid_yyyy_mm_dd() -> None:
    value = _valid_policy_value_date()
    assert value.value_type == "DATE"
    assert value.value_date == "2024-02-29"


def test_schema_registry_entry_rejects_invalid_datetime() -> None:
    valid_payload = {
        "schema_name": "fixture-schema",
        "schema_version": "v1",
        "schema_hash": "0" * 64,
        "generated_at": "2024-02-29T12:00:00Z",
    }
    valid = SCHEMA_REGISTRY_ENTRY_V1.model_validate(valid_payload)
    assert valid.generated_at.isoformat() == "2024-02-29T12:00:00+00:00"

    invalid_payload = valid_payload.copy()
    invalid_payload["generated_at"] = "NOT-A-DATE-TIME"
    with pytest.raises(ValidationError, match=r"SCHEMA_REGISTRY_ENTRY_V1: \$\.generated_at: .*is not a 'date-time'"):
        SCHEMA_REGISTRY_ENTRY_V1.model_validate(invalid_payload)


def test_owner_account_rejects_invalid_uuid() -> None:
    payload = _valid_owner_account().model_dump(mode="json")
    payload["owner_id"] = "NOT-A-UUID"

    with pytest.raises(ValidationError, match=r"OWNER_ACCOUNT_V1: \$\.owner_id: .*is not a 'uuid'") as exc_info:
        OWNER_ACCOUNT_V1.model_validate(payload, context={"skip_self_hash": True})

    assert "self hash" not in str(exc_info.value)
