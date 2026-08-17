from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from aic.domain import schema_runtime
from aic.domain.contracts import OWNER_ACCOUNT_V1
from aic.domain.errors import ContractValidationError
from aic.domain.schema_runtime import PROFILE_FORMAT_CHECKER, RESOURCES, validate_resource


_PINNED_UUID_CORPUS_SHA256 = "25951c7ab5f48991ca3e752513bf38febcbdca066540a844e5bba7ec9a88eaa6"
_VALID_UUID = "2eb8aa08-aa98-11ea-b4aa-73b441d16380"


def _official_uuid_corpus() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = Path(os.environ["AIC_OFFICIAL_UUID_CORPUS"])
    contents = source.read_bytes()
    assert sha256(contents).hexdigest() == _PINNED_UUID_CORPUS_SHA256
    groups = json.loads(contents)
    assert len(groups) == 1
    schema = groups[0]["schema"]
    vectors = groups[0]["tests"]
    assert len(vectors) == 28
    return schema, vectors


def _format_errors(resource_name: str, payload: dict[str, Any]) -> list[Any]:
    validator = Draft202012Validator(
        RESOURCES[resource_name],
        registry=schema_runtime.OFFLINE_REGISTRY,
        format_checker=PROFILE_FORMAT_CHECKER,
    )
    return list(validator.iter_errors(payload))


def _walk_errors(errors: list[Any]):
    for error in errors:
        yield error
        yield from _walk_errors(error.context)


def _uuid_surface_payloads() -> dict[str, tuple[dict[str, Any], tuple[str, ...]]]:
    timestamp = "2024-02-29T12:00:00Z"
    hash_value = "0" * 64
    return {
        "APPROVAL_ACTION_NONCE_V1": (
            {
                "action_nonce_id": _VALID_UUID,
                "action_nonce_hash": hash_value,
                "session_id": _VALID_UUID,
                "owner_id": _VALID_UUID,
                "proposal_id": "proposal-1",
                "canonical_payload_hash": hash_value,
                "allowed_decision": "APPROVE",
                "issued_at": timestamp,
                "expires_at": timestamp,
                "consumed_at": None,
                "status": "ACTIVE",
                "action_nonce_record_hash": hash_value,
            },
            ("action_nonce_id", "session_id", "owner_id"),
        ),
        "JOURNAL_EVENT_ENVELOPE_V1": (
            {
                "event_id": _VALID_UUID,
                "aggregate_type": "proposal",
                "aggregate_id": _VALID_UUID,
                "aggregate_version": 1,
                "run_id": _VALID_UUID,
                "event_type": "created",
                "payload_schema": "fixture",
                "payload_schema_version": "v1",
                "occurred_at": timestamp,
                "actor_type": "system",
                "actor_id": None,
                "correlation_id": _VALID_UUID,
                "causation_id": _VALID_UUID,
                "payload": {},
                "prev_event_hash": None,
                "event_hash": hash_value,
                "inserted_at": timestamp,
            },
            ("event_id", "aggregate_id", "run_id", "correlation_id", "causation_id"),
        ),
        "OWNER_ACCOUNT_V1": (
            {
                "owner_id": _VALID_UUID,
                "login_name": "fixture-owner",
                "password_hash_argon2id": "$argon2id$fixture",
                "password_hash_parameters_ref": "params-1",
                "auth_version": 0,
                "created_at": timestamp,
                "password_changed_at": timestamp,
                "status": "ACTIVE",
                "all_sessions_revoked_after": None,
                "owner_account_hash": hash_value,
            },
            ("owner_id",),
        ),
        "OWNER_SESSION_V1": (
            {
                "session_id": _VALID_UUID,
                "session_token_hash": hash_value,
                "owner_id": _VALID_UUID,
                "auth_version_at_issue": 0,
                "issued_at": timestamp,
                "last_primary_auth_at": timestamp,
                "last_seen_at": timestamp,
                "idle_expires_at": timestamp,
                "absolute_expires_at": timestamp,
                "revoked_at": None,
                "revocation_reason": None,
                "csrf_token_hash": hash_value,
                "status": "ACTIVE",
                "session_hash": hash_value,
            },
            ("session_id", "owner_id"),
        ),
    }


def test_official_uuid_corpus_expected_equals_actual() -> None:
    schema, vectors = _official_uuid_corpus()
    validator = Draft202012Validator(schema, format_checker=PROFILE_FORMAT_CHECKER)
    actual = [not list(validator.iter_errors(vector["data"])) for vector in vectors]
    expected = [vector["valid"] for vector in vectors]
    assert len(expected) == len(actual) == 28
    assert actual == expected


@pytest.mark.parametrize(
    "value",
    [
        f" {_VALID_UUID}",
        f"{_VALID_UUID} ",
        f"\t{_VALID_UUID}",
        f"{_VALID_UUID}\t",
    ],
)
def test_uuid_rejects_surrounding_horizontal_whitespace_without_trimming(value: str) -> None:
    assert not PROFILE_FORMAT_CHECKER.conforms(value, "uuid")


def test_uuid_checker_is_registered_on_the_shared_profile_and_is_not_a_type_checker() -> None:
    assert PROFILE_FORMAT_CHECKER.checkers["uuid"][0] is schema_runtime._is_strict_uuid
    for non_string in (12, 13.7, {}, [], False, None):
        assert PROFILE_FORMAT_CHECKER.conforms(non_string, "uuid")


def test_all_eleven_active_uuid_surfaces_use_the_shared_profile_checker() -> None:
    surfaces = _uuid_surface_payloads()
    assert sum(len(fields) for _, fields in surfaces.values()) == 11
    assert sum(1 for schema in RESOURCES.values() for _ in _uuid_nodes(schema)) == 11

    for resource_name, (payload, fields) in surfaces.items():
        validate_resource(resource_name, payload)
        for field in fields:
            malformed = deepcopy(payload)
            malformed[field] = "not-a-uuid"
            errors = _format_errors(resource_name, malformed)
            assert any(
                error.validator == "format"
                and error.validator_value == "uuid"
                and error.instance == "not-a-uuid"
                for error in _walk_errors(errors)
            )
            with pytest.raises(ContractValidationError):
                validate_resource(resource_name, malformed)


def _uuid_nodes(node: Any):
    if isinstance(node, dict):
        if node.get("format") == "uuid":
            yield node
        for value in node.values():
            yield from _uuid_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _uuid_nodes(value)


def test_raw_schema_validation_rejects_uuid_before_pydantic_binding() -> None:
    payload, _ = _uuid_surface_payloads()["OWNER_ACCOUNT_V1"]
    payload["owner_id"] = f"{_VALID_UUID}-"
    with pytest.raises(ValidationError, match=r"OWNER_ACCOUNT_V1: \$\.owner_id: .*is not a 'uuid'"):
        OWNER_ACCOUNT_V1.model_validate(payload, context={"skip_self_hash": True})
