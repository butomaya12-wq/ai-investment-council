from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, ForwardRef, Literal, Union, get_args
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, StrictBool, StrictInt, ValidationInfo, create_model, model_validator
from typing_extensions import Annotated

from .canonical import canonical_data, canonical_sha256
from .errors import ContractValidationError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUTHORITY_DIR = _REPO_ROOT / "config" / "b1_2"
_BUNDLE_PATH = _AUTHORITY_DIR / "canonical_schema_contract_bundle_v0.5.2.json"
_REGISTRY_PATH = _AUTHORITY_DIR / "canonical_schema_registry_baseline_v0.5.2.json"

BUNDLE: dict[str, Any] = json.loads(_BUNDLE_PATH.read_text(encoding="utf-8"))
REGISTRY_BASELINE: dict[str, Any] = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
RESOURCES: dict[str, dict[str, Any]] = BUNDLE["$defs"]
ID_TO_NAME = {schema["$id"]: name for name, schema in RESOURCES.items() if "$id" in schema}
CANONICAL_NAMES: tuple[str, ...] = tuple(BUNDLE["x-aic-inventory"])

_ALLOWED_SCHEMA_KEYWORDS = {
    "$id", "$schema", "$ref", "$comment", "title", "description",
    "type", "properties", "required", "additionalProperties", "items",
    "anyOf", "allOf", "not", "if", "then", "contains",
    "enum", "const", "format", "pattern", "minLength",
    "minimum", "maximum", "minItems", "maxItems", "uniqueItems",
}


