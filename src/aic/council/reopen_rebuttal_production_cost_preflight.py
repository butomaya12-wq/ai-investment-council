from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import COUNCIL_OPINION_V1, MATERIAL_CLAIM_V1

from .initial_runtime import _validate_processed_record
from .initial_runtime_cost_v02 import runtime_cost_upper_bound_usd
from .model_policy import (
    REBUTTAL_MODEL_LADDER,
    STAGE_MAX_OUTPUT_TOKENS,
    CouncilModelStage,
)
from .models import CouncilLane
from .rebuttal_schema_repair_v01 import (
    REBUTTAL_CLAIM_TYPE_CONTRACT_VERSION,
    REBUTTAL_OPPOSING_LANE_CONTRACT_VERSION,
    REBUTTAL_PROMOTION_SEMANTICS_CONTRACT_VERSION,
    REBUTTAL_SCHEMA_REPAIR_VERSION,
    REBUTTAL_SCHEMA_VERSION,
    build_bounded_rebuttal_request_v01,
)
from .reopen_initial_runtime import (
    EXPECTED_EFFECTIVE_INPUT_MANIFEST_HASH,
    EXPECTED_REQUEST_MANIFEST_HASH,
    load_and_build_reopen_initial_runtime_plan,
)
from .reopen_lifecycle_plan import EXPECTED_REBUTTAL_SELECTED


ARTIFACT_VERSION = "B4_REOPEN_REBUTTAL_PRODUCTION_COST_PREFLIGHT_v0_1"
PASS_STATUS = "B4_REOPEN_REBUTTAL_PRODUCTION_COST_PREFLIGHT_ZERO_CALL_PASS"
NEXT_GATE = "B4_REOPEN_REBUTTAL_PRODUCTION_RUNTIME_DRY_ZERO_CALL"

EXPECTED_RECOVERED_INITIAL_FREEZE_HASH = (
    "b98a3fbb2ce43cd9cab0d97b28ec62c1819ea5c777d8ff0a0dc36eb7628e8440"
)
EXPECTED_RECOVERED_INITIAL_FREEZE_VERSION = (
    "B4_REOPEN_INITIAL_COUNCIL_FREEZE_RECOVERED_v0_2"
)
EXPECTED_RECOVERED_INITIAL_STATUS = (
    "B4_REOPEN_INITIAL_COUNCIL_FROZEN_AFTER_UNKNOWN_DISPATCH_RECOVERY"
)
EXPECTED_RECOVERY_AUTH_HASH = (
    "984bb7ff5ab7d2ad64de8fae3f6e8a5db63e04323759c8388f04272078b58611"
)
EXPECTED_RECOVERY_RECEIPT_HASH = (
    "c927f37e36805d8c97797797cc09e209116165b7d5ab7a11005cd1a27bb5176b"
)
EXPECTED_REOPEN_LIFECYCLE_HASH = (
    "fabd17c8615b7bd4dec00d2a6c09c688d80b91e0730e8f02bb9f99289a7c6f55"
)
EXPECTED_CANDIDATES = ("NVDA", "MSFT", "META")
EXPECTED_REBUTTAL_CALLS = 3
EXPECTED_KNOWN_INITIAL_COST_USD = Decimal("0.3595905")
EXPECTED_INITIAL_SPEND_UPPER_USD = Decimal("0.4963025")


class B4ReopenRebuttalCostPreflightError(ValueError):
    pass


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenRebuttalCostPreflightError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise B4ReopenRebuttalCostPreflightError(f"{label} root must be object")
    return value


