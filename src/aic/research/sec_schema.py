from __future__ import annotations

from typing import Any


SEC_SECTION_SCHEMA_VERSION = "B3_SEC_SECTION_SCHEMA_v0_1"
ALLOWED_SEC_SECTION_VALUES = (
    "Business",
    "Risk Factors",
    "MD&A",
    "Material 8-K",
)


def constrain_sec_sections_in_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Bind SEC section names in the model-facing JSON Schema to the frozen allowlist.

    The ResearchPolicy validator remains the second, application-owned enforcement layer.
    This function prevents the model from freely naming SEC sections in the first place.
    """
    defs = schema.get("$defs")
    if not isinstance(defs, dict):
        raise ValueError("ResearchGapPlan schema must contain $defs")
    sec_parameters = defs.get("SecFilingSectionParameters")
    if not isinstance(sec_parameters, dict):
        raise ValueError("ResearchGapPlan schema is missing SecFilingSectionParameters")
    properties = sec_parameters.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("SecFilingSectionParameters schema must contain properties")
    sections = properties.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("SecFilingSectionParameters.sections schema is missing")
    items = sections.get("items")
    if not isinstance(items, dict):
        raise ValueError("SecFilingSectionParameters.sections must have item schema")
    if items.get("type") != "string":
        raise ValueError("SEC section item schema must be string before constraint")

    # Copy only the objects we mutate; callers retain a deterministic, isolated schema object.
    out = dict(schema)
    out_defs = dict(defs)
    out_sec = dict(sec_parameters)
    out_properties = dict(properties)
    out_sections = dict(sections)
    out_items = dict(items)
    out_items["enum"] = list(ALLOWED_SEC_SECTION_VALUES)
    out_sections["items"] = out_items
    out_properties["sections"] = out_sections
    out_sec["properties"] = out_properties
    out_defs["SecFilingSectionParameters"] = out_sec
    out["$defs"] = out_defs
    return out
