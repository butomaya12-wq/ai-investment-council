from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping

from aic.council.initial_runtime_cost_v02 import (
    EXPECTED_RUNTIME_PRICING_VERSION,
    load_initial_runtime_pricing,
    runtime_cost_upper_bound_usd,
)
from aic.council.initial_schema_repair_v05 import (
    INITIAL_SCHEMA_REPAIR_VERSION,
    INITIAL_SCHEMA_VERSION,
    build_bounded_initial_request_v05,
)
from aic.council.model_input import build_initial_model_inputs
from aic.council.model_policy import STAGE_MAX_OUTPUT_TOKENS, CouncilModelStage
from aic.council.model_selection import InitialSelectedModelAuthority
from aic.council.models import CouncilInputBundle, CouncilInputFreezeArtifact, CouncilLane
from aic.council.request import CouncilRequestStage
from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import MATERIAL_CLAIM_V1
from aic.research.handoff import load_real_event_handoff


ARTIFACT_VERSION = "B4_REOPEN_PRODUCTION_COST_PREFLIGHT_v0_1"
PASS_STATUS = "B4_REOPEN_PRODUCTION_COST_PREFLIGHT_ZERO_CALL_PASS"
NEXT_GATE = "B4_REOPEN_INITIAL_OWNER_COST_APPROVAL"
COST_AUTHORITY_MODE = "STAGED_EXACT"
REOPEN_MODEL_INPUT_VERSION = "B4_REOPEN_INITIAL_MODEL_INPUT_v0_1"
EFFECTIVE_BUNDLE_VERSION = "B4_REOPEN_EFFECTIVE_COUNCIL_BUNDLE_v0_1"
SUPPLEMENTAL_MATERIAL_CLAIM_VIEW_VERSION = "B4_REOPEN_SUPPLEMENTAL_MATERIAL_CLAIM_VIEW_v0_1"

EXPECTED_LIFECYCLE_HASH = "fabd17c8615b7bd4dec00d2a6c09c688d80b91e0730e8f02bb9f99289a7c6f55"
EXPECTED_LIFECYCLE_STATUS = "B4_REOPEN_LIFECYCLE_PLAN_ZERO_CALL_PASS"
EXPECTED_OVERLAY_HASH = "ff4d3357ee49927b7ed07bb8fa70cbbca162f6110b74bb9e7f93f2c3dc654ab0"
EXPECTED_OVERLAY_STATUS = "B4_REOPEN_INPUT_OVERLAY_ZERO_CALL_PASS"
EXPECTED_CLOSURE_HASH = "af8f48ae8e6984c73c7ff447eeb523fbda72855ee49460bdc60f0634be4216e6"
EXPECTED_CLOSURE_STATUS = "B3_REOPEN_REMAINING_GAPS_CLOSURE_ZERO_CALL_PASS"
EXPECTED_SELECTED_B3_HASH = "938b7eecfee58d1074be662d30a1bf183f1133f92815028637de4cd662307f27"
EXPECTED_INITIAL_SELECTION_HASH = "0554900c0e7c1b696a681301d249d011f6d500331fe53751998024477269d1e0"
EXPECTED_CANDIDATES = ("NVDA", "MSFT", "META")
EXPECTED_EFFECTIVE_COUNTS = {"NVDA": 12, "MSFT": 13, "META": 12}
EXPECTED_SUPPLEMENTAL_IDS = (
    "B3_REOPEN_SUPPLEMENTAL_MSFT_VALUATION_001",
    "B3_REOPEN_SUPPLEMENTAL_META_VALUATION_001",
    "B3_REOPEN_SUPPLEMENTAL_META_PORTFOLIO_001",
)
EXPECTED_SUPPLEMENTAL_EVIDENCE_IDS = (
    "B3_REOPEN_EVID_MSFT_VALUATION_20260828T173300Z",
    "B3_REOPEN_EVID_META_VALUATION_20260828T173300Z",
    "B3_REOPEN_EVID_META_PORTFOLIO_20260827T200000Z",
)