def _decimal(value: object, *, field: str) -> Decimal:
    if not isinstance(value, str):
        raise B4ReopenRebuttalCostPreflightError(f"{field} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise B4ReopenRebuttalCostPreflightError(f"{field} invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise B4ReopenRebuttalCostPreflightError(
            f"{field} must be finite and non-negative"
        )
    return parsed


def _decimal_text(value: Decimal) -> str:
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


def _verify_self_hash(
    payload: Mapping[str, Any],
    *,
    field: str,
    expected: str | None,
    label: str,
) -> str:
    observed = payload.get(field)
    if not isinstance(observed, str) or len(observed) != 64:
        raise B4ReopenRebuttalCostPreflightError(f"{label} {field} missing")
    if expected is not None and observed != expected:
        raise B4ReopenRebuttalCostPreflightError(f"{label} {field} drift")
    if observed != canonical_sha256(payload, exclude_fields=(field,)):
        raise B4ReopenRebuttalCostPreflightError(f"{label} self-hash mismatch")
    return observed


def _selected_rebuttal_candidate() -> Any:
    matches = [item for item in REBUTTAL_MODEL_LADDER if item.candidate_key == "R3"]
    if len(matches) != 1:
        raise B4ReopenRebuttalCostPreflightError("R3 model-policy candidate missing")
    selected = matches[0]
    observed = {
        "candidate_key": selected.candidate_key,
        "stage": selected.stage.value,
        "model": selected.model,
        "reasoning_effort": selected.reasoning_effort,
        "ladder_position": selected.ladder_position,
    }
    if observed != EXPECTED_REBUTTAL_SELECTED:
        raise B4ReopenRebuttalCostPreflightError("R3 model-policy identity drift")
    return selected


def verify_reopen_lifecycle_for_rebuttal(lifecycle: Mapping[str, Any]) -> str:
    lifecycle_hash = _verify_self_hash(
        lifecycle,
        field="artifact_hash",
        expected=EXPECTED_REOPEN_LIFECYCLE_HASH,
        label="B4 reopen lifecycle",
    )
    if lifecycle.get("artifact_version") != "B4_REOPEN_LIFECYCLE_PLAN_v0_2":
        raise B4ReopenRebuttalCostPreflightError("reopen lifecycle version drift")
    if lifecycle.get("status") != "B4_REOPEN_LIFECYCLE_PLAN_ZERO_CALL_PASS":
        raise B4ReopenRebuttalCostPreflightError("reopen lifecycle is not PASS")
    if lifecycle.get("model_eval_reruns_required") is not False:
        raise B4ReopenRebuttalCostPreflightError("Rebuttal model eval rerun unexpectedly required")
    if lifecycle.get("planned_model_eval_calls") != 0:
        raise B4ReopenRebuttalCostPreflightError("Rebuttal model eval calls are not zero")

    reuse = lifecycle.get("selected_model_authority_reuse")
    if not isinstance(reuse, Mapping) or not isinstance(reuse.get("REBUTTAL"), Mapping):
        raise B4ReopenRebuttalCostPreflightError("Rebuttal selected-model lifecycle binding missing")
    rebuttal = reuse["REBUTTAL"]
    if rebuttal.get("reused") is not True:
        raise B4ReopenRebuttalCostPreflightError("Rebuttal selected-model identity is not frozen")
    if rebuttal.get("selected_model") != EXPECTED_REBUTTAL_SELECTED:
        raise B4ReopenRebuttalCostPreflightError("Rebuttal selected model changed")
    if rebuttal.get("eval_calls_repeated") != 0:
        raise B4ReopenRebuttalCostPreflightError("Rebuttal eval calls were repeated")
    if rebuttal.get("exact_historical_authority_revalidation_required_before_paid_dispatch") is not True:
        raise B4ReopenRebuttalCostPreflightError(
            "historical Rebuttal selection-authority revalidation boundary missing"
        )

    stages = lifecycle.get("fresh_production_stages")
    if not isinstance(stages, list):
        raise B4ReopenRebuttalCostPreflightError("fresh production stage plan missing")
    rows = [row for row in stages if isinstance(row, Mapping) and row.get("stage") == "REBUTTAL"]
    if len(rows) != 1:
        raise B4ReopenRebuttalCostPreflightError("fresh Rebuttal production stage missing")
    stage = rows[0]
    if stage.get("selected_model") != EXPECTED_REBUTTAL_SELECTED:
        raise B4ReopenRebuttalCostPreflightError("fresh Rebuttal model identity drift")
    if stage.get("fresh_model_calls_max") != EXPECTED_REBUTTAL_CALLS:
        raise B4ReopenRebuttalCostPreflightError("fresh Rebuttal call topology drift")
    if stage.get("max_output_tokens_per_call") != STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.REBUTTAL]:
        raise B4ReopenRebuttalCostPreflightError("fresh Rebuttal output cap drift")
    if lifecycle.get("owner_cost_approval_required") is not True:
        raise B4ReopenRebuttalCostPreflightError("owner cost approval boundary missing")
    if lifecycle.get("model_calls_authorized") is not False:
        raise B4ReopenRebuttalCostPreflightError("lifecycle unexpectedly grants model-call authority")
    if lifecycle.get("provider_reads_authorized") is not False:
        raise B4ReopenRebuttalCostPreflightError("lifecycle unexpectedly grants provider-read authority")
    if lifecycle.get("automatic_repair_calls_authorized") != 0 or lifecycle.get("automatic_retries") != 0:
        raise B4ReopenRebuttalCostPreflightError("lifecycle repair/retry boundary drift")
    if lifecycle.get("live_money") != "PROHIBITED":
        raise B4ReopenRebuttalCostPreflightError("lifecycle live-money boundary drift")
    _selected_rebuttal_candidate()
    return lifecycle_hash


