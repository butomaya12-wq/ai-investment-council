from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from . import reopen_rebuttal_runtime as v01


RUNTIME_VERSION = "B4_REOPEN_REBUTTAL_PRODUCTION_RUNTIME_v0_2"
DRY_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_DRY_v0_2"
AUTH_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_PAID_AUTHORIZATION_v0_2"
EVENT_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_JOURNAL_EVENT_v0_2"
RECEIPT_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_PAID_CALL_RECEIPT_v0_2"
FREEZE_VERSION = "B4_REOPEN_REBUTTAL_COUNCIL_FREEZE_v0_2"
BLOCKED_VERSION = "B4_REOPEN_REBUTTAL_COUNCIL_BLOCKED_v0_2"
DERIVATION_CONTRACT = "REBUTTAL_REQUIRED_UNKNOWNS_FROM_EFFECTIVE_DATA_GAPS_v0_2"


B4ReopenRebuttalRuntimeError = v01.B4ReopenRebuttalRuntimeError


def required_unknown_refs_from_context(context: Mapping[str, Any]) -> tuple[str, ...]:
    effective = context.get("effective_data_gap_refs")
    if not isinstance(effective, list) or not all(isinstance(value, str) for value in effective):
        raise B4ReopenRebuttalRuntimeError("effective_data_gap_refs malformed")
    model_input = context.get("model_input")
    if not isinstance(model_input, Mapping):
        raise B4ReopenRebuttalRuntimeError("Rebuttal context model_input missing")
    candidate_input = model_input.get("candidate_model_input")
    if not isinstance(candidate_input, Mapping):
        raise B4ReopenRebuttalRuntimeError("candidate_model_input missing")
    candidate_gaps = candidate_input.get("data_gap_refs")
    if candidate_gaps != effective:
        raise B4ReopenRebuttalRuntimeError(
            "effective data-gap refs differ from candidate model-input data gaps"
        )
    if len(effective) != len(set(effective)):
        raise B4ReopenRebuttalRuntimeError("effective data-gap refs must be unique")
    return tuple(effective)


