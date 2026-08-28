from __future__ import annotations

from typing import Any

from aic.research.synthesize import (
    CLAIM_CATEGORIES,
    CandidateSynthesisDraft,
    MaterialClaimDraft,
    _openai_strict_schema,
)


def test_material_claim_category_is_exact_model_facing_enum() -> None:
    schema = MaterialClaimDraft.model_json_schema(mode="validation")
    category = schema["properties"]["category"]
    assert tuple(category["enum"]) == CLAIM_CATEGORIES


def _walk(node: Any):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_synthesis_structured_output_schema_requires_every_object_field() -> None:
    schema = _openai_strict_schema(
        CandidateSynthesisDraft.model_json_schema(mode="validation")
    )
    for node in _walk(schema):
        if not isinstance(node, dict) or node.get("type") != "object":
            continue
        properties = node.get("properties")
        if not isinstance(properties, dict):
            continue
        assert node.get("additionalProperties") is False
        assert node.get("required") == list(properties.keys())


def test_synthesis_structured_output_schema_removes_pydantic_defaults() -> None:
    schema = _openai_strict_schema(
        CandidateSynthesisDraft.model_json_schema(mode="validation")
    )
    assert all(
        not isinstance(node, dict) or "default" not in node
        for node in _walk(schema)
    )