def verify_recovered_initial_freeze(
    recovered: Mapping[str, Any],
    *,
    initial_plan: Sequence[Any],
) -> str:
    freeze_hash = _verify_self_hash(
        recovered,
        field="artifact_hash",
        expected=EXPECTED_RECOVERED_INITIAL_FREEZE_HASH,
        label="recovered Initial freeze",
    )
    if recovered.get("artifact_version") != EXPECTED_RECOVERED_INITIAL_FREEZE_VERSION:
        raise B4ReopenRebuttalCostPreflightError("recovered Initial freeze version drift")
    if recovered.get("status") != EXPECTED_RECOVERED_INITIAL_STATUS:
        raise B4ReopenRebuttalCostPreflightError("recovered Initial freeze is not frozen")
    if recovered.get("request_manifest_hash") != EXPECTED_REQUEST_MANIFEST_HASH:
        raise B4ReopenRebuttalCostPreflightError("Initial request-manifest lineage drift")
    if recovered.get("effective_input_manifest_hash") != EXPECTED_EFFECTIVE_INPUT_MANIFEST_HASH:
        raise B4ReopenRebuttalCostPreflightError("effective-input manifest lineage drift")
    if recovered.get("recovery_paid_authorization_artifact_hash") != EXPECTED_RECOVERY_AUTH_HASH:
        raise B4ReopenRebuttalCostPreflightError("recovery authorization lineage drift")
    if recovered.get("recovery_receipt_hash") != EXPECTED_RECOVERY_RECEIPT_HASH:
        raise B4ReopenRebuttalCostPreflightError("recovery receipt lineage drift")
    if recovered.get("candidate_order") != list(EXPECTED_CANDIDATES):
        raise B4ReopenRebuttalCostPreflightError("recovered Initial candidate order drift")
    if recovered.get("initial_opinion_count") != 9:
        raise B4ReopenRebuttalCostPreflightError("Rebuttal requires exactly nine Initial opinions")
    if recovered.get("reused_source_processed_opinion_count") != 8:
        raise B4ReopenRebuttalCostPreflightError("recovered Initial reusable opinion count drift")
    if recovered.get("fresh_recovery_processed_opinion_count") != 1:
        raise B4ReopenRebuttalCostPreflightError("recovered Initial fresh recovery count drift")
    if recovered.get("source_provider_dispatch_attempts") != 9:
        raise B4ReopenRebuttalCostPreflightError("source Initial dispatch count drift")
    if recovered.get("recovery_provider_dispatch_attempts") != 1:
        raise B4ReopenRebuttalCostPreflightError("recovery dispatch count drift")
    if recovered.get("aggregate_provider_dispatch_attempts") != 10:
        raise B4ReopenRebuttalCostPreflightError("aggregate Initial dispatch accounting drift")
    if recovered.get("model_calls_known_completed") != 9:
        raise B4ReopenRebuttalCostPreflightError("known completed Initial model-call count drift")
    if _decimal(recovered.get("known_actual_cost_usd"), field="known Initial cost") != EXPECTED_KNOWN_INITIAL_COST_USD:
        raise B4ReopenRebuttalCostPreflightError("known Initial cost drift")
    if _decimal(recovered.get("aggregate_initial_spend_upper_bound_usd"), field="Initial spend upper bound") != EXPECTED_INITIAL_SPEND_UPPER_USD:
        raise B4ReopenRebuttalCostPreflightError("Initial spend upper bound drift")
    if recovered.get("aggregate_cost_receipt_status") != "PARTIAL_UNKNOWN_HISTORICAL_DISPATCH":
        raise B4ReopenRebuttalCostPreflightError("historical unknown-cost semantics were lost")
    if recovered.get("source_unknown_dispatch_cost_remains_unknown") is not True:
        raise B4ReopenRebuttalCostPreflightError("historical unknown dispatch was incorrectly resolved")
    if recovered.get("initial_freeze_barrier") is not True:
        raise B4ReopenRebuttalCostPreflightError("Initial freeze barrier not crossed")
    if recovered.get("rebuttal_cost_requires_this_fresh_initial_freeze") is not True:
        raise B4ReopenRebuttalCostPreflightError("Rebuttal cost lineage requirement missing")
    if recovered.get("rebuttal_authorized") is not False:
        raise B4ReopenRebuttalCostPreflightError("Initial freeze unexpectedly authorizes Rebuttal")
    if recovered.get("judge_authorized") is not False:
        raise B4ReopenRebuttalCostPreflightError("Initial freeze unexpectedly authorizes Judge")
    if recovered.get("recovery_rerun_authorized") is not False:
        raise B4ReopenRebuttalCostPreflightError("recovery rerun boundary drift")
    if recovered.get("provider_reads") != 0 or recovered.get("broker_writes") != 0 or recovered.get("alpaca_orders") != 0:
        raise B4ReopenRebuttalCostPreflightError("Initial freeze side-effect counters drift")
    if recovered.get("live_money") != "PROHIBITED":
        raise B4ReopenRebuttalCostPreflightError("Initial freeze live-money boundary drift")
    if recovered.get("next_gate") != "B4_REOPEN_REBUTTAL_PRODUCTION_COST_PREFLIGHT_ZERO_CALL":
        raise B4ReopenRebuttalCostPreflightError("Initial freeze next-gate drift")

    if len(initial_plan) != 9:
        raise B4ReopenRebuttalCostPreflightError("reconstructed Initial plan must contain nine calls")
    records = recovered.get("processed_records")
    if not isinstance(records, list) or len(records) != 9:
        raise B4ReopenRebuttalCostPreflightError("recovered Initial records must contain nine entries")

    opinion_ids: list[str] = []
    opinion_hashes: list[str] = []
    for raw, planned in zip(records, initial_plan, strict=True):
        if not isinstance(raw, Mapping):
            raise B4ReopenRebuttalCostPreflightError("Initial processed record malformed")
        try:
            opinion_id, opinion_hash = _validate_processed_record(raw)
        except Exception as exc:
            raise B4ReopenRebuttalCostPreflightError(
                f"Initial processed record invalid: {exc}"
            ) from exc
        if raw.get("candidate_id") != planned.candidate_id or raw.get("lane") != planned.lane.value:
            raise B4ReopenRebuttalCostPreflightError("Initial processed-record identity/order drift")
        if raw.get("request_hash") != planned.request.request_hash:
            raise B4ReopenRebuttalCostPreflightError("Initial processed-record request lineage drift")
        opinion_raw = raw.get("council_opinion")
        claims_raw = raw.get("material_claims")
        if not isinstance(opinion_raw, Mapping) or not isinstance(claims_raw, list):
            raise B4ReopenRebuttalCostPreflightError("Initial opinion/material claims missing")
        opinion = COUNCIL_OPINION_V1.model_validate(dict(opinion_raw))
        if opinion.input_snapshot_hash != planned.bundle.bundle_hash:
            raise B4ReopenRebuttalCostPreflightError("Initial opinion effective-bundle lineage drift")
        if opinion.candidate_packet_hash != planned.bundle.candidate_packet_hash:
            raise B4ReopenRebuttalCostPreflightError("Initial opinion candidate-packet lineage drift")
        claims = tuple(MATERIAL_CLAIM_V1.model_validate(item) for item in claims_raw)
        if tuple(claim.claim_id for claim in claims) != tuple(opinion.material_claim_ids):
            raise B4ReopenRebuttalCostPreflightError("Initial opinion/material-claim closure drift")
        if any(claim.candidate_id != planned.candidate_id for claim in claims):
            raise B4ReopenRebuttalCostPreflightError("Initial promoted-claim candidate drift")
        opinion_ids.append(opinion_id)
        opinion_hashes.append(opinion_hash)

    if recovered.get("initial_opinion_ids") != opinion_ids:
        raise B4ReopenRebuttalCostPreflightError("Initial opinion-id freeze drift")
    if recovered.get("initial_opinion_hashes") != opinion_hashes:
        raise B4ReopenRebuttalCostPreflightError("Initial opinion-hash freeze drift")
    if len(set(opinion_ids)) != 9 or len(set(opinion_hashes)) != 9:
        raise B4ReopenRebuttalCostPreflightError("Initial opinions must be unique")
    return freeze_hash


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _candidate_context(
    *,
    candidate_id: str,
    recovered: Mapping[str, Any],
    initial_plan: Sequence[Any],
) -> dict[str, Any]:
    planned = [item for item in initial_plan if item.candidate_id == candidate_id]
    records = [
        item
        for item in recovered["processed_records"]
        if isinstance(item, Mapping) and item.get("candidate_id") == candidate_id
    ]
    if len(planned) != 3 or len(records) != 3:
        raise B4ReopenRebuttalCostPreflightError(
            f"{candidate_id} requires three Initial lanes"
        )
    expected_lanes = [CouncilLane.BULL.value, CouncilLane.BEAR.value, CouncilLane.RED_TEAM.value]
    if [item.lane.value for item in planned] != expected_lanes:
        raise B4ReopenRebuttalCostPreflightError(f"{candidate_id} reconstructed lane order drift")
    if [item.get("lane") for item in records] != expected_lanes:
        raise B4ReopenRebuttalCostPreflightError(f"{candidate_id} frozen lane order drift")

    bundle = planned[0].bundle
    candidate_model_input = dict(planned[0].model_input)
    for item in planned[1:]:
        if item.bundle.bundle_hash != bundle.bundle_hash:
            raise B4ReopenRebuttalCostPreflightError(f"{candidate_id} effective bundle differs by lane")
        if item.model_input.get("model_input_hash") != candidate_model_input.get("model_input_hash"):
            raise B4ReopenRebuttalCostPreflightError(f"{candidate_id} effective model input differs by lane")
    if candidate_model_input.get("data_gap_refs") != []:
        raise B4ReopenRebuttalCostPreflightError(f"{candidate_id} effective data gaps are not closed")

    opinion_ids: list[str] = []
    opinion_hashes: list[str] = []
    claim_ids_by_lane: dict[str, tuple[str, ...]] = {}
    uncertainty_refs: list[str] = []
    frozen_opinions: list[dict[str, Any]] = []
    for raw in records:
        opinion = COUNCIL_OPINION_V1.model_validate(dict(raw["council_opinion"]))
        claims = tuple(MATERIAL_CLAIM_V1.model_validate(item) for item in raw["material_claims"])
        opinion_ids.append(opinion.opinion_id)
        opinion_hashes.append(str(raw["council_opinion_hash"]))
        claim_ids_by_lane[str(raw["lane"])] = tuple(claim.claim_id for claim in claims)
        for ref in opinion.data_gap_refs:
            if ref not in uncertainty_refs:
                uncertainty_refs.append(ref)
        frozen_opinions.append(
            {
                "lane": raw["lane"],
                "council_opinion": dict(raw["council_opinion"]),
                "council_opinion_hash": raw["council_opinion_hash"],
                "material_claims": [dict(item) for item in raw["material_claims"]],
                "claim_metadata": [dict(item) for item in raw["claim_metadata"]],
            }
        )

    opposing: dict[str, list[str]] = {}
    for responding_lane in expected_lanes:
        values: list[str] = []
        for owner_lane, ids in claim_ids_by_lane.items():
            if owner_lane != responding_lane:
                values.extend(ids)
        opposing[responding_lane] = list(_ordered_unique(values))
        if not opposing[responding_lane]:
            raise B4ReopenRebuttalCostPreflightError(
                f"{candidate_id}/{responding_lane} has no opposing findings"
            )

    model_input = {
        "candidate_model_input": candidate_model_input,
        "initial_council": {
            "initial_freeze_artifact_hash": EXPECTED_RECOVERED_INITIAL_FREEZE_HASH,
            "initial_opinions": frozen_opinions,
        },
    }
    context: dict[str, Any] = {
        "candidate_id": candidate_id,
        "effective_bundle_hash": bundle.bundle_hash,
        "effective_model_input_hash": candidate_model_input["model_input_hash"],
        "rebuttal_model_input_hash": canonical_sha256(model_input),
        "model_input": model_input,
        "initial_opinion_ids": opinion_ids,
        "initial_opinion_hashes": opinion_hashes,
        "opposing_claim_ids_by_lane": opposing,
        "allowed_uncertainty_refs": uncertainty_refs,
        "effective_data_gap_refs": [],
    }
    context["context_hash"] = canonical_sha256(context)
    return context


