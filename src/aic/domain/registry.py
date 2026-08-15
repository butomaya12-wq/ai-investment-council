from __future__ import annotations

from typing import Any

from .contracts import CANONICAL_MODELS
from .schema_runtime import BUNDLE, CANONICAL_NAMES, REGISTRY_BASELINE, RESOURCES, validate_resource

ACTIVATED_FIELD_AUTHORITY_VERSION = "v0.5.2"
ACTIVATED_MAPPING_DELTA = "M071"
EFFECTIVE_MAPPING_COUNT = 71
STALE_GLOBAL_CHECK_AFTER_ACTIVATION = "B1-VC-058"


def model_for(resource_name: str):
    return CANONICAL_MODELS[resource_name]


def schema_for(resource_name: str) -> dict[str, Any]:
    return RESOURCES[resource_name]


__all__ = [
    "ACTIVATED_FIELD_AUTHORITY_VERSION",
    "ACTIVATED_MAPPING_DELTA",
    "EFFECTIVE_MAPPING_COUNT",
    "STALE_GLOBAL_CHECK_AFTER_ACTIVATION",
    "CANONICAL_NAMES",
    "CANONICAL_MODELS",
    "BUNDLE",
    "REGISTRY_BASELINE",
    "model_for",
    "schema_for",
    "validate_resource",
]