def _collect_unknown_schema_keywords(node: Any, path: str = "$") -> list[str]:
    unknown: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key.startswith("x-aic-"):
                continue
            if key not in _ALLOWED_SCHEMA_KEYWORDS and key not in {
                # property names are traversed as values, not schema keywords
            }:
                # Keys under properties are field names; keys under x-aic are ignored above.
                if path.endswith(".properties"):
                    pass
                else:
                    unknown.append(f"{path}.{key}")
            if key == "properties" and isinstance(value, dict):
                for field_name, field_schema in value.items():
                    unknown.extend(_collect_unknown_schema_keywords(field_schema, f"{path}.properties.{field_name}"))
            elif key.startswith("x-aic-"):
                continue
            else:
                unknown.extend(_collect_unknown_schema_keywords(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            unknown.extend(_collect_unknown_schema_keywords(item, f"{path}[{i}]"))
    return unknown


def assert_supported_schema_subset() -> None:
    unknown: list[str] = []
    for name, schema in RESOURCES.items():
        unknown.extend(_collect_unknown_schema_keywords(schema, f"$defs.{name}"))
    # Filter metadata object keys carried under non-schema extension content.
    unknown = [u for u in unknown if ".x-aic-" not in u]
    if unknown:
        raise ContractValidationError("unsupported JSON Schema keyword(s): " + ", ".join(sorted(set(unknown))[:20]))


def _parse_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or isinstance(value, (int, float)):
        raise ValueError("authoritative decimal rejects int/float/bool input")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, str):
        if "e" in value.lower():
            raise ValueError("decimal exponent notation is forbidden")
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("invalid decimal string") from exc
    else:
        raise ValueError("decimal must be Decimal or canonical decimal string")
    if not result.is_finite():
        raise ValueError("NaN/Infinity are forbidden")
    return result


def _parse_utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("invalid ISO-8601 datetime") from exc
    else:
        raise ValueError("datetime must be aware datetime or ISO-8601 string")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("naive datetime is forbidden")
    return dt.astimezone(UTC)


CanonicalDecimalT = Annotated[Decimal, BeforeValidator(_parse_decimal)]
UtcDateTimeT = Annotated[datetime, BeforeValidator(_parse_utc_datetime)]


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


def _resolve_ref(ref: str) -> dict[str, Any]:
    try:
        return RESOURCES[ID_TO_NAME[ref]]
    except KeyError as exc:
        raise ContractValidationError(f"unresolved schema ref: {ref}") from exc


def _json_type_ok(expected: str, value: Any) -> bool:
    if expected == "null": return value is None
    if expected == "boolean": return type(value) is bool
    if expected == "integer": return type(value) is int
    if expected == "number": return type(value) in {int, float} and type(value) is not bool
    if expected == "string": return isinstance(value, str)
    if expected == "array": return isinstance(value, list)
    if expected == "object": return isinstance(value, dict)
    return False


def _validation_errors(schema: dict[str, Any], value: Any, path: str = "$") -> list[str]:
    if "$ref" in schema:
        return _validation_errors(_resolve_ref(schema["$ref"]), value, path)

    if "anyOf" in schema:
        branch_errors = [_validation_errors(branch, value, path) for branch in schema["anyOf"]]
        if not any(not errors for errors in branch_errors):
            return [f"{path}: no anyOf branch matched"]

    if "allOf" in schema:
        errors: list[str] = []
        for branch in schema["allOf"]:
            errors.extend(_validation_errors(branch, value, path))
        if errors:
            return errors

    if "not" in schema and not _validation_errors(schema["not"], value, path):
        return [f"{path}: forbidden by not schema"]

    if "if" in schema and not _validation_errors(schema["if"], value, path):
        if "then" in schema:
            errors = _validation_errors(schema["then"], value, path)
            if errors:
                return errors

    expected_type = schema.get("type")
    if expected_type is not None and not _json_type_ok(expected_type, value):
        return [f"{path}: expected {expected_type}, got {type(value).__name__}"]

    if "const" in schema and value != schema["const"]:
        return [f"{path}: const mismatch"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: enum mismatch"]

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            return [f"{path}: string shorter than minLength"]
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            return [f"{path}: pattern mismatch"]
        fmt = schema.get("format")
        if fmt == "uuid":
            try:
                UUID(value)
            except Exception:
                return [f"{path}: invalid uuid"]
        if fmt == "date-time":
            try:
                dt = _parse_utc_datetime(value)
            except ValueError as exc:
                return [f"{path}: {exc}"]
            if schema.get("x-aic-require-aware-utc") and dt.tzinfo is None:
                return [f"{path}: aware UTC datetime required"]

    if type(value) in {int, float} and type(value) is not bool:
        if "minimum" in schema and value < schema["minimum"]:
            return [f"{path}: below minimum"]
        if "maximum" in schema and value > schema["maximum"]:
            return [f"{path}: above maximum"]

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            return [f"{path}: fewer than minItems"]
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return [f"{path}: more than maxItems"]
        if schema.get("uniqueItems"):
            seen: set[str] = set()
            for item in value:
                key = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                if key in seen:
                    return [f"{path}: duplicate item violates uniqueItems"]
                seen.add(key)
        if "items" in schema:
            errors: list[str] = []
            for i, item in enumerate(value):
                errors.extend(_validation_errors(schema["items"], item, f"{path}[{i}]"))
            if errors:
                return errors
        if "contains" in schema:
            if not any(not _validation_errors(schema["contains"], item, f"{path}[*]") for item in value):
                return [f"{path}: contains constraint not satisfied"]

    if isinstance(value, dict):
        required = schema.get("required", [])
        for field in required:
            if field not in value:
                return [f"{path}.{field}: required field missing"]
        properties = schema.get("properties", {})
        errors: list[str] = []
        for field, field_value in value.items():
            if field in properties:
                errors.extend(_validation_errors(properties[field], field_value, f"{path}.{field}"))
            else:
                additional = schema.get("additionalProperties", True)
                if additional is False:
                    errors.append(f"{path}.{field}: additional property forbidden")
                elif isinstance(additional, dict):
                    errors.extend(_validation_errors(additional, field_value, f"{path}.{field}"))
        if errors:
            return errors

    return []


def validate_resource(resource_name: str, value: Any) -> None:
    schema = RESOURCES[resource_name]
    data = canonical_data(value)
    errors = _validation_errors(schema, data)
    if errors:
        raise ContractValidationError(f"{resource_name}: {errors[0]}")


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

    @model_validator(mode="after")
    def _validate_activated_schema(self, info: ValidationInfo) -> "SchemaBoundModel":
        validate_resource(self.__schema_name__, self)
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
    assert_supported_schema_subset()
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
