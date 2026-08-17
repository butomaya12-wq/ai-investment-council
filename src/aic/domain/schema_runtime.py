from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, ForwardRef, Literal, Union, get_args

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, BeforeValidator, ConfigDict, StrictBool, StrictInt, ValidationInfo, WrapSerializer, create_model, model_validator
from referencing import Registry
from referencing.jsonschema import DRAFT202012
from typing_extensions import Annotated

from .canonical import Rfc3339DateTime, canonical_rfc3339_datetime, canonical_sha256, parse_rfc3339_datetime
from .errors import ContractValidationError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUTHORITY_DIR = _REPO_ROOT / "config" / "b1_2"
_BUNDLE_PATH = _AUTHORITY_DIR / "canonical_schema_contract_bundle_v0.5.2.json"
_REGISTRY_PATH = _AUTHORITY_DIR / "canonical_schema_registry_baseline_v0.5.2.json"

BUNDLE: dict[str, Any] = json.loads(_BUNDLE_PATH.read_text(encoding="utf-8"))
REGISTRY_BASELINE: dict[str, Any] = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
RESOURCES: dict[str, dict[str, Any]] = BUNDLE["$defs"]

_REVIEWED_ACTIVE_FORMATS = frozenset({"date", "date-time", "uuid"})
EXPECTED_ACTIVATED_RESOURCE_COUNT = 83
_EXPECTED_ACTIVE_FORMAT_OCCURRENCES = MappingProxyType(
    {"date-time": 97, "uuid": 11, "date": 1}
)
_EXPECTED_ACTIVE_FORMAT_TOTAL = 109
_STRICT_UUID_PATTERN = re.compile(
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}",
    re.ASCII,
)


def _build_offline_registry(resources: dict[str, dict[str, Any]]) -> Registry:
    if len(resources) != EXPECTED_ACTIVATED_RESOURCE_COUNT:
        raise ContractValidationError(
            f"activated resource count must equal {EXPECTED_ACTIVATED_RESOURCE_COUNT}"
        )
    seen_ids: set[str] = set()
    registry_resources: list[tuple[str, Any]] = []
    for name, schema in resources.items():
        resource_id = schema.get("$id")
        if not isinstance(resource_id, str) or not resource_id:
            raise ContractValidationError(f"{name}: non-empty $id is required")
        if resource_id in seen_ids:
            raise ContractValidationError(f"duplicate schema $id: {resource_id}")
        Draft202012Validator.check_schema(schema)
        seen_ids.add(resource_id)
        registry_resources.append((resource_id, DRAFT202012.create_resource(schema)))
    return Registry().with_resources(registry_resources)


def _active_format_occurrences(resources: dict[str, dict[str, Any]]) -> Counter[str]:
    occurrences: Counter[str] = Counter()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if "format" in node:
                format_name = node["format"]
                if not isinstance(format_name, str):
                    raise ContractValidationError("JSON Schema format must be a string")
                occurrences[format_name] += 1
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    for schema in resources.values():
        visit(schema)
    return occurrences


def _is_strict_uuid(instance: object) -> bool:
    if not isinstance(instance, str):
        return True
    return _STRICT_UUID_PATTERN.fullmatch(instance) is not None


def _parse_authorized_rfc3339_datetime(value: Any) -> datetime | Rfc3339DateTime:
    parsed = parse_rfc3339_datetime(value)
    if isinstance(parsed, Rfc3339DateTime) and parsed.is_leap_second:
        preceding_utc_second = parsed._utc_second
        is_final_day_of_month = (
            (preceding_utc_second + timedelta(days=1)).month != preceding_utc_second.month
        )
        if not (
            preceding_utc_second.hour == 23
            and preceding_utc_second.minute == 59
            and preceding_utc_second.second == 59
            and is_final_day_of_month
        ):
            raise ValueError("leap second must normalize to the UTC end of a month")
    return parsed


def _is_strict_authorized_rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True
    try:
        _parse_authorized_rfc3339_datetime(value)
    except ValueError:
        return False
    return True


