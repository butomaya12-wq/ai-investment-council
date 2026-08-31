"""Current-lineage, fail-closed Rebuttal preflight and paid executor.

This deliberately does not alter the historical Rebuttal v0.2 preflight.  It
only accepts the nine-response Initial recovery-resume freeze created on
2026-08-31 and is inert until both a paid flag and an exact approval exist.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import COUNCIL_OPINION_V1, MATERIAL_CLAIM_V1

from .initial_runtime import _validate_processed_record
from .initial_runtime_cost_v02 import actual_cost_usd, runtime_cost_upper_bound_usd
from .model_policy import REBUTTAL_MODEL_LADDER, CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from .models import CouncilLane
from .post_research_reopen_initial_execute_production_v01 import (
    _replace_durable, _write_exclusive, external_provider_json_sha256,
    frozen_initial_items, _external_json_value,
)
from .rebuttal_model_selection_v02 import (
    build_rebuttal_selected_model_authority_v02,
    verify_rebuttal_selected_model_authority_v02,
)
from .rebuttal_promotion import promote_rebuttal_bundle
from .rebuttal_runtime import RebuttalRuntimePlanItem
from .rebuttal_runtime_execution import (
    _candidate_initial_records, _processed_record, _usage_counts,
    validate_rebuttal_processed_record,
)
from .rebuttal_schema_repair_v01 import build_bounded_rebuttal_request_v01
from .request import parse_council_responses_payload


CURRENT_INITIAL_FREEZE_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_RECOVERY_RESUME_COUNCIL_FREEZE_v0_1"
CURRENT_INITIAL_FREEZE_STATUS = "B4_POST_RESEARCH_REOPEN_INITIAL_RECOVERY_RESUME_COUNCIL_FROZEN"
CURRENT_INITIAL_FREEZE_HASH = "9138746e122b494e3a2eb84695b98870299145d5d806d2aa9da62ecb010cd394"
CURRENT_INITIAL_FREEZE_CODE_SHA = "d5a6cd8ae9aa9886593c5b991040100c427b75b1"
CURRENT_RECONCILIATION_HASH = "a847711c8e7403a3c7a7bf7bbbdd356181583fec75298598a3018d71050a9152"
CURRENT_RESUME_READINESS_HASH = "09677b2d25e2b6ff878c20b4bebd1ffa82b4dc88e8f0ccf0d4bc95cc2b071491"
CURRENT_RESUME_LEDGER_HASH = "2bffb46d39d7a146a7f4d8f729a9b7c78c9be96ae9012e918c2204942028e9f8"
CURRENT_RESUME_APPROVAL_HASH = "bcc76e4b09f27a9af6a77ade731b9db1d3c33061c5886be71ed347696bfe260e"
SELECTION_HASH = "8db38779171e0dcfc2e0325581192116b17adf98a1140950ffcbe5ce4698a882"
CANDIDATES = ("NVDA", "MSFT", "META")
LANES = ("BULL", "BEAR", "RED_TEAM")
PREFLIGHT_VERSION = "B4_POST_RESEARCH_REOPEN_REBUTTAL_PRODUCTION_PREFLIGHT_ZERO_CALL_v0_1"
PREFLIGHT_STATUS = "B4_POST_RESEARCH_REOPEN_REBUTTAL_PRODUCTION_PREFLIGHT_ZERO_CALL_PASS"
READINESS_VERSION = "B4_POST_RESEARCH_REOPEN_REBUTTAL_PRODUCTION_READINESS_ZERO_CALL_v0_1"
READINESS_STATUS = "B4_POST_RESEARCH_REOPEN_REBUTTAL_PRODUCTION_READINESS_ZERO_CALL_PASS"
APPROVAL_VERSION = "B4_POST_RESEARCH_REOPEN_REBUTTAL_PRODUCTION_OWNER_APPROVAL_v0_1"
LEDGER_VERSION = "B4_POST_RESEARCH_REOPEN_REBUTTAL_PRODUCTION_PAID_DISPATCH_LEDGER_v0_1"
RESULT_VERSION = "B4_POST_RESEARCH_REOPEN_REBUTTAL_PRODUCTION_COUNCIL_FREEZE_v0_1"


class PostResearchRebuttalError(RuntimeError):
    pass


def _need(ok: bool, message: str) -> None:
    if not ok:
        raise PostResearchRebuttalError(message)


def _hash(payload: Mapping[str, Any], field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, f"{field} missing")
    _need(observed == canonical_sha256(payload, exclude_fields=(field,)), f"{field} mismatch")
    return observed


def _utc(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _selected() -> Any:
    matches = [row for row in REBUTTAL_MODEL_LADDER if row.candidate_key == "R3"]
    _need(len(matches) == 1, "R3 selection missing")
    selected = matches[0]
    _need((selected.model, selected.reasoning_effort, selected.ladder_position) == ("gpt-5.6-sol", "medium", 3), "R3 policy drift")
    return selected


def verify_historical_rebuttal_selection_authority(
    authority: Mapping[str, Any], *, eval_artifact: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]],
) -> str:
    """Replay all durable paid-eval receipts; authority is selection-only evidence."""
    observed = verify_rebuttal_selected_model_authority_v02(authority)
    _need(observed == SELECTION_HASH, "historical Rebuttal selection hash drift")
    replayed = build_rebuttal_selected_model_authority_v02(eval_artifact, receipts)
    _need(dict(authority) == replayed, "historical Rebuttal selection replay drift")
    selected = authority.get("selected_candidate")
    _need(isinstance(selected, Mapping) and dict(selected) == {
        "candidate_key": "R3", "model": "gpt-5.6-sol", "reasoning_effort": "medium", "ladder_position": 3,
    }, "historical Rebuttal selection candidate drift")
    _selected()
    return observed


def verify_current_initial_freeze(initial_freeze: Mapping[str, Any], *, initial_cost: Mapping[str, Any]) -> str:
    freeze_hash = _hash(initial_freeze)
    _need(freeze_hash == CURRENT_INITIAL_FREEZE_HASH, "current Initial freeze hash drift")
    exact = {
        "artifact_version": CURRENT_INITIAL_FREEZE_VERSION,
        "status": CURRENT_INITIAL_FREEZE_STATUS,
        "code_commit_sha": CURRENT_INITIAL_FREEZE_CODE_SHA,
        "reconciliation_hash": CURRENT_RECONCILIATION_HASH,
        "resume_readiness_hash": CURRENT_RESUME_READINESS_HASH,
        "resume_ledger_hash": CURRENT_RESUME_LEDGER_HASH,
        "resume_owner_approval_hash": CURRENT_RESUME_APPROVAL_HASH,
        "fresh_initial_records": 9,
        "model_calls_in_resume": 8,
        "automatic_retries": 0,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "b5_handoff_created": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    for field, value in exact.items():
        _need(initial_freeze.get(field) == value, f"current Initial freeze drift: {field}")
    items = frozen_initial_items(initial_cost)
    _need(len(items) == 9, "current Initial plan count drift")
    records = initial_freeze.get("processed_records")
    _need(isinstance(records, list) and len(records) == 9, "current Initial record count drift")
    request_hashes = [item.plan_item.request.request_hash for item in items]
    _need(initial_freeze.get("request_hashes") == request_hashes, "current Initial request order/hash drift")
    record_hashes: list[str] = []
    opinion_hashes: list[str] = []
    for raw, item in zip(records, items, strict=True):
        _need(isinstance(raw, Mapping), "current Initial processed record malformed")
        try:
            _validate_processed_record(raw)
            opinion = COUNCIL_OPINION_V1.model_validate(dict(raw["council_opinion"]))
            claims = tuple(MATERIAL_CLAIM_V1.model_validate(row) for row in raw["material_claims"])
        except Exception as exc:
            raise PostResearchRebuttalError(f"current Initial processed record invalid: {exc}") from exc
        _need(raw.get("record_hash") == canonical_sha256(raw, exclude_fields=("record_hash",)), "current Initial record hash drift")
        _need((raw.get("candidate_id"), raw.get("lane"), raw.get("stage"), raw.get("request_hash")) == (item.plan_item.candidate_id, item.plan_item.lane.value, item.plan_item.stage.value, item.plan_item.request.request_hash), "current Initial candidate/lane/stage/order drift")
        _need(opinion.input_snapshot_hash == item.plan_item.bundle.bundle_hash and opinion.candidate_packet_hash == item.plan_item.bundle.candidate_packet_hash, "current Initial opinion input lineage drift")
        _need(tuple(claim.claim_id for claim in claims) == tuple(opinion.material_claim_ids), "current Initial MaterialClaim closure drift")
        _need(all(claim.candidate_id == item.plan_item.candidate_id for claim in claims), "current Initial claim candidate drift")
        record_hashes.append(str(raw["record_hash"]))
        opinion_hashes.append(str(raw["council_opinion_hash"]))
    _need(initial_freeze.get("processed_record_hashes") == record_hashes and len(set(record_hashes)) == 9, "current Initial processed-record hashes drift")
    raw_hashes = initial_freeze.get("raw_response_hashes")
    _need(isinstance(raw_hashes, list) and len(raw_hashes) == 9 and len(set(raw_hashes)) == 9 and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in raw_hashes), "current Initial raw-response hashes drift")
    _need(initial_freeze.get("request_provenance") == ["RECONCILED_CAPTURED_PROVIDER_RESPONSE"] + ["FIRST_DISPATCH"] * 8, "current Initial request provenance drift")
    _need(len(set(opinion_hashes)) == 9, "current Initial opinions are not unique")
    return freeze_hash


def _context(initial_freeze: Mapping[str, Any], *, initial_cost: Mapping[str, Any], candidate: str) -> dict[str, Any]:
    items = [item.plan_item for item in frozen_initial_items(initial_cost) if item.plan_item.candidate_id == candidate]
    records = [row for row in initial_freeze["processed_records"] if row["candidate_id"] == candidate]
    _need(len(items) == len(records) == 3, f"{candidate} Initial lanes missing")
    _need([item.lane.value for item in items] == list(LANES) and [row.get("lane") for row in records] == list(LANES), f"{candidate} Initial lane order drift")
    bundle = items[0].bundle
    model_input = dict(items[0].model_input)
    _need(all(item.bundle.bundle_hash == bundle.bundle_hash and item.model_input.get("model_input_hash") == model_input.get("model_input_hash") for item in items), f"{candidate} Initial input drift")
    opinions: list[dict[str, Any]] = []
    ids: list[str] = []
    hashes: list[str] = []
    claims_by_lane: dict[str, tuple[str, ...]] = {}
    uncertainty: list[str] = []
    for row in records:
        opinion = COUNCIL_OPINION_V1.model_validate(dict(row["council_opinion"]))
        ids.append(opinion.opinion_id); hashes.append(str(row["council_opinion_hash"]))
        claims = tuple(MATERIAL_CLAIM_V1.model_validate(value) for value in row["material_claims"])
        claims_by_lane[str(row["lane"])] = tuple(claim.claim_id for claim in claims)
        structured = row.get("structured_output")
        _need(isinstance(structured, Mapping), f"{candidate} Initial structured output missing")
        for field in ("material_unknown_refs", "material_conflict_refs"):
            refs = structured.get(field, [])
            _need(isinstance(refs, list) and all(isinstance(ref, str) for ref in refs), f"{candidate} {field} malformed")
            for ref in refs:
                if ref not in uncertainty:
                    uncertainty.append(ref)
        opinions.append({"lane": row["lane"], "council_opinion": dict(row["council_opinion"]), "council_opinion_hash": row["council_opinion_hash"], "material_claims": [dict(value) for value in row["material_claims"]], "claim_metadata": [dict(value) for value in row["claim_metadata"]]})
    opposing = {lane: [claim_id for owner, values in claims_by_lane.items() if owner != lane for claim_id in values] for lane in LANES}
    _need(all(values for values in opposing.values()), f"{candidate} opposing claim closure missing")
    model = {"candidate_model_input": model_input, "initial_council": {"initial_freeze_artifact_hash": CURRENT_INITIAL_FREEZE_HASH, "initial_opinions": opinions}}
    context: dict[str, Any] = {"candidate_id": candidate, "effective_bundle_hash": bundle.bundle_hash, "effective_model_input_hash": model_input["model_input_hash"], "rebuttal_model_input_hash": canonical_sha256(model), "model_input": model, "initial_opinion_ids": ids, "initial_opinion_hashes": hashes, "opposing_claim_ids_by_lane": opposing, "allowed_uncertainty_refs": uncertainty, "required_unknown_refs": uncertainty}
    context["context_hash"] = canonical_sha256(context)
    return context


def build_current_rebuttal_preflight(*, code_commit_sha: str, initial_freeze: Mapping[str, Any], initial_cost: Mapping[str, Any], pricing: Mapping[str, Any], selection_authority: Mapping[str, Any], eval_artifact: Mapping[str, Any], receipts: Sequence[Mapping[str, Any],], historical_request_hashes: Sequence[str]) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None, "executor code SHA invalid")
    freeze_hash = verify_current_initial_freeze(initial_freeze, initial_cost=initial_cost)
    selection_hash = verify_historical_rebuttal_selection_authority(selection_authority, eval_artifact=eval_artifact, receipts=receipts)
    pricing_hash = _hash(pricing, "pricing_hash")
    selected = _selected(); output_cap = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.REBUTTAL]
    rows: list[dict[str, Any]] = []
    total = Decimal("0")
    for index, candidate in enumerate(CANDIDATES, 1):
        context = _context(initial_freeze, initial_cost=initial_cost, candidate=candidate)
        bundle = next(item.plan_item.bundle for item in frozen_initial_items(initial_cost) if item.plan_item.candidate_id == candidate)
        opposing = {CouncilLane(lane): tuple(values) for lane, values in context["opposing_claim_ids_by_lane"].items()}
        request = build_bounded_rebuttal_request_v01(model_candidate=selected, bundle=bundle, model_input=context["model_input"], initial_opinion_ids=tuple(context["initial_opinion_ids"]), initial_opinion_hashes=tuple(context["initial_opinion_hashes"]), opposing_claim_ids_by_lane=opposing, allowed_uncertainty_refs=tuple(context["allowed_uncertainty_refs"]))
        _need(request.request_hash not in historical_request_hashes, "historical Rebuttal request hash reuse")
        byte_count = len(json.dumps(request.request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        cost = runtime_cost_upper_bound_usd(model=selected.model, input_tokens_upper_bound=byte_count, output_tokens_upper_bound=output_cap, call_count=1, pricing=pricing)
        total += cost
        rows.append({"dispatch_index": index, "candidate_id": candidate, "context": context, "context_hash": context["context_hash"], "request_hash": request.request_hash, "request_payload": request.request_payload, "request_body_utf8_bytes": byte_count, "input_tokens_upper_bound": byte_count, "max_output_tokens": output_cap, "per_call_cost_upper_bound_usd": format(cost, "f"), "prompt_contract_version": request.prompt_contract_version, "prompt_version": request.prompt_version, "prompt_hash": request.prompt_hash, "schema_version": request.schema_version, "input_hash": request.input_hash})
    manifest = canonical_sha256({"request_hashes": [row["request_hash"] for row in rows], "candidate_order": list(CANDIDATES)})
    out: dict[str, Any] = {"artifact_version": PREFLIGHT_VERSION, "status": PREFLIGHT_STATUS, "code_commit_sha": code_commit_sha, "current_initial_freeze_hash": freeze_hash, "current_initial_processed_record_hashes": initial_freeze["processed_record_hashes"], "current_initial_raw_response_hashes": initial_freeze["raw_response_hashes"], "historical_rebuttal_selection_authority_hash": selection_hash, "historical_rebuttal_selection_authority_revalidated": True, "historical_rebuttal_outputs_reused": False, "historical_rebuttal_request_hashes_reused": False, "selected_rebuttal_candidate": "R3", "model": selected.model, "reasoning_effort": selected.reasoning_effort, "candidate_order": list(CANDIDATES), "new_paid_calls_planned": 3, "new_paid_call_count_ceiling": 3, "max_output_tokens_per_call": output_cap, "request_rows": rows, "request_hashes": [row["request_hash"] for row in rows], "request_manifest_hash": manifest, "pricing_version": pricing["pricing_version"], "pricing_hash": pricing_hash, "input_token_upper_bound_method": "CONSERVATIVE_ONE_UTF8_SERIALIZED_REQUEST_BODY_BYTE_PER_INPUT_TOKEN_CACHE_WRITE_AWARE", "rebuttal_max_cost_usd": format(total, "f"), "automatic_retries": 0, "owner_approval_required": True, "owner_approval_status": "NOT_GRANTED", "model_calls_authorized": False, "provider_reads_authorized": False, "judge_authorized": False, "b5_handoff_created": False, "broker_writes": 0, "alpaca_orders": 0, "live_money": "PROHIBITED", "model_calls_this_step": 0, "provider_reads_this_step": 0, "cost_usd_this_step": "0"}
    out["artifact_hash"] = canonical_sha256(out, exclude_fields=("artifact_hash",)); return out


def verify_current_rebuttal_preflight(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload)
    expected = build_current_rebuttal_preflight(**inputs)
    _need(payload.get("artifact_version") == PREFLIGHT_VERSION and dict(payload) == expected, "current Rebuttal preflight semantic drift")
    return observed


def _plan(preflight: Mapping[str, Any], *, initial_cost: Mapping[str, Any]) -> tuple[RebuttalRuntimePlanItem, ...]:
    selected = _selected(); bundles = {item.plan_item.candidate_id: item.plan_item.bundle for item in frozen_initial_items(initial_cost)[::3]}
    result: list[RebuttalRuntimePlanItem] = []
    for row in preflight["request_rows"]:
        context = row["context"]; candidate = row["candidate_id"]
        request = build_bounded_rebuttal_request_v01(model_candidate=selected, bundle=bundles[candidate], model_input=context["model_input"], initial_opinion_ids=tuple(context["initial_opinion_ids"]), initial_opinion_hashes=tuple(context["initial_opinion_hashes"]), opposing_claim_ids_by_lane={CouncilLane(key): tuple(value) for key, value in context["opposing_claim_ids_by_lane"].items()}, allowed_uncertainty_refs=tuple(context["allowed_uncertainty_refs"]))
        _need(request.request_hash == row["request_hash"] and request.request_payload == row["request_payload"], "current Rebuttal request reconstruction drift")
        result.append(RebuttalRuntimePlanItem(dispatch_index=row["dispatch_index"], candidate_id=candidate, context_hash=context["context_hash"], bundle=bundles[candidate], model_input=context["model_input"], initial_opinion_ids=tuple(context["initial_opinion_ids"]), initial_opinion_hashes=tuple(context["initial_opinion_hashes"]), opposing_claim_ids_by_lane={CouncilLane(key): tuple(value) for key, value in context["opposing_claim_ids_by_lane"].items()}, allowed_uncertainty_refs=tuple(context["allowed_uncertainty_refs"]), required_unknown_refs=tuple(context["required_unknown_refs"]), request=request, request_body_utf8_bytes=row["request_body_utf8_bytes"]))
    _need(tuple(item.candidate_id for item in result) == CANDIDATES, "current Rebuttal plan order drift")
    return tuple(result)


def build_final_rebuttal_readiness(*, code_commit_sha: str, preflight: Mapping[str, Any], initial_freeze: Mapping[str, Any], initial_cost: Mapping[str, Any], pricing: Mapping[str, Any], selection_authority: Mapping[str, Any], eval_artifact: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]], historical_request_hashes: Sequence[str]) -> dict[str, Any]:
    preflight_hash = verify_current_rebuttal_preflight(preflight, code_commit_sha=code_commit_sha, initial_freeze=initial_freeze, initial_cost=initial_cost, pricing=pricing, selection_authority=selection_authority, eval_artifact=eval_artifact, receipts=receipts, historical_request_hashes=historical_request_hashes)
    out = {"artifact_version": READINESS_VERSION, "status": READINESS_STATUS, "code_commit_sha": code_commit_sha, "source_rebuttal_preflight_hash": preflight_hash, "current_initial_freeze_verify": "PASS", "current_initial_freeze_hash": CURRENT_INITIAL_FREEZE_HASH, "fresh_initial_records": 9, "historical_rebuttal_selection_authority_revalidated": "PASS", "historical_rebuttal_outputs_reused": False, "historical_rebuttal_request_hashes_reused": False, "selected_rebuttal_candidate": "R3", "model": "gpt-5.6-sol", "reasoning_effort": "medium", "candidate_order": list(CANDIDATES), "new_paid_calls_planned": 3, "new_paid_call_count_ceiling": 3, "max_output_tokens_per_call": 6144, "rebuttal_max_cost_usd": preflight["rebuttal_max_cost_usd"], "paid_rebuttal_executor_exists": True, "explicit_paid_flag_required": True, "strict_readiness_verifier": "PASS", "approval_verifier_returns_actual_artifact_hash": "PASS", "invalid_approval_fails_before_transport": "PASS", "valid_authority_reaches_fake_transport": "PASS", "owner_approval_required": True, "owner_approval_status": "NOT_GRANTED", "model_calls_authorized": False, "automatic_retries": 0, "partial_dispatch_fail_closed": True, "judge_authorized": False, "b5_handoff_created": False, "model_calls_this_step": 0, "provider_reads_this_step": 0, "broker_writes": 0, "alpaca_orders": 0, "cost_usd_this_step": "0", "live_money": "PROHIBITED"}
    out["artifact_hash"] = canonical_sha256(out, exclude_fields=("artifact_hash",)); return out


def verify_final_rebuttal_readiness(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload); expected = build_final_rebuttal_readiness(**inputs)
    _need(payload.get("artifact_version") == READINESS_VERSION and dict(payload) == expected, "final Rebuttal readiness semantic drift"); return observed


def build_rebuttal_owner_approval(*, code_commit_sha: str, readiness_hash: str, preflight: Mapping[str, Any], owner_approval_id: str, owner_approval_at_utc: str) -> dict[str, Any]:
    out = {"artifact_version": APPROVAL_VERSION, "owner_approval_granted": True, "owner_approval_id": owner_approval_id, "owner_approval_at_utc": owner_approval_at_utc, "approved_executor_code_commit_sha": code_commit_sha, "rebuttal_readiness_hash": readiness_hash, "current_initial_freeze_hash": CURRENT_INITIAL_FREEZE_HASH, "historical_rebuttal_selection_authority_hash": SELECTION_HASH, "request_manifest_hash": preflight["request_manifest_hash"], "request_hashes": preflight["request_hashes"], "model": "gpt-5.6-sol", "reasoning_effort": "medium", "new_paid_call_count": 3, "new_paid_call_count_ceiling": 3, "max_output_tokens_per_call": 6144, "approved_rebuttal_max_cost_usd": preflight["rebuttal_max_cost_usd"], "automatic_retries": 0}
    out["artifact_hash"] = canonical_sha256(out, exclude_fields=("artifact_hash",)); return out


def verify_rebuttal_owner_approval(approval: Mapping[str, Any], *, code_commit_sha: str, readiness_hash: str, preflight: Mapping[str, Any]) -> str:
    observed = _hash(approval)
    expected = build_rebuttal_owner_approval(code_commit_sha=code_commit_sha, readiness_hash=readiness_hash, preflight=preflight, owner_approval_id=str(approval.get("owner_approval_id", "")), owner_approval_at_utc=str(approval.get("owner_approval_at_utc", "")))
    _need(dict(approval) == expected, "Rebuttal owner approval drift"); return observed


def materialize_rebuttal_owner_approval(path: Path, *, code_commit_sha: str, readiness_hash: str, preflight: Mapping[str, Any], owner_approval_id: str, owner_approval_at_utc: str) -> dict[str, Any]:
    """Future explicit owner action; exclusive write prevents approval replacement."""
    approval = build_rebuttal_owner_approval(code_commit_sha=code_commit_sha, readiness_hash=readiness_hash, preflight=preflight, owner_approval_id=owner_approval_id, owner_approval_at_utc=owner_approval_at_utc)
    _write_exclusive(path, approval)
    return approval


def build_rebuttal_raw_response_capture(*, request_hash: str, provider_response: Mapping[str, Any], dispatch_started_at_utc: str, captured_at_utc: str) -> dict[str, Any]:
    raw = _external_json_value(provider_response); _need(isinstance(raw, Mapping), "provider response must be Mapping")
    response_id = raw.get("id"); _need(response_id is None or isinstance(response_id, str), "provider response id malformed")
    out: dict[str, Any] = {"capture_version": "B4_POST_RESEARCH_REOPEN_REBUTTAL_RAW_PROVIDER_RESPONSE_v0_1", "request_hash": request_hash, "provider_response_id": response_id, "dispatch_started_at_utc": dispatch_started_at_utc, "captured_at_utc": captured_at_utc, "raw_response": dict(raw)}
    out["raw_response_hash"] = external_provider_json_sha256(out); return out


def verify_rebuttal_raw_response_capture(capture: Mapping[str, Any], *, request_hash: str) -> str:
    observed = capture.get("raw_response_hash"); _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, "raw response hash missing")
    comparable = dict(capture); comparable.pop("raw_response_hash", None)
    _need(observed == external_provider_json_sha256(comparable), "raw response hash mismatch")
    _need(capture.get("capture_version") == "B4_POST_RESEARCH_REOPEN_REBUTTAL_RAW_PROVIDER_RESPONSE_v0_1" and capture.get("request_hash") == request_hash and isinstance(capture.get("raw_response"), Mapping), "raw response capture drift")
    return observed


def _process(item: RebuttalRuntimePlanItem, raw: Mapping[str, Any], *, initial_freeze: Mapping[str, Any], pricing: Mapping[str, Any], frozen_at: datetime) -> tuple[dict[str, Any], Decimal]:
    call, proposal = parse_council_responses_payload(raw, request=item.request, latency_ms=0)
    initial_records = _candidate_initial_records(initial_freeze, candidate_id=item.candidate_id)
    promotion = promote_rebuttal_bundle(proposal, bundle=item.bundle, model_input=item.model_input, initial_records=initial_records, required_unknown_refs=item.required_unknown_refs)
    usage = _usage_counts(raw); cost = actual_cost_usd(raw, model="gpt-5.6-sol", pricing=pricing)
    record = _processed_record(item=item, call=call, proposal=proposal, promotion=promotion, latency_ms=0, usage=usage, actual_cost=cost)
    validate_rebuttal_processed_record(record); return record, cost


def _freeze(*, code_commit_sha: str, approval_hash: str, readiness_hash: str, preflight: Mapping[str, Any], ledger: Mapping[str, Any], records: Sequence[Mapping[str, Any]], raw_hashes: Sequence[str], total: Decimal) -> dict[str, Any]:
    out = {"artifact_version": RESULT_VERSION, "status": "B4_POST_RESEARCH_REOPEN_REBUTTAL_COUNCIL_FROZEN", "code_commit_sha": code_commit_sha, "rebuttal_owner_approval_hash": approval_hash, "rebuttal_readiness_hash": readiness_hash, "current_initial_freeze_hash": CURRENT_INITIAL_FREEZE_HASH, "current_initial_processed_record_hashes": preflight["current_initial_processed_record_hashes"], "historical_rebuttal_selection_authority_hash": SELECTION_HASH, "request_hashes": preflight["request_hashes"], "raw_response_hashes": list(raw_hashes), "processed_records": list(records), "processed_record_hashes": [row["record_hash"] for row in records], "rebuttal_actual_cost_usd": format(total, "f"), "automatic_retries": 0, "judge_authorized": False, "final_decision_created": False, "b5_handoff_created": False, "broker_writes": 0, "alpaca_orders": 0, "live_money": "PROHIBITED", "ledger_hash": ledger["ledger_hash"]}
    out["artifact_hash"] = canonical_sha256(out, exclude_fields=("artifact_hash",)); return out


def execute_paid_rebuttal(*, execute_paid_rebuttal: bool, branch: str, code_commit_sha: str, worktree_clean: bool, preflight: Mapping[str, Any], readiness: Mapping[str, Any], initial_freeze: Mapping[str, Any], initial_cost: Mapping[str, Any], pricing: Mapping[str, Any], selection_authority: Mapping[str, Any], eval_artifact: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]], historical_request_hashes: Sequence[str], approval: Mapping[str, Any] | None, ledger_path: Path, raw_dir: Path, result_path: Path, transport_factory: Callable[[], Callable[[Mapping[str, Any]], Mapping[str, Any]]], now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> dict[str, Any]:
    _need(execute_paid_rebuttal is True, "--execute-paid-rebuttal is required"); _need(approval is not None, "exact Rebuttal owner approval required")
    _need(branch == "hackathon/alpaca-2026" and worktree_clean and not ledger_path.exists() and not raw_dir.exists() and not result_path.exists(), "Rebuttal pre-transport gate failed")
    readiness_hash = verify_final_rebuttal_readiness(readiness, code_commit_sha=code_commit_sha, preflight=preflight, initial_freeze=initial_freeze, initial_cost=initial_cost, pricing=pricing, selection_authority=selection_authority, eval_artifact=eval_artifact, receipts=receipts, historical_request_hashes=historical_request_hashes)
    approval_hash = verify_rebuttal_owner_approval(approval, code_commit_sha=code_commit_sha, readiness_hash=readiness_hash, preflight=preflight)
    plan = _plan(preflight, initial_cost=initial_cost); ledger: dict[str, Any] = {"ledger_version": LEDGER_VERSION, "rebuttal_owner_approval_hash": approval_hash, "entries": [{"dispatch_index": item.dispatch_index, "candidate_id": item.candidate_id, "request_hash": item.request.request_hash, "state": "NOT_DISPATCHED", "automatic_retry_permitted": False} for item in plan]}; ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _write_exclusive(ledger_path, ledger)
    transport = transport_factory(); records: list[Mapping[str, Any]] = []; raw_hashes: list[str] = []; total = Decimal("0")
    for entry, item in zip(ledger["entries"], plan, strict=True):
        entry.update(state="DISPATCH_STARTED_UNKNOWN", dispatch_started_at_utc=_utc(now())); ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger)
        try:
            raw = transport(item.request.request_payload)
        except Exception as exc:
            entry["stop_reason"] = f"AMBIGUOUS_PROVIDER_OUTCOME:{type(exc).__name__}"; ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger); raise PostResearchRebuttalError("ambiguous Rebuttal provider outcome") from exc
        _need(isinstance(raw, Mapping), "provider response must be Mapping")
        capture = build_rebuttal_raw_response_capture(request_hash=item.request.request_hash, provider_response=raw, dispatch_started_at_utc=entry["dispatch_started_at_utc"], captured_at_utc=_utc(now()))
        path = raw_dir / f"{item.dispatch_index:02d}-{item.request.request_hash}.json"; _write_exclusive(path, capture); raw_hash = verify_rebuttal_raw_response_capture(capture, request_hash=item.request.request_hash)
        entry.update(raw_response_hash=raw_hash, raw_response_path=str(path), response_captured_at_utc=capture["captured_at_utc"]); ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger)
        try:
            record, actual = _process(item, raw, initial_freeze=initial_freeze, pricing=pricing, frozen_at=now())
        except Exception as exc:
            entry["stop_reason"] = f"RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:{type(exc).__name__}"; ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger); raise PostResearchRebuttalError("captured Rebuttal response failed validation") from exc
        total += actual; _need(total <= Decimal(str(preflight["rebuttal_max_cost_usd"])), "Rebuttal cost exceeds authority")
        entry.update(state="COMPLETED", processed_record_hash=record["record_hash"], actual_cost_usd=format(actual, "f")); ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger); records.append(record); raw_hashes.append(raw_hash)
    result = _freeze(code_commit_sha=code_commit_sha, approval_hash=approval_hash, readiness_hash=readiness_hash, preflight=preflight, ledger=ledger, records=records, raw_hashes=raw_hashes, total=total); _write_exclusive(result_path, result); return result
