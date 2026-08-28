from __future__ import annotations

from typing import Any


SEC_SECTION_SCHEMA_VERSION = "B3_SEC_SECTION_SCHEMA_v0_2"

# Generic research policy may name Material 8-K as a source class, but the current
# filing-section adapter is accession-bound to the supplied 10-K/10-Q document.
# Therefore the model-facing runtime capability is intentionally narrower.
ALLOWED_SEC_SECTION_VALUES = (
    "Business",
    "Risk Factors",
    "MD&A",
    "Material 8-K",
)
RUNTIME_RETRIEVABLE_SEC_SECTION_VALUES = (
    "Business",
    "Risk Factors",
    "MD&A",
)


def validate_runtime_sec_sections(sections: tuple[str, ...]) -> None:
    if any(section not in RUNTIME_RETRIEVABLE_SEC_SECTION_VALUES for section in sections):
        raise ValueError("SEC section requested outside current retrieval capability")


def constrain_sec_sections_in_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Bind model-facing SEC sections to what the current accession-bound adapter can fetch.

    The broader ResearchPolicy validator remains a separate application-owned layer.
    Current reports/8-K are not represented as a section of an annual filing in V1.
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

    out = dict(schema)
    out_defs = dict(defs)
    out_sec = dict(sec_parameters)
    out_properties = dict(properties)
    out_sections = dict(sections)
    out_items = dict(items)
    out_items["enum"] = list(RUNTIME_RETRIEVABLE_SEC_SECTION_VALUES)
    out_sections["items"] = out_items
    out_properties["sections"] = out_sections
    out_sec["properties"] = out_properties
    out_defs["SecFilingSectionParameters"] = out_sec
    out["$defs"] = out_defs
    return out
