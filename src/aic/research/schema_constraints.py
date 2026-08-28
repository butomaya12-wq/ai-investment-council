from __future__ import annotations

import re
from typing import Any, Iterable

from .policy import MAX_ITEMS_PER_NEED


PLANNER_SCHEMA_CONSTRAINT_VERSION = "B3_PLANNER_SCHEMA_CONSTRAINT_v0_1"
_ACCESSION_RE = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")


def _require_defs(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("$defs")
    if not isinstance(defs, dict):
        raise ValueError("ResearchGapPlan schema must contain $defs")
    return defs


def _copy_def_properties(
    defs: dict[str, Any],
    def_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    definition = defs.get(def_name)
    if not isinstance(definition, dict):
        raise ValueError(f"ResearchGapPlan schema is missing {def_name}")
    properties = definition.get("properties")
    if not isinstance(properties, dict):
        raise ValueError(f"{def_name} schema must contain properties")
    return dict(definition), dict(properties)


def _constrain_string_items(
    properties: dict[str, Any],
    field_name: str,
    values: Iterable[str],
) -> dict[str, Any]:
    allowed = tuple(dict.fromkeys(values))
    if not allowed:
        return properties
    field = properties.get(field_name)
    if not isinstance(field, dict):
        raise ValueError(f"{field_name} schema is missing")
    items = field.get("items")
    if not isinstance(items, dict) or items.get("type") != "string":
        raise ValueError(f"{field_name} must be an array of strings before constraint")
    out_field = dict(field)
    out_items = dict(items)
    out_items["enum"] = list(allowed)
    out_field["items"] = out_items
    out = dict(properties)
    out[field_name] = out_field
    return out


def constrain_planner_schema(
    schema: dict[str, Any],
    *,
    evidence_refs: tuple[str, ...],
    computed_value_refs: tuple[str, ...],
    allowed_source_handles: tuple[str, ...],
) -> dict[str, Any]:
    """Narrow model-facing planner schema to application-owned budgets and refs.

    The application post-validator remains authoritative. This layer reduces avoidable
    model-policy violations and prevents free-form source identifier selection.
    """
    out = dict(schema)
    defs = dict(_require_defs(schema))

    need_def, need_props = _copy_def_properties(defs, "ResearchNeed")
    max_items = need_props.get("max_items")
    if not isinstance(max_items, dict) or max_items.get("type") != "integer":
        raise ValueError("ResearchNeed.max_items must be integer before constraint")
    max_items_out = dict(max_items)
    # enum is part of the Structured Outputs-supported subset and exactly represents 1..5.
    max_items_out["enum"] = list(range(1, MAX_ITEMS_PER_NEED + 1))
    need_props["max_items"] = max_items_out
    need_def["properties"] = need_props
    defs["ResearchNeed"] = need_def

    evidence_def, evidence_props = _copy_def_properties(defs, "B2EvidenceDetailParameters")
    evidence_props = _constrain_string_items(evidence_props, "evidence_ids", evidence_refs)
    evidence_def["properties"] = evidence_props
    defs["B2EvidenceDetailParameters"] = evidence_def

    computed_def, computed_props = _copy_def_properties(defs, "B2ComputedValueDetailParameters")
    computed_props = _constrain_string_items(
        computed_props,
        "computed_value_ids",
        computed_value_refs,
    )
    computed_def["properties"] = computed_props
    defs["B2ComputedValueDetailParameters"] = computed_def

    sec_accessions = tuple(
        handle for handle in allowed_source_handles if _ACCESSION_RE.fullmatch(handle)
    )
    if sec_accessions:
        sec_def, sec_props = _copy_def_properties(defs, "SecFilingSectionParameters")
        accession = sec_props.get("filing_accession")
        if not isinstance(accession, dict) or accession.get("type") != "string":
            raise ValueError("filing_accession must be string before constraint")
        accession_out = dict(accession)
        accession_out["enum"] = list(dict.fromkeys(sec_accessions))
        sec_props["filing_accession"] = accession_out
        sec_def["properties"] = sec_props
        defs["SecFilingSectionParameters"] = sec_def

    out["$defs"] = defs
    return out