def build_reopen_rebuttal_production_cost_preflight(
    *,
    code_commit_sha: str,
    recovered_initial_freeze: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    initial_plan: Sequence[Any],
    pricing: Mapping[str, Any],
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise B4ReopenRebuttalCostPreflightError("exact lowercase git SHA required")
    lifecycle_hash = verify_reopen_lifecycle_for_rebuttal(lifecycle)
    freeze_hash = verify_recovered_initial_freeze(
        recovered_initial_freeze,
        initial_plan=initial_plan,
    )
    selected = _selected_rebuttal_candidate()
    output_cap = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.REBUTTAL]

    pricing_hash = _verify_self_hash(
        pricing,
        field="pricing_hash",
        expected=None,
        label="OpenAI runtime pricing",
    )
    if pricing.get("pricing_version") != "OPENAI_TEXT_PRICING_2026_08_30_CACHE_WRITE_AWARE":
        raise B4ReopenRebuttalCostPreflightError("runtime pricing version drift")
    cache_write = pricing.get("cache_write")
    if not isinstance(cache_write, Mapping) or cache_write.get("input_rate_multiplier") != "1.25":
        raise B4ReopenRebuttalCostPreflightError("cache-write pricing drift")

    bundles = {item.candidate_id: item.bundle for item in initial_plan[::3]}
    contexts = [
        _candidate_context(
            candidate_id=candidate,
            recovered=recovered_initial_freeze,
            initial_plan=initial_plan,
        )
        for candidate in EXPECTED_CANDIDATES
    ]
    request_rows: list[dict[str, Any]] = []
    total = Decimal("0")
    for context in contexts:
        candidate = str(context["candidate_id"])
        opposing = {
            CouncilLane(key): tuple(value)
            for key, value in context["opposing_claim_ids_by_lane"].items()
        }
        request = build_bounded_rebuttal_request_v01(
            model_candidate=selected,
            bundle=bundles[candidate],
            model_input=context["model_input"],
            initial_opinion_ids=tuple(context["initial_opinion_ids"]),
            initial_opinion_hashes=tuple(context["initial_opinion_hashes"]),
            opposing_claim_ids_by_lane=opposing,
            allowed_uncertainty_refs=tuple(context["allowed_uncertainty_refs"]),
        )
        if request.request_payload.get("model") != selected.model:
            raise B4ReopenRebuttalCostPreflightError("Rebuttal request model drift")
        reasoning = request.request_payload.get("reasoning")
        if not isinstance(reasoning, Mapping) or reasoning.get("effort") != selected.reasoning_effort:
            raise B4ReopenRebuttalCostPreflightError("Rebuttal request reasoning effort drift")
        if request.request_payload.get("max_output_tokens") != output_cap:
            raise B4ReopenRebuttalCostPreflightError("Rebuttal request output cap drift")
        byte_count = _request_body_utf8_bytes(request.request_payload)
        cost = runtime_cost_upper_bound_usd(
            model=selected.model,
            input_tokens_upper_bound=byte_count,
            output_tokens_upper_bound=output_cap,
            call_count=1,
            pricing=pricing,
        )
        total += cost
        request_rows.append(
            {
                "candidate_id": candidate,
                "candidate_key": selected.candidate_key,
                "model": selected.model,
                "reasoning_effort": selected.reasoning_effort,
                "context_hash": context["context_hash"],
                "effective_bundle_hash": context["effective_bundle_hash"],
                "effective_model_input_hash": context["effective_model_input_hash"],
                "rebuttal_model_input_hash": context["rebuttal_model_input_hash"],
                "initial_opinion_ids": list(context["initial_opinion_ids"]),
                "initial_opinion_hashes": list(context["initial_opinion_hashes"]),
                "request_hash": request.request_hash,
                "request_body_utf8_bytes": byte_count,
                "input_tokens_upper_bound": byte_count,
                "max_output_tokens": output_cap,
                "schema_hash": canonical_sha256(request.request_payload["text"]["format"]["schema"]),
                "prompt_contract_version": request.prompt_contract_version,
                "prompt_version": request.prompt_version,
                "prompt_hash": request.prompt_hash,
                "schema_version": request.schema_version,
                "input_hash": request.input_hash,
                "per_call_cost_upper_bound_usd": _decimal_text(cost),
            }
        )

    if [row["candidate_id"] for row in request_rows] != list(EXPECTED_CANDIDATES):
        raise B4ReopenRebuttalCostPreflightError("Rebuttal request order drift")
    request_manifest_hash = canonical_sha256(
        {
            "rows": [
                {
                    "candidate_id": row["candidate_id"],
                    "candidate_key": row["candidate_key"],
                    "context_hash": row["context_hash"],
                    "request_hash": row["request_hash"],
                    "request_body_utf8_bytes": row["request_body_utf8_bytes"],
                    "max_output_tokens": row["max_output_tokens"],
                }
                for row in request_rows
            ]
        }
    )
    aggregate_upper = EXPECTED_INITIAL_SPEND_UPPER_USD + total

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_recovered_initial_freeze_hash": freeze_hash,
        "source_reopen_lifecycle_plan_hash": lifecycle_hash,
        "source_initial_request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "source_effective_input_manifest_hash": EXPECTED_EFFECTIVE_INPUT_MANIFEST_HASH,
        "selected_rebuttal_model": dict(EXPECTED_REBUTTAL_SELECTED),
        "historical_rebuttal_outputs_reused": False,
        "historical_rebuttal_request_hashes_reused": False,
        "historical_rebuttal_selected_model_authority_used_as_request_evidence": False,
        "historical_rebuttal_selection_authority_revalidation_deferred_to_paid_runtime_gate": True,
        "candidate_order": list(EXPECTED_CANDIDATES),
        "planned_paid_calls_max": EXPECTED_REBUTTAL_CALLS,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "max_output_tokens_per_call": output_cap,
        "request_rows": request_rows,
        "request_manifest_hash": request_manifest_hash,
        "pricing_version": pricing["pricing_version"],
        "pricing_hash": pricing_hash,
        "pricing_as_of_date": pricing["as_of_date"],
        "cache_write_input_rate_multiplier": cache_write["input_rate_multiplier"],
        "input_token_upper_bound_method": (
            "CONSERVATIVE: one input token per UTF-8 serialized request-body byte; "
            "all input tokens additionally assumed eligible for cache-write billing"
        ),
        "cached_input_discount_assumed_for_upper_bound": False,
        "rebuttal_exact_cost_upper_bound_usd": _decimal_text(total),
        "source_initial_known_actual_cost_usd": _decimal_text(EXPECTED_KNOWN_INITIAL_COST_USD),
        "source_initial_spend_upper_bound_usd": _decimal_text(EXPECTED_INITIAL_SPEND_UPPER_USD),
        "source_initial_cost_receipt_status": "PARTIAL_UNKNOWN_HISTORICAL_DISPATCH",
        "historical_unknown_initial_dispatch_cost_remains_unknown": True,
        "aggregate_initial_plus_rebuttal_spend_upper_bound_usd": _decimal_text(aggregate_upper),
        "owner_cost_approval_required": True,
        "rebuttal_paid_dispatch_authorized": False,
        "judge_paid_dispatch_authorized": False,
        "model_calls_authorized": False,
        "provider_reads_authorized": False,
        "rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
        "schema_repair_version": REBUTTAL_SCHEMA_REPAIR_VERSION,
        "schema_version": REBUTTAL_SCHEMA_VERSION,
        "promotion_semantics_contract_version": REBUTTAL_PROMOTION_SEMANTICS_CONTRACT_VERSION,
        "opposing_lane_contract_version": REBUTTAL_OPPOSING_LANE_CONTRACT_VERSION,
        "claim_type_contract_version": REBUTTAL_CLAIM_TYPE_CONTRACT_VERSION,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def load_and_build_b4_reopen_rebuttal_production_cost_preflight(
    *,
    code_commit_sha: str,
    recovered_initial_freeze_path: str | Path,
    lifecycle_path: str | Path,
    initial_cost_preflight_path: str | Path,
    overlay_path: str | Path,
    closure_path: str | Path,
    freeze_path: str | Path,
    reconciliation_path: str | Path,
    handoff_path: str | Path,
    initial_authority_path: str | Path,
    pricing_path: str | Path,
) -> dict[str, Any]:
    recovered = _read_object(
        recovered_initial_freeze_path,
        label="recovered B4 reopen Initial freeze",
    )
    lifecycle = _read_object(lifecycle_path, label="B4 reopen lifecycle plan")
    _, initial_plan, _, pricing = load_and_build_reopen_initial_runtime_plan(
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
    return build_reopen_rebuttal_production_cost_preflight(
        code_commit_sha=code_commit_sha,
        recovered_initial_freeze=recovered,
        lifecycle=lifecycle,
        initial_plan=initial_plan,
        pricing=pricing,
    )