def load_and_build_reopen_rebuttal_runtime_plan(
    *,
    cost_preflight_path: str | Path,
    recovered_initial_freeze_path: str | Path,
    selection_authority_path: str | Path,
    lifecycle_path: str | Path,
    initial_cost_preflight_path: str | Path,
    overlay_path: str | Path,
    closure_path: str | Path,
    freeze_path: str | Path,
    reconciliation_path: str | Path,
    handoff_path: str | Path,
    initial_authority_path: str | Path,
    pricing_path: str | Path,
) -> v01.BoundReopenRebuttalRuntime:
    cost = v01._read_object(cost_preflight_path, label="B4 reopen Rebuttal cost preflight")
    v01.verify_cost_preflight(cost)
    recomputed = v01.cost_v02.load_and_build_b4_reopen_rebuttal_production_cost_preflight(
        code_commit_sha=v01.EXPECTED_COST_SOURCE_HEAD,
        recovered_initial_freeze_path=recovered_initial_freeze_path,
        lifecycle_path=lifecycle_path,
        initial_cost_preflight_path=initial_cost_preflight_path,
        overlay_path=overlay_path,
        closure_path=closure_path,
        freeze_path=freeze_path,
        reconciliation_path=reconciliation_path,
        handoff_path=handoff_path,
        initial_authority_path=initial_authority_path,
        pricing_path=pricing_path,
    )
    if recomputed != cost:
        raise B4ReopenRebuttalRuntimeError(
            "current deterministic code/source artifacts do not reproduce frozen Rebuttal cost preflight"
        )

    selection = v01._read_object(
        selection_authority_path,
        label="historical Rebuttal selected-model authority",
    )
    v01.verify_selection_authority(selection)
    recovered = v01._read_object(
        recovered_initial_freeze_path,
        label="recovered Initial freeze",
    )
    _, initial_plan, _, pricing = v01.load_and_build_reopen_initial_runtime_plan(
        cost_preflight_path=initial_cost_preflight_path,
        lifecycle_path=lifecycle_path,
        overlay_path=overlay_path,
        closure_path=closure_path,
        freeze_path=freeze_path,
        reconciliation_path=reconciliation_path,
        handoff_path=handoff_path,
        initial_authority_path=initial_authority_path,
        pricing_path=pricing_path,
    )
    v01.cost_v02.verify_recovered_initial_freeze(recovered, initial_plan=initial_plan)
    selected = v01._selected_model()
    bundles = {item.candidate_id: item.bundle for item in initial_plan[::3]}
    contexts = [
        v01.cost_v01._candidate_context(
            candidate_id=candidate,
            recovered=recovered,
            initial_plan=initial_plan,
        )
        for candidate in v01.EXPECTED_CANDIDATES
    ]
    rows = {row["candidate_id"]: row for row in cost["request_rows"]}
    if tuple(rows) != v01.EXPECTED_CANDIDATES:
        raise B4ReopenRebuttalRuntimeError("frozen Rebuttal request row order drift")

    plan: list[v01.RebuttalRuntimePlanItem] = []
    for index, context in enumerate(contexts, start=1):
        candidate = str(context["candidate_id"])
        opposing = {
            v01.CouncilLane(key): tuple(value)
            for key, value in context["opposing_claim_ids_by_lane"].items()
        }
        request = v01.build_bounded_rebuttal_request_v01(
            model_candidate=selected,
            bundle=bundles[candidate],
            model_input=context["model_input"],
            initial_opinion_ids=tuple(context["initial_opinion_ids"]),
            initial_opinion_hashes=tuple(context["initial_opinion_hashes"]),
            opposing_claim_ids_by_lane=opposing,
            allowed_uncertainty_refs=tuple(context["allowed_uncertainty_refs"]),
        )
        byte_count = v01.cost_v01._request_body_utf8_bytes(request.request_payload)
        frozen = rows[candidate]
        expected_fields = {
            "candidate_key": "R3",
            "model": selected.model,
            "reasoning_effort": selected.reasoning_effort,
            "context_hash": context["context_hash"],
            "effective_bundle_hash": bundles[candidate].bundle_hash,
            "effective_model_input_hash": context["effective_model_input_hash"],
            "rebuttal_model_input_hash": context["rebuttal_model_input_hash"],
            "request_hash": request.request_hash,
            "request_body_utf8_bytes": byte_count,
            "max_output_tokens": v01.EXPECTED_MAX_OUTPUT_TOKENS,
        }
        for key, expected in expected_fields.items():
            if frozen.get(key) != expected:
                raise B4ReopenRebuttalRuntimeError(
                    f"{candidate} reconstructed Rebuttal request differs from cost preflight: {key}"
                )
        if request.request_payload.get("max_output_tokens") != v01.EXPECTED_MAX_OUTPUT_TOKENS:
            raise B4ReopenRebuttalRuntimeError("reconstructed Rebuttal output cap drift")
        required_unknown_refs = required_unknown_refs_from_context(context)
        plan.append(
            v01.RebuttalRuntimePlanItem(
                dispatch_index=index,
                candidate_id=candidate,
                context_hash=str(context["context_hash"]),
                bundle=bundles[candidate],
                model_input=context["model_input"],
                initial_opinion_ids=tuple(context["initial_opinion_ids"]),
                initial_opinion_hashes=tuple(context["initial_opinion_hashes"]),
                opposing_claim_ids_by_lane=opposing,
                allowed_uncertainty_refs=tuple(context["allowed_uncertainty_refs"]),
                required_unknown_refs=required_unknown_refs,
                request=request,
                request_body_utf8_bytes=byte_count,
            )
        )
    if len(plan) != v01.EXPECTED_CALLS or tuple(item.candidate_id for item in plan) != v01.EXPECTED_CANDIDATES:
        raise B4ReopenRebuttalRuntimeError(
            "reopen Rebuttal runtime plan must contain NVDA/MSFT/META exactly once"
        )
    loaded_pricing = v01.load_initial_runtime_pricing(Path(pricing_path))
    if loaded_pricing != pricing or pricing.get("pricing_hash") != cost.get("pricing_hash"):
        raise B4ReopenRebuttalRuntimeError("runtime pricing differs from cost preflight")
    return v01.BoundReopenRebuttalRuntime(
        cost_preflight=cost,
        selection_authority=selection,
        recovered_initial_freeze=recovered,
        plan=tuple(plan),
        pricing=pricing,
    )