def _build_profile_format_checker(resources: dict[str, dict[str, Any]]) -> FormatChecker:
    occurrences = _active_format_occurrences(resources)
    active_formats = frozenset(occurrences)
    if active_formats != _REVIEWED_ACTIVE_FORMATS:
        raise ContractValidationError(
            "unreviewed active JSON Schema format(s): "
            + ", ".join(sorted(active_formats - _REVIEWED_ACTIVE_FORMATS))
        )
    if sum(occurrences.values()) != _EXPECTED_ACTIVE_FORMAT_TOTAL:
        raise ContractValidationError("active JSON Schema format total occurrence anchor mismatch")
    if dict(occurrences) != dict(_EXPECTED_ACTIVE_FORMAT_OCCURRENCES):
        raise ContractValidationError("active JSON Schema format occurrence anchor mismatch")
    missing_checkers = sorted(active_formats - FormatChecker.checkers.keys())
    if missing_checkers:
        raise ContractValidationError(
            "required JSON Schema format checker(s) unavailable: " + ", ".join(missing_checkers)
        )
    checker = FormatChecker(formats=active_formats)
    checker.checks("date-time")(_is_strict_authorized_rfc3339_datetime)
    checker.checks("uuid")(_is_strict_uuid)
    return checker


OFFLINE_REGISTRY = _build_offline_registry(RESOURCES)
ACTIVE_FORMAT_OCCURRENCES = MappingProxyType(dict(_active_format_occurrences(RESOURCES)))
ACTIVE_FORMATS = frozenset(ACTIVE_FORMAT_OCCURRENCES)
PROFILE_FORMAT_CHECKER = _build_profile_format_checker(RESOURCES)
ID_TO_NAME = {schema["$id"]: name for name, schema in RESOURCES.items()}
CANONICAL_NAMES: tuple[str, ...] = tuple(BUNDLE["x-aic-inventory"])


def _parse_decimal(value: Any) -> Decimal:
    if type(value) is not str:
        raise ValueError("authoritative decimal requires a canonical decimal string")
    pattern = RESOURCES["CanonicalDecimal"].get("pattern")
    if not isinstance(pattern, str):
        raise ContractValidationError("CanonicalDecimal.pattern must be a string")
    if re.fullmatch(pattern, value) is None:
        raise ValueError("invalid canonical decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal string") from exc
    if not result.is_finite():
        raise ValueError("NaN/Infinity are forbidden")
    return result


def _parse_utc_datetime(value: Any) -> datetime | Rfc3339DateTime:
    return _parse_authorized_rfc3339_datetime(value)


def _serialize_utc_datetime(value: datetime | Rfc3339DateTime, handler: Any, info: Any) -> Any:
    if isinstance(value, Rfc3339DateTime):
        if info.mode == "json":
            return canonical_rfc3339_datetime(value)
        return value
    return handler(value, info)


CanonicalDecimalT = Annotated[Decimal, BeforeValidator(_parse_decimal)]
UtcDateTimeT = Annotated[
    Union[datetime, Rfc3339DateTime],
    BeforeValidator(_parse_utc_datetime),
    WrapSerializer(_serialize_utc_datetime),
]