_STAGE_LANE = (
    (CouncilRequestStage.BULL_INITIAL, CouncilLane.BULL),
    (CouncilRequestStage.BEAR_INITIAL, CouncilLane.BEAR),
    (CouncilRequestStage.RED_TEAM_INITIAL, CouncilLane.RED_TEAM),
)


class B4ReopenProductionCostPreflightError(ValueError):
    pass


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenProductionCostPreflightError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise B4ReopenProductionCostPreflightError(f"{label} root must be object")
    return value


def _verify_artifact_hash(
    payload: Mapping[str, Any],
    *,
    label: str,
    expected_hash: str,
) -> str:
    observed = payload.get("artifact_hash")
    if observed != expected_hash:
        raise B4ReopenProductionCostPreflightError(f"{label} hash drift")
    if observed != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise B4ReopenProductionCostPreflightError(f"{label} self-hash mismatch")
    return str(observed)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite() or value < 0:
        raise B4ReopenProductionCostPreflightError("cost must be finite and non-negative")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _request_body_utf8_bytes(payload: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _source_material_claim_enum(schema: Mapping[str, Any]) -> tuple[str, ...]:
    matches: list[tuple[str, ...]] = []
    for node in _walk_dicts(schema):
        properties = node.get("properties")
        if not isinstance(properties, Mapping):
            continue
        prop = properties.get("source_material_claim_ids")
        if not isinstance(prop, Mapping):
            continue
        items = prop.get("items")
        if not isinstance(items, Mapping):
            continue
        values = items.get("enum")
        if isinstance(values, list) and all(isinstance(item, str) for item in values):
            matches.append(tuple(values))
    if not matches:
        raise B4ReopenProductionCostPreflightError(
            "Initial response schema lacks source MaterialClaim allowlist"
        )
    return max(matches, key=len)


def _validate_lifecycle_and_overlay(
    lifecycle: Mapping[str, Any],
    overlay: Mapping[str, Any],
) -> None:
    _verify_artifact_hash(
        lifecycle,
        label="B4 reopen lifecycle plan",
        expected_hash=EXPECTED_LIFECYCLE_HASH,
    )
    if lifecycle.get("status") != EXPECTED_LIFECYCLE_STATUS:
        raise B4ReopenProductionCostPreflightError("B4 reopen lifecycle plan is not PASS")
    if lifecycle.get("planned_model_eval_calls") != 0:
        raise B4ReopenProductionCostPreflightError("model eval rerun unexpectedly planned")
    if lifecycle.get("planned_fresh_production_model_calls_max") != 13:
        raise B4ReopenProductionCostPreflightError("fresh production call ceiling drift")
    if lifecycle.get("planned_paid_calls_max") != 13:
        raise B4ReopenProductionCostPreflightError("paid call ceiling drift")
    if lifecycle.get("model_calls_authorized") is not False:
        raise B4ReopenProductionCostPreflightError("lifecycle unexpectedly grants model authority")
    if lifecycle.get("provider_reads_authorized") is not False:
        raise B4ReopenProductionCostPreflightError("lifecycle unexpectedly grants provider authority")
    if lifecycle.get("automatic_repair_calls_authorized") != 0:
        raise B4ReopenProductionCostPreflightError("automatic repair authority drift")
    if lifecycle.get("automatic_retries") != 0:
        raise B4ReopenProductionCostPreflightError("automatic retry authority drift")
    if lifecycle.get("post_reopen_judge_contract_required") is not True:
        raise B4ReopenProductionCostPreflightError("post-reopen Judge contract requirement missing")
    if lifecycle.get("historical_reopen_restricted_judge_runtime_reusable") is not False:
        raise B4ReopenProductionCostPreflightError("historical Judge runtime cannot be reusable")

    _verify_artifact_hash(
        overlay,
        label="B4 reopen input overlay",
        expected_hash=EXPECTED_OVERLAY_HASH,
    )
    if overlay.get("status") != EXPECTED_OVERLAY_STATUS:
        raise B4ReopenProductionCostPreflightError("B4 reopen input overlay is not PASS")
    if overlay.get("effective_material_claim_count") != 37:
        raise B4ReopenProductionCostPreflightError("effective MaterialClaim count must be 37")
    if overlay.get("source_b3_reopen_closure_hash") != EXPECTED_CLOSURE_HASH:
        raise B4ReopenProductionCostPreflightError("B3 reopen closure lineage drift")
    gap = overlay.get("effective_gap_overlay")
    if not isinstance(gap, Mapping):
        raise B4ReopenProductionCostPreflightError("effective gap overlay missing")
    if gap.get("effective_unresolved_data_gap_refs") != []:
        raise B4ReopenProductionCostPreflightError("effective data gaps remain open")
    if gap.get("effective_unresolved_reopen_reason_codes") != []:
        raise B4ReopenProductionCostPreflightError("effective reopen reasons remain open")


def _closure_supplemental(
    closure: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    _verify_artifact_hash(
        closure,
        label="B3 reopen closure",
        expected_hash=EXPECTED_CLOSURE_HASH,
    )
    if closure.get("status") != EXPECTED_CLOSURE_STATUS:
        raise B4ReopenProductionCostPreflightError("B3 reopen closure is not PASS")
    if closure.get("overall_research_reopen_complete") is not True:
        raise B4ReopenProductionCostPreflightError("B3 reopen is not complete")
    if closure.get("all_judge_conditions_satisfied") is not True:
        raise B4ReopenProductionCostPreflightError("Judge conditions are not all satisfied")
    if closure.get("remaining_reopen_reason_codes") != []:
        raise B4ReopenProductionCostPreflightError("B3 reopen reasons remain open")
    if closure.get("legacy_frozen_artifacts_mutated") is not False:
        raise B4ReopenProductionCostPreflightError("legacy frozen artifacts were mutated")
    if closure.get("legacy_material_claim_payloads_mutated") is not False:
        raise B4ReopenProductionCostPreflightError("legacy MaterialClaims were mutated")
    if closure.get("reopen_overlay_is_additive") is not True:
        raise B4ReopenProductionCostPreflightError("B3 reopen closure must be additive")

    raw_claims = closure.get("supplemental_claims")
    raw_evidence = closure.get("supplemental_evidence_units")
    if not isinstance(raw_claims, list) or not all(isinstance(item, Mapping) for item in raw_claims):
        raise B4ReopenProductionCostPreflightError("supplemental claims missing")
    if not isinstance(raw_evidence, list) or not all(isinstance(item, Mapping) for item in raw_evidence):
        raise B4ReopenProductionCostPreflightError("supplemental evidence missing")
    if tuple(item.get("claim_id") for item in raw_claims) != EXPECTED_SUPPLEMENTAL_IDS:
        raise B4ReopenProductionCostPreflightError("supplemental claim IDs drift")
    if tuple(item.get("evidence_id") for item in raw_evidence) != EXPECTED_SUPPLEMENTAL_EVIDENCE_IDS:
        raise B4ReopenProductionCostPreflightError("supplemental evidence IDs drift")
    return raw_claims, raw_evidence


def materialize_supplemental_material_claim(
    supplemental: Mapping[str, Any],
) -> dict[str, Any]:
    claim_id = supplemental.get("claim_id")
    candidate_id = supplemental.get("candidate_id")
    category = supplemental.get("category")
    claim_text = supplemental.get("claim_text")
    claim_kind = supplemental.get("claim_kind")
    support_status = supplemental.get("support_status")
    evidence_ids = supplemental.get("evidence_ids")
    if (
        not isinstance(claim_id, str)
        or not isinstance(candidate_id, str)
        or not isinstance(category, str)
        or not isinstance(claim_text, str)
        or claim_kind != "FACT"
        or support_status != "SUPPORTED"
        or not isinstance(evidence_ids, list)
        or not evidence_ids
        or not all(isinstance(item, str) for item in evidence_ids)
    ):
        raise B4ReopenProductionCostPreflightError("supplemental claim shape invalid")

    payload: dict[str, Any] = {
        "claim_id": claim_id,
        "candidate_id": candidate_id,
        "category": category,
        "claim_text": claim_text,
        "claim_kind": "FACT",
        "materiality": "MATERIAL",
        "evidence_ids": list(evidence_ids),
        "computed_value_ids": [],
        "conflict_ids": [],
        "assumptions": [],
        "support_status": "SUPPORTED",
        "uncertainty_note": None,
    }
    payload["claim_hash"] = canonical_sha256(payload)
    try:
        validated = MATERIAL_CLAIM_V1.model_validate(payload)
    except Exception as exc:
        raise B4ReopenProductionCostPreflightError(
            f"supplemental MaterialClaim view invalid: {claim_id}"
        ) from exc
    return validated.model_dump(mode="json", exclude_none=False, warnings=False)


def derive_effective_bundle(
    historical: CouncilInputBundle,
    *,
    effective_claim_ids: tuple[str, ...],
    supplemental_portfolio_context_refs: tuple[str, ...],
) -> CouncilInputBundle:
    shared = tuple(
        dict.fromkeys(
            (*historical.shared_portfolio_context_refs, *supplemental_portfolio_context_refs)
        )
    )
    return CouncilInputBundle.from_unhashed(
        bundle_id=f"{historical.bundle_id}:B4_REOPEN_EFFECTIVE_v0_1",
        candidate_id=historical.candidate_id,
        candidate_packet_id=historical.candidate_packet_id,
        candidate_packet_hash=historical.candidate_packet_hash,
        research_snapshot_id=historical.research_snapshot_id,
        research_snapshot_hash=historical.research_snapshot_hash,
        b2_snapshot_id=historical.b2_snapshot_id,
        deep_comparison_id=historical.deep_comparison_id,
        mandate_version=historical.mandate_version,
        council_policy_version=historical.council_policy_version,
        judge_policy_version=historical.judge_policy_version,
        model_policy_version=historical.model_policy_version,
        allowed_material_claim_ids=effective_claim_ids,
        allowed_computed_value_ids=historical.allowed_computed_value_ids,
        allowed_conflict_ids=historical.allowed_conflict_ids,
        shared_portfolio_context_refs=shared,
        created_at=historical.created_at,
    )


def build_reopen_model_input(
    *,
    legacy_model_input: Any,
    effective_bundle: CouncilInputBundle,
    effective_material_claims: tuple[Mapping[str, Any], ...],
    supplemental_evidence_units: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    candidate = str(legacy_model_input.candidate_id)
    historical_gaps = list(legacy_model_input.data_gap_refs)
    body: dict[str, Any] = {
        "model_input_version": REOPEN_MODEL_INPUT_VERSION,
        "candidate_id": candidate,
        "council_input_bundle": effective_bundle.model_dump(mode="json", exclude_none=False),
        "candidate_packet": dict(legacy_model_input.candidate_packet),
        "material_claims": [dict(item) for item in effective_material_claims],
        "computed_values": [
            item.model_dump(mode="json", exclude_none=False)
            for item in legacy_model_input.computed_values
        ],
        "data_gap_refs": [],
        "reopen_overlay": {
            "overlay_version": "B4_REOPEN_EFFECTIVE_GAP_AND_EVIDENCE_OVERLAY_v0_1",
            "source_b3_reopen_closure_hash": EXPECTED_CLOSURE_HASH,
            "source_b4_reopen_input_overlay_hash": EXPECTED_OVERLAY_HASH,
            "historical_candidate_packet_source_gaps": historical_gaps,
            "historical_candidate_packet_source_gaps_are_effectively_closed": True,
            "effective_unresolved_data_gap_refs": [],
            "effective_unresolved_reopen_reason_codes": [],
            "supplemental_evidence_units": [dict(item) for item in supplemental_evidence_units],
            "legacy_candidate_packet_is_immutable_historical_context": True,
        },
    }
    body["model_input_hash"] = canonical_sha256(body)
    return body


def _effective_surface_by_candidate(
    *,
    overlay: Mapping[str, Any],
    supplemental_claims: list[Mapping[str, Any]],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    surfaces = overlay.get("effective_candidate_surfaces")
    if not isinstance(surfaces, list):
        raise B4ReopenProductionCostPreflightError("effective candidate surfaces missing")
    claim_ids: dict[str, tuple[str, ...]] = {}
    portfolio_refs: dict[str, tuple[str, ...]] = {}
    for row in surfaces:
        if not isinstance(row, Mapping):
            raise B4ReopenProductionCostPreflightError("effective candidate surface malformed")
        candidate = row.get("candidate_id")
        ids = row.get("effective_material_claim_ids")
        refs = row.get("supplemental_portfolio_context_refs")
        if (
            not isinstance(candidate, str)
            or not isinstance(ids, list)
            or not all(isinstance(item, str) for item in ids)
            or not isinstance(refs, list)
            or not all(isinstance(item, str) for item in refs)
        ):
            raise B4ReopenProductionCostPreflightError("effective candidate surface invalid")
        claim_ids[candidate] = tuple(ids)
        portfolio_refs[candidate] = tuple(refs)
    if tuple(claim_ids) != EXPECTED_CANDIDATES:
        raise B4ReopenProductionCostPreflightError("effective candidate order drift")
    if {key: len(value) for key, value in claim_ids.items()} != EXPECTED_EFFECTIVE_COUNTS:
        raise B4ReopenProductionCostPreflightError("effective candidate claim counts drift")
    supplemental_ids = {str(item.get("claim_id")) for item in supplemental_claims}
    if supplemental_ids != set(EXPECTED_SUPPLEMENTAL_IDS):
        raise B4ReopenProductionCostPreflightError("supplemental claim set drift")
    return claim_ids, portfolio_refs


def build_b4_reopen_production_cost_preflight(
    *,
    code_commit_sha: str,
    lifecycle: Mapping[str, Any],
    overlay: Mapping[str, Any],
    closure: Mapping[str, Any],
    freeze: CouncilInputFreezeArtifact,
    reconciliation: Mapping[str, Any],
    handoff: Any,
    initial_authority: InitialSelectedModelAuthority,
    pricing: Mapping[str, Any],
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise B4ReopenProductionCostPreflightError("exact lowercase git SHA required")
    _validate_lifecycle_and_overlay(lifecycle, overlay)
    supplemental_claims, supplemental_evidence = _closure_supplemental(closure)

    if reconciliation.get("artifact_hash") != EXPECTED_SELECTED_B3_HASH:
        raise B4ReopenProductionCostPreflightError("selected B3 reconciliation lineage drift")
    if reconciliation.get("artifact_hash") != canonical_sha256(
        reconciliation, exclude_fields=("artifact_hash",)
    ):
        raise B4ReopenProductionCostPreflightError("selected B3 reconciliation self-hash mismatch")
    if initial_authority.selection_hash != EXPECTED_INITIAL_SELECTION_HASH:
        raise B4ReopenProductionCostPreflightError("Initial selected-model authority drift")
    selected = initial_authority.selected_candidate
    if (
        selected.candidate_key != "L2"
        or selected.model != "gpt-5.6-terra"
        or selected.reasoning_effort != "low"
        or selected.ladder_position != 2
    ):
        raise B4ReopenProductionCostPreflightError("Initial selected model identity drift")
    if pricing.get("pricing_version") != EXPECTED_RUNTIME_PRICING_VERSION:
        raise B4ReopenProductionCostPreflightError("runtime pricing version drift")

    legacy_inputs = build_initial_model_inputs(freeze, reconciliation, handoff)
    if tuple(item.candidate_id for item in legacy_inputs) != EXPECTED_CANDIDATES:
        raise B4ReopenProductionCostPreflightError("legacy Initial model-input candidate order drift")
    effective_ids_by_candidate, portfolio_refs_by_candidate = _effective_surface_by_candidate(
        overlay=overlay,
        supplemental_claims=supplemental_claims,
    )

    supplemental_views = {
        str(item["claim_id"]): materialize_supplemental_material_claim(item)
        for item in supplemental_claims
    }
    supplemental_evidence_by_candidate: dict[str, list[Mapping[str, Any]]] = {
        candidate: [] for candidate in EXPECTED_CANDIDATES
    }
    for item in supplemental_evidence:
        candidate = item.get("candidate_id")
        if candidate not in supplemental_evidence_by_candidate:
            raise B4ReopenProductionCostPreflightError("supplemental evidence candidate drift")
        supplemental_evidence_by_candidate[str(candidate)].append(item)

    effective_bundles: dict[str, CouncilInputBundle] = {}
    reopen_inputs: dict[str, dict[str, Any]] = {}
    legacy_claim_count = 0
    supplemental_claim_count = 0
    for legacy_input, historical_bundle in zip(legacy_inputs, freeze.bundles, strict=True):
        candidate = legacy_input.candidate_id
        legacy_claims = [dict(item) for item in legacy_input.material_claims]
        legacy_ids = tuple(str(item["claim_id"]) for item in legacy_claims)
        effective_ids = effective_ids_by_candidate[candidate]
        appended = tuple(item for item in effective_ids if item not in set(legacy_ids))
        if effective_ids[: len(legacy_ids)] != legacy_ids:
            raise B4ReopenProductionCostPreflightError(
                f"{candidate} effective surface does not preserve legacy claim prefix"
            )
        if any(item not in supplemental_views for item in appended):
            raise B4ReopenProductionCostPreflightError(
                f"{candidate} effective surface contains unknown supplemental claim"
            )
        effective_claims = tuple(
            [*legacy_claims, *(supplemental_views[item] for item in appended)]
        )
        if tuple(str(item["claim_id"]) for item in effective_claims) != effective_ids:
            raise B4ReopenProductionCostPreflightError(
                f"{candidate} effective claim materialization order drift"
            )
        bundle = derive_effective_bundle(
            historical_bundle,
            effective_claim_ids=effective_ids,
            supplemental_portfolio_context_refs=portfolio_refs_by_candidate[candidate],
        )
        model_input = build_reopen_model_input(
            legacy_model_input=legacy_input,
            effective_bundle=bundle,
            effective_material_claims=effective_claims,
            supplemental_evidence_units=tuple(supplemental_evidence_by_candidate[candidate]),
        )
        effective_bundles[candidate] = bundle
        reopen_inputs[candidate] = model_input
        legacy_claim_count += len(legacy_claims)
        supplemental_claim_count += len(appended)

    if legacy_claim_count != 34 or supplemental_claim_count != 3:
        raise B4ReopenProductionCostPreflightError("effective 34+3 claim construction drift")

    request_rows: list[dict[str, Any]] = []
    total_cost = Decimal("0")
    output_cap = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.INITIAL]
    for candidate in EXPECTED_CANDIDATES:
        bundle = effective_bundles[candidate]
        model_input = reopen_inputs[candidate]
        for stage, lane in _STAGE_LANE:
            model_run_ref = (
                f"B4_REOPEN_INITIAL_{candidate}_{lane.value}_L2_"
                f"{str(model_input['model_input_hash'])[:12]}"
            )
            request = build_bounded_initial_request_v05(
                stage=stage,
                model_candidate=selected,
                bundle=bundle,
                model_run_ref=model_run_ref,
                model_input=model_input,
                allowed_data_gap_refs=(),
            )
            payload = dict(request.request_payload)
            if payload.get("max_output_tokens") != output_cap:
                raise B4ReopenProductionCostPreflightError("Initial output-token cap drift")
            text = payload.get("text")
            fmt = text.get("format") if isinstance(text, Mapping) else None
            schema = fmt.get("schema") if isinstance(fmt, Mapping) else None
            if not isinstance(schema, Mapping):
                raise B4ReopenProductionCostPreflightError("Initial response schema missing")
            allowed = _source_material_claim_enum(schema)
            expected_allowed = effective_ids_by_candidate[candidate]
            if not set(expected_allowed).issubset(set(allowed)):
                raise B4ReopenProductionCostPreflightError(
                    f"{candidate} response schema omits effective MaterialClaim refs"
                )
            byte_count = _request_body_utf8_bytes(payload)
            per_call_cost = runtime_cost_upper_bound_usd(
                model=selected.model,
                input_tokens_upper_bound=byte_count,
                output_tokens_upper_bound=output_cap,
                call_count=1,
                pricing=pricing,
            )
            total_cost += per_call_cost
            request_rows.append(
                {
                    "candidate_id": candidate,
                    "lane": lane.value,
                    "stage": stage.value,
                    "model_run_ref": model_run_ref,
                    "model": selected.model,
                    "reasoning_effort": selected.reasoning_effort,
                    "model_input_hash": model_input["model_input_hash"],
                    "effective_bundle_hash": bundle.bundle_hash,
                    "historical_candidate_packet_hash": bundle.candidate_packet_hash,
                    "request_hash": request.request_hash,
                    "request_body_utf8_bytes": byte_count,
                    "input_tokens_upper_bound": byte_count,
                    "max_output_tokens": output_cap,
                    "per_call_cost_upper_bound_usd": _decimal_text(per_call_cost),
                    "effective_material_claim_count": len(expected_allowed),
                    "effective_material_claim_ids": list(expected_allowed),
                    "schema_allows_all_effective_material_claim_ids": True,
                    "effective_data_gap_refs": [],
                }
            )

    if len(request_rows) != 9:
        raise B4ReopenProductionCostPreflightError("Initial exact-cost scope must contain 9 requests")
    pricing_hash = pricing.get("pricing_hash")
    if not isinstance(pricing_hash, str) or pricing_hash != canonical_sha256(
        pricing, exclude_fields=("pricing_hash",)
    ):
        raise B4ReopenProductionCostPreflightError("runtime pricing hash mismatch")

    request_manifest_hash = canonical_sha256(
        {
            "rows": [
                {
                    "candidate_id": row["candidate_id"],
                    "lane": row["lane"],
                    "model_input_hash": row["model_input_hash"],
                    "effective_bundle_hash": row["effective_bundle_hash"],
                    "request_hash": row["request_hash"],
                    "request_body_utf8_bytes": row["request_body_utf8_bytes"],
                    "max_output_tokens": row["max_output_tokens"],
                }
                for row in request_rows
            ]
        }
    )
    effective_input_manifest_hash = canonical_sha256(
        {
            "candidate_order": list(EXPECTED_CANDIDATES),
            "effective_bundles": {
                candidate: effective_bundles[candidate].model_dump(
                    mode="json", exclude_none=False
                )
                for candidate in EXPECTED_CANDIDATES
            },
            "model_input_hashes": {
                candidate: reopen_inputs[candidate]["model_input_hash"]
                for candidate in EXPECTED_CANDIDATES
            },
            "source_closure_hash": EXPECTED_CLOSURE_HASH,
            "source_overlay_hash": EXPECTED_OVERLAY_HASH,
        }
    )

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "cost_authority_mode": COST_AUTHORITY_MODE,
        "source_b4_reopen_lifecycle_plan_hash": EXPECTED_LIFECYCLE_HASH,
        "source_b4_reopen_input_overlay_hash": EXPECTED_OVERLAY_HASH,
        "source_b3_reopen_closure_hash": EXPECTED_CLOSURE_HASH,
        "source_selected_b3_reconciliation_hash": EXPECTED_SELECTED_B3_HASH,
        "source_initial_selected_model_selection_hash": EXPECTED_INITIAL_SELECTION_HASH,
        "source_historical_b4_input_freeze_hash": freeze.artifact_hash,
        "effective_material_claim_count": 37,
        "legacy_material_claim_count": 34,
        "supplemental_material_claim_count": 3,
        "supplemental_material_claim_view_version": SUPPLEMENTAL_MATERIAL_CLAIM_VIEW_VERSION,
        "reopen_model_input_version": REOPEN_MODEL_INPUT_VERSION,
        "effective_bundle_version": EFFECTIVE_BUNDLE_VERSION,
        "historical_candidate_packets_mutated": False,
        "historical_candidate_packet_source_gaps_retained_as_historical_context": True,
        "effective_unresolved_data_gap_refs": [],
        "effective_unresolved_reopen_reason_codes": [],
        "initial_schema_repair_version": INITIAL_SCHEMA_REPAIR_VERSION,
        "initial_schema_version": INITIAL_SCHEMA_VERSION,
        "selected_initial_model": {
            "candidate_key": selected.candidate_key,
            "model": selected.model,
            "reasoning_effort": selected.reasoning_effort,
            "ladder_position": selected.ladder_position,
        },
        "planned_total_production_calls_max": 13,
        "exactly_costed_now_stage": "INITIAL",
        "exactly_costed_now_calls": 9,
        "deferred_exact_costing_calls": 4,
        "rebuttal_cost_requires_fresh_initial_freeze": True,
        "judge_cost_requires_fresh_rebuttal_freeze": True,
        "all_13_owner_approval_ready": False,
        "next_owner_approval_scope": "INITIAL_ONLY",
        "request_manifest_hash": request_manifest_hash,
        "effective_input_manifest_hash": effective_input_manifest_hash,
        "initial_request_rows": request_rows,
        "max_request_body_utf8_bytes": max(
            int(row["request_body_utf8_bytes"]) for row in request_rows
        ),
        "max_output_tokens_per_call": output_cap,
        "input_token_upper_bound_method": (
            "CONSERVATIVE: one input token per UTF-8 serialized request-body byte; "
            "pricing helper additionally assumes all input can be billed at the higher "
            "cache-write input rate and applies frozen long-context multipliers when relevant"
        ),
        "pricing_version": pricing["pricing_version"],
        "pricing_hash": pricing_hash,
        "pricing_as_of_date": pricing["as_of_date"],
        "initial_exact_cost_upper_bound_usd": _decimal_text(total_cost),
        "owner_cost_approval_required": True,
        "initial_paid_dispatch_authorized": False,
        "rebuttal_paid_dispatch_authorized": False,
        "judge_paid_dispatch_authorized": False,
        "model_calls_authorized": False,
        "provider_reads_authorized": False,
        "planned_provider_reads": 0,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "rerun_authorized": False,
        "broker_writes_authorized": 0,
        "alpaca_orders_authorized": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def load_and_build_b4_reopen_production_cost_preflight(
    *,
    code_commit_sha: str,
    lifecycle_path: str | Path,
    overlay_path: str | Path,
    closure_path: str | Path,
    freeze_path: str | Path,
    reconciliation_path: str | Path,
    handoff_path: str | Path,
    initial_authority_path: str | Path,
    pricing_path: str | Path,
) -> dict[str, Any]:
    lifecycle = _read_object(lifecycle_path, label="B4 reopen lifecycle plan")
    overlay = _read_object(overlay_path, label="B4 reopen input overlay")
    closure = _read_object(closure_path, label="B3 reopen closure")
    reconciliation = _read_object(reconciliation_path, label="selected B3 reconciliation")
    freeze_raw = _read_object(freeze_path, label="historical B4 input freeze")
    authority_raw = _read_object(
        initial_authority_path, label="Initial selected-model authority"
    )
    try:
        freeze = CouncilInputFreezeArtifact.model_validate(freeze_raw)
        authority = InitialSelectedModelAuthority.model_validate(authority_raw)
    except Exception as exc:
        raise B4ReopenProductionCostPreflightError(
            "typed historical B4 authority validation failed"
        ) from exc
    handoff = load_real_event_handoff(Path(handoff_path))
    pricing = load_initial_runtime_pricing(Path(pricing_path))
    return build_b4_reopen_production_cost_preflight(
        code_commit_sha=code_commit_sha,
        lifecycle=lifecycle,
        overlay=overlay,
        closure=closure,
        freeze=freeze,
        reconciliation=reconciliation,
        handoff=handoff,
        initial_authority=authority,
        pricing=pricing,
    )