class FrozenDict(dict):
    def _blocked(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("canonical nested mapping is immutable")
    __setitem__ = _blocked
    __delitem__ = _blocked
    clear = _blocked
    pop = _blocked
    popitem = _blocked
    setdefault = _blocked
    update = _blocked


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value
    if isinstance(value, dict) and not isinstance(value, FrozenDict):
        return FrozenDict({k: _deep_freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(v) for v in value)
    if isinstance(value, tuple):
        return tuple(_deep_freeze(v) for v in value)
    return value


def validate_resource(resource_name: str, value: Any) -> None:
    schema = RESOURCES[resource_name]
    validator = Draft202012Validator(
        schema,
        registry=OFFLINE_REGISTRY,
        format_checker=PROFILE_FORMAT_CHECKER,
    )
    error = next(validator.iter_errors(value), None)
    if error is not None:
        raise ContractValidationError(f"{resource_name}: {error.json_path}: {error.message}")


def _schema_to_annotation(schema: dict[str, Any], models: dict[str, type[BaseModel]]) -> Any:
    if "$ref" in schema:
        target = ID_TO_NAME[schema["$ref"]]
        if target == "CanonicalDecimal":
            return CanonicalDecimalT
        if target == "UtcDateTime":
            return UtcDateTimeT
        if target in {"Hash256", "CanonicalId"}:
            return str
        if target == "JsonValue":
            return Any
        if target in models:
            return models[target]
        return ForwardRef(target)

    if "const" in schema:
        return Literal[schema["const"]]
    if "enum" in schema:
        return Literal[tuple(schema["enum"])]
    if "anyOf" in schema:
        annotations = tuple(_schema_to_annotation(branch, models) for branch in schema["anyOf"])
        # Remove exact duplicates while preserving order.
        unique: list[Any] = []
        for ann in annotations:
            if ann not in unique:
                unique.append(ann)
        return Union[tuple(unique)] if len(unique) > 1 else unique[0]

    t = schema.get("type")
    if t == "string":
        if schema.get("x-aic-python-type") == "decimal.Decimal":
            return CanonicalDecimalT
        if schema.get("format") == "date-time":
            return UtcDateTimeT
        return str
    if t == "integer": return StrictInt
    if t == "boolean": return StrictBool
    if t == "number": return Union[StrictInt, float]
    if t == "null": return type(None)
    if t == "array":
        item_ann = _schema_to_annotation(schema.get("items", {}), models)
        return list[item_ann]
    if t == "object": return dict[str, Any]
    return Any


class SchemaBoundModel(BaseModel):
    __schema_name__: ClassVar[str]
    __deep_frozen__: ClassVar[bool] = False

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_activated_schema(cls, value: Any) -> Any:
        validate_resource(cls.__schema_name__, value)
        return value

    @model_validator(mode="after")
    def _validate_activated_schema(self, info: ValidationInfo) -> "SchemaBoundModel":
        schema = RESOURCES[self.__schema_name__]
        self_hash = schema.get("x-aic-self-hash-field")
        if self_hash and not (info.context or {}).get("skip_self_hash"):
            expected = canonical_sha256(self, exclude_fields=(self_hash,))
            actual = getattr(self, self_hash)
            if actual != expected:
                raise ContractValidationError(
                    f"{self.__schema_name__}: {self_hash} != canonical self hash"
                )
        if self.__deep_frozen__:
            for field_name in self.__class__.model_fields:
                object.__setattr__(self, field_name, _deep_freeze(getattr(self, field_name)))
        return self

    @classmethod
    def from_unhashed(cls, **data: Any) -> "SchemaBoundModel":
        schema = RESOURCES[cls.__schema_name__]
        self_hash = schema.get("x-aic-self-hash-field")
        if not self_hash:
            return cls.model_validate(data)
        if self_hash in data:
            raise ValueError(f"{self_hash} must be omitted when using from_unhashed")
        placeholder = "0" * 64
        draft = cls.model_validate({**data, self_hash: placeholder}, context={"skip_self_hash": True})
        expected = canonical_sha256(draft, exclude_fields=(self_hash,))
        normalized = draft.model_dump(mode="json", exclude_none=False)
        normalized[self_hash] = expected
        return cls.model_validate(normalized)


def _object_dependency_order() -> list[str]:
    object_names = {name for name, schema in RESOURCES.items() if schema.get("type") == "object"}
    deps: dict[str, set[str]] = {name: set() for name in object_names}

    def refs(node: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(node, dict):
            if "$ref" in node and node["$ref"] in ID_TO_NAME:
                found.add(ID_TO_NAME[node["$ref"]])
            for val in node.values():
                found |= refs(val)
        elif isinstance(node, list):
            for val in node:
                found |= refs(val)
        return found

    for name in object_names:
        deps[name] = {d for d in refs(RESOURCES[name]) if d in object_names and d != name}

    ordered: list[str] = []
    remaining = set(object_names)
    while remaining:
        ready = sorted(name for name in remaining if deps[name].issubset(set(ordered)))
        if not ready:
            raise ContractValidationError("cyclic object schema dependency")
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered


def build_models() -> dict[str, type[BaseModel]]:
    models: dict[str, type[BaseModel]] = {}
    canonical_set = set(CANONICAL_NAMES)
    for name in _object_dependency_order():
        schema = RESOURCES[name]
        fields: dict[str, tuple[Any, Any]] = {}
        required = set(schema.get("required", []))
        for field_name, field_schema in schema.get("properties", {}).items():
            annotation = _schema_to_annotation(field_schema, models)
            default = ... if field_name in required else None
            fields[field_name] = (annotation, default)

        self_hash = schema.get("x-aic-self-hash-field")
        # Canonical/value/helper contracts are immutable bindings. The sole
        # intentionally mutable B1 projection is AGGREGATE_HEAD_V1.
        deep_frozen = name != "AGGREGATE_HEAD_V1"
        config = ConfigDict(
            extra="forbid",
            strict=True,
            frozen=deep_frozen,
            validate_assignment=True,
            arbitrary_types_allowed=False,
        )
        dynamic_base = type(
            f"_{name}Base",
            (SchemaBoundModel,),
            {
                "model_config": config,
                "__schema_name__": name,
                "__deep_frozen__": deep_frozen,
            },
        )
        model = create_model(name, __base__=dynamic_base, **fields)
        models[name] = model

    namespace = {**models, "CanonicalDecimalT": CanonicalDecimalT, "UtcDateTimeT": UtcDateTimeT}
    for model in models.values():
        model.model_rebuild(_types_namespace=namespace)
    return models
