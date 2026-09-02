#!/usr/bin/env python3
"""Build a zero-call, inactive TTL fresh-Judge activation/cost readiness record.

This script intentionally creates neither a decision nor any authority to call a
model or provider.  It binds an inactive proposal to immutable expired-decision
lineage and constructs only the bounded request that a separately authorized
future activation would have to reproduce.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType
from typing import Any, Mapping, Sequence, TextIO

from aic.council import post_research_reopen_judge_current_v03 as v03
from aic.council import post_research_reopen_judge_current_v04 as v04
from aic.council.bounded_request import assert_bounded_request_invariants, build_bounded_judge_request
from aic.council.initial_runtime_cost_v02 import (
    load_initial_runtime_pricing,
    runtime_cost_upper_bound_usd,
)
from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B4_TTL_JUDGE_ACTIVATION_READINESS_ZERO_CALL_v0_1"
STATUS = "PASS_ZERO_CALL_TTL_FRESH_JUDGE_ACTIVATION_READINESS"
PROPOSAL_POLICY_PATH = Path("config/event/decision_ttl_reevaluation_policy_competition_v1.json")
PRICING_PATH = Path("config/event/openai_text_pricing_2026_08_30.json")
TTL_RECEIPT_PATH = Path(".aic-runtime/b4_recovered_decision_ttl_lineage_v0_1__fc4d73a__5500332.json")
TTL_PREFLIGHT_PATH = Path(".aic-runtime/b4_ttl_expiry_review_preflight_zero_call_v0_1__fc4d73a__7d8e0d4.json")
MODEL_RUN_REF = "B4_TTL_REEVALUATION_JUDGE_J1_V01"
HISTORICAL_MODEL_RUN_REF = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_J1_V04"
HISTORICAL_REQUEST_HASH = "2312558ae6e3979d6f8816b6b1c64309750e4e420890c4f6447f755ce4423c53"
HISTORICAL_RAW_RESPONSE_HASH = "fc4d73a86a178c03e1acbda64f176df4bd4fe225227832fcd5b286fa2c77e37d"
HISTORICAL_PROVIDER_RESPONSE_ID = "resp_071e5625bc07e951016a96927b756087d28fc9b9eba67c2780"
RECOVERED_B4_ARTIFACT_HASH = "f9a9e08a30b58ebf6fcb358c2b35a82717682ddef3ac5fd58c912d518d3fadf0"
PROPOSAL_POLICY_HASH = "0b9128d8b19505daa19ef556b50ae7c1435ad02d04db34cff7730e8235eb3c7a"
DEFAULT_EVALUATION_TIME_UTC = "2026-09-01T19:45:35Z"
FROZEN_JUDGE_SOURCE_COMMIT_SHA = "814895777015cfbf47a1be03c028b65030cab2df"
EXPECTED_PRICING_HASH = "13b67bf92f56b2962694f463850e0a0e289fc08f0c4a3d3cafe8eb928d0ee336"
EXPECTED_PRICING_VERSION = "OPENAI_TEXT_PRICING_2026_08_30_CACHE_WRITE_AWARE"
FROZEN_JUDGE_SOURCE_FILE_SHA256 = {
    "src/aic/council/post_research_reopen_judge_current_v03.py": "d44d51fafdf678b3ec88d82f6a54663f9ce736c6d889a57a4c14a45287abeee3",
    "src/aic/council/post_research_reopen_judge_current_v04.py": "e6f9a4212870138ca4091002c0e218f41e8a68df24e67c71e0a5bf7ec45ef10d",
    "src/aic/council/bounded_request.py": "244928d3b5c1647ad097fff5984e9c39775708c10b1d1bc5c223307b862a0e6c",
    "src/aic/council/request.py": "7b715314f402869419fa9919f7e51758e524de3e1b7f133a109828c5f6e3402e",
    "src/aic/council/model_policy.py": "3f6d7ac2973946e42488a7ad2c3360b5a732424149fcc3d153787e9846abb8e2",
    "src/aic/council/proposal.py": "291fcf686ddf34fc0d49f17446045622538867f51d9e3dd1b1f649e2870bebcd",
    "src/aic/council/initial_runtime_cost_v02.py": "d0e537e75801d5ac4805586c7c3aa583fc41d1ddfdb8403729004d871e037ca2",
    "src/aic/domain/canonical.py": "468db71ab49ff37925d077d84d40a16751ccd364059e42bef53e4e07cbeed3b6",
}


class ReadinessBlocked(ValueError):
    """A required local proof or no-authority invariant did not hold."""


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise ReadinessBlocked(reason)


def _mapping(value: object, reason: str) -> Mapping[str, Any]:
    _need(isinstance(value, Mapping), reason)
    return value


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReadinessBlocked("BLOCK_EVALUATION_TIME_UTC") from exc
    _need(parsed.tzinfo is not None and parsed.utcoffset() is not None, "BLOCK_EVALUATION_TIME_UTC")
    return parsed.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_json(path: Path, reason: str) -> Mapping[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), reason)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadinessBlocked(reason) from exc


def _load_jsonl(path: Path, reason: str) -> list[Mapping[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadinessBlocked(reason) from exc
    return [_mapping(row, reason) for row in rows]


def _load_script(repository: Path, name: str, filename: str) -> ModuleType:
    path = repository / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    _need(spec is not None and spec.loader is not None, f"BLOCK_{name.upper()}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def current_head(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    _need(completed.returncode == 0, "BLOCK_READINESS_REPOSITORY_HEAD")
    head = completed.stdout.strip()
    _need(re.fullmatch(r"[0-9a-f]{40}", head) is not None, "BLOCK_READINESS_REPOSITORY_HEAD")
    return head


def verify_frozen_judge_source_files(repository: Path) -> dict[str, str]:
    """Fail closed if the request semantics differ from the frozen base snapshot."""
    observed: dict[str, str] = {}
    for relative_path, expected in FROZEN_JUDGE_SOURCE_FILE_SHA256.items():
        try:
            digest = hashlib.sha256((repository / relative_path).read_bytes()).hexdigest()
        except OSError as exc:
            raise ReadinessBlocked("BLOCK_FROZEN_JUDGE_SOURCE_DRIFT") from exc
        _need(digest == expected, "BLOCK_FROZEN_JUDGE_SOURCE_DRIFT")
        observed[relative_path] = digest
    return observed


def verify_inactive_proposal(repository: Path) -> str:
    module = _load_script(
        repository,
        "b4_ttl_reevaluation_policy_for_activation_readiness",
        "b4_ttl_reevaluation_policy_proposal_zero_call_v01.py",
    )
    try:
        policy_hash = module.verify_policy_at(repository)
    except Exception as exc:
        raise ReadinessBlocked("BLOCK_INACTIVE_PROPOSAL") from exc
    _need(policy_hash == PROPOSAL_POLICY_HASH, "BLOCK_PROPOSAL_POLICY_HASH")
    return policy_hash


def verify_expired_ttl_lineage(
    repository: Path, *, evaluation_time_utc: datetime
) -> tuple[Any, Mapping[str, Any], str]:
    module = _load_script(
        repository,
        "b4_ttl_lineage_for_activation_readiness",
        "b4_recovered_decision_ttl_lineage_zero_call_v01.py",
    )
    try:
        lineage = module.recover_lineage(
            raw=module.load_json(repository / module.RAW_CAPTURE_RELATIVE_PATH, "RAW_CAPTURE"),
            recovered=module.load_json(repository / module.RECOVERED_RELATIVE_PATH, "RECOVERED_B4"),
            policy=module.load_json(repository / module.POLICY_RELATIVE_PATH, "LIFECYCLE_POLICY"),
        )
        _, ttl_status, expires = module.evaluate_ttl(lineage, evaluation_time_utc)
    except Exception as exc:
        raise ReadinessBlocked("BLOCK_TTL_LINEAGE") from exc
    _need(lineage.raw_response_hash == HISTORICAL_RAW_RESPONSE_HASH, "BLOCK_TTL_RAW_HASH")
    _need(lineage.provider_response_id == HISTORICAL_PROVIDER_RESPONSE_ID, "BLOCK_TTL_PROVIDER_RESPONSE_ID")
    _need(lineage.recovered_artifact_hash == RECOVERED_B4_ARTIFACT_HASH, "BLOCK_TTL_RECOVERED_HASH")
    _need(_utc_text(lineage.decision_created_at_utc) == "2026-09-01T08:53:32Z", "BLOCK_TTL_CREATED_AT")
    _need(_utc_text(expires) == "2026-09-01T10:53:32Z", "BLOCK_TTL_EXPIRES_AT")
    _need(ttl_status == "TTL_EXPIRED", "BLOCK_TTL_NOT_EXPIRED")

    receipt = _load_json(repository / TTL_RECEIPT_PATH, "BLOCK_TTL_RECEIPT")
    _need(
        receipt.get("artifact_hash") == canonical_sha256(receipt, exclude_fields=("artifact_hash",)),
        "BLOCK_TTL_RECEIPT_SELF_HASH",
    )
    _need(receipt.get("source_raw_response_sha256") == lineage.raw_response_hash, "BLOCK_TTL_RECEIPT_RAW_BINDING")
    _need(receipt.get("recovered_b4_artifact_hash") == lineage.recovered_artifact_hash, "BLOCK_TTL_RECEIPT_RECOVERED_BINDING")
    _need(receipt.get("b4_outcome") == "INVEST", "BLOCK_TTL_OUTCOME")
    _need(receipt.get("primary_candidate_id") == "NVDA", "BLOCK_TTL_PRIMARY")
    return lineage, receipt, _utc_text(expires)


def verify_canonical_ttl_preflight(repository: Path) -> str:
    payload = _load_json(repository / TTL_PREFLIGHT_PATH, "BLOCK_TTL_PREFLIGHT")
    _need(
        payload.get("artifact_hash") == canonical_sha256(payload, exclude_fields=("artifact_hash",)),
        "BLOCK_TTL_PREFLIGHT_SELF_HASH",
    )
    _need(payload.get("ttl_status") == "TTL_EXPIRED", "BLOCK_TTL_PREFLIGHT_STATUS")
    _need(payload.get("preflight_outcome") == "TTL_REVIEW_SCOPE_UNDERSPECIFIED", "BLOCK_TTL_PREFLIGHT_OUTCOME")
    _need(payload.get("model_stage_scope_required") == "UNDERSPECIFIED", "BLOCK_TTL_PREFLIGHT_SCOPE")
    _need(payload.get("provider_refresh_required_before_model") == "UNDERSPECIFIED", "BLOCK_TTL_PREFLIGHT_PROVIDER")
    return str(payload["artifact_hash"])


def _source_inputs(repository: Path) -> tuple[Mapping[str, Any], v03.JudgeContext, Mapping[str, Any], v03.JudgeContext, Mapping[str, Any]]:
    runtime = repository / ".aic-runtime"
    read = lambda name: _load_json(runtime / name, "BLOCK_SOURCE_INPUT")
    closure = read("b3_research_reopen_final_competition_closure_zero_call_v0_1.json")
    residual = read("b3_research_reopen_residual_external_read_plan_zero_call_v0_1.json")
    gaps = read("b3_reopen_remaining_gaps_closure_zero_call_v0_2.json")
    initial = read("b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json")
    initial_cost = read("b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
    rebuttal = read("b4_post_research_reopen_rebuttal_council_freeze_v0_1.json")
    selection = read("b4_judge_selected_model_authority_v0_1.json")
    evaluation = read("b4_judge_model_eval_v0_1.json")
    receipts = _load_jsonl(runtime / "b4_judge_model_eval_paid_receipts_v0_1.jsonl", "BLOCK_SELECTION_RECEIPTS")

    try:
        selection_hash = v03.verify_selection(selection, eval_artifact=evaluation, receipts=receipts)
        source_entry = v03.build_entry(
            code_commit_sha=FROZEN_JUDGE_SOURCE_COMMIT_SHA,
            closure=closure,
            residual_plan=residual,
            remaining_gaps_closure=gaps,
            initial_freeze=initial,
            initial_cost=initial_cost,
            rebuttal_freeze=rebuttal,
        )
        source_context = v03.build_context(
            entry=source_entry,
            closure=closure,
            residual_plan=residual,
            remaining_gaps_closure=gaps,
            initial_cost=initial_cost,
            initial_freeze=initial,
            rebuttal_freeze=rebuttal,
            selection=selection,
        )
        _need(
            source_context.model_input.get("source_lineage", {}).get("judge_selection_authority_hash") == selection_hash,
            "BLOCK_SELECTION_LINEAGE",
        )
        gate = v04.build_gate(source_entry=source_entry, source_context=source_context)
        entry = v04.build_entry(
            code_commit_sha=FROZEN_JUDGE_SOURCE_COMMIT_SHA,
            source_entry=source_entry,
            source_context=source_context,
            gate=gate,
        )
        context = v04.build_context(
            entry=entry,
            source_entry=source_entry,
            source_context=source_context,
            gate=gate,
        )
    except Exception as exc:
        raise ReadinessBlocked("BLOCK_FRESH_JUDGE_SOURCE_CONTEXT") from exc
    return source_entry, source_context, entry, context, gate


def build_prospective_judge_context(
    *,
    source_entry: Mapping[str, Any],
    source_context: v03.JudgeContext,
    entry: Mapping[str, Any],
    context: v03.JudgeContext,
    policy_hash: str,
    ttl_receipt_hash: str,
) -> v03.JudgeContext:
    """Rebind canonical B3/Initial/Rebuttal input without importing Judge output."""
    v03.verify_context(source_context)
    v04.verify_context(context, entry=entry, source_entry=source_entry, source_context=source_context, gate=v04.build_gate(source_entry=source_entry, source_context=source_context))
    base = deepcopy(dict(context.model_input))
    base.pop("judge_input_hash", None)
    base["context_version"] = "B4_TTL_REEVALUATION_JUDGE_CONTEXT_v0_1"
    source_lineage = dict(base["source_lineage"])
    source_lineage["ttl_reevaluation"] = {
        "proposal_policy_hash": policy_hash,
        "ttl_lineage_receipt_hash": ttl_receipt_hash,
        "source_b3_final_closure_hash": source_entry["b3_final_closure_hash"],
        "source_initial_freeze_hash": source_entry["current_initial_freeze_hash"],
        "source_rebuttal_freeze_hash": source_entry["current_rebuttal_freeze_hash"],
        "historical_judge_semantic_input_allowed": False,
        "historical_judge_reactivation_allowed": False,
    }
    base["source_lineage"] = source_lineage
    judge_input_hash = canonical_sha256(base)
    model_input = {**base, "judge_input_hash": judge_input_hash}
    encoded = json.dumps(model_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _need(HISTORICAL_PROVIDER_RESPONSE_ID not in encoded, "BLOCK_HISTORICAL_RESPONSE_IN_MODEL_INPUT")
    _need(HISTORICAL_RAW_RESPONSE_HASH not in encoded, "BLOCK_HISTORICAL_RAW_HASH_IN_MODEL_INPUT")
    _need(HISTORICAL_MODEL_RUN_REF not in encoded, "BLOCK_HISTORICAL_RUN_REF_IN_MODEL_INPUT")
    return v03.JudgeContext(
        model_input=model_input,
        judge_input_hash=judge_input_hash,
        context_hash=canonical_sha256(model_input),
        mandate_version=context.mandate_version,
        deep_comparison_id=context.deep_comparison_id,
        allowed_claim_ids=context.allowed_claim_ids,
        allowed_dispute_refs=context.allowed_dispute_refs,
        allowed_conflict_refs=context.allowed_conflict_refs,
        allowed_unknown_refs=context.allowed_unknown_refs,
        allowed_condition_refs=context.allowed_condition_refs,
    )


def build_prospective_request(*, entry: Mapping[str, Any], context: v03.JudgeContext) -> Any:
    request = build_bounded_judge_request(
        model_candidate=v03._selected(),
        model_input=context.model_input,
        candidate_ids=tuple(entry["candidate_order"]),
        mandate_version=context.mandate_version,
        deep_comparison_id=context.deep_comparison_id,
        judge_input_hash=context.judge_input_hash,
        council_policy_version=entry["council_policy_version"],
        judge_policy_version=entry["judge_policy_version"],
        model_policy_version=entry["model_policy_version"],
        model_run_ref=MODEL_RUN_REF,
        allowed_claim_ids=context.allowed_claim_ids,
        allowed_dispute_refs=context.allowed_dispute_refs,
        allowed_conflict_refs=context.allowed_conflict_refs,
        allowed_unknown_refs=context.allowed_unknown_refs,
        allowed_condition_refs=context.allowed_condition_refs,
    )
    assert_bounded_request_invariants(request)
    encoded = json.dumps(request.request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _need(request.request_payload.get("model") == "gpt-5.6-terra", "BLOCK_MODEL")
    _need(request.request_payload.get("reasoning") == {"effort": "medium"}, "BLOCK_REASONING")
    _need(request.request_payload.get("max_output_tokens") == 8192, "BLOCK_MAX_OUTPUT_TOKENS")
    _need(MODEL_RUN_REF in encoded and HISTORICAL_MODEL_RUN_REF not in encoded, "BLOCK_MODEL_RUN_REF")
    _need(HISTORICAL_PROVIDER_RESPONSE_ID not in encoded, "BLOCK_HISTORICAL_RESPONSE_IN_REQUEST")
    _need(HISTORICAL_RAW_RESPONSE_HASH not in encoded, "BLOCK_HISTORICAL_RAW_HASH_IN_REQUEST")
    _need(request.request_hash != HISTORICAL_REQUEST_HASH, "BLOCK_HISTORICAL_REQUEST_REUSE")
    return request


def build_readiness(
    *, repository: Path, evaluation_time_utc: datetime, readiness_repository_head: str
) -> dict[str, object]:
    _need(
        re.fullmatch(r"[0-9a-f]{40}", readiness_repository_head) is not None,
        "BLOCK_READINESS_REPOSITORY_HEAD",
    )
    policy_hash = verify_inactive_proposal(repository)
    lineage, receipt, expires = verify_expired_ttl_lineage(repository, evaluation_time_utc=evaluation_time_utc)
    ttl_preflight_hash = verify_canonical_ttl_preflight(repository)
    frozen_source_files = verify_frozen_judge_source_files(repository)
    source_entry, source_context, entry, context, gate = _source_inputs(repository)
    prospective_context = build_prospective_judge_context(
        source_entry=source_entry,
        source_context=source_context,
        entry=entry,
        context=context,
        policy_hash=policy_hash,
        ttl_receipt_hash=str(receipt["artifact_hash"]),
    )
    request = build_prospective_request(entry=entry, context=prospective_context)
    request_bytes = len(json.dumps(request.request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    try:
        pricing = load_initial_runtime_pricing(repository / PRICING_PATH)
        pricing_hash = str(pricing["pricing_hash"])
        _need(pricing_hash == EXPECTED_PRICING_HASH, "BLOCK_PRICING_HASH")
        _need(pricing.get("pricing_version") == EXPECTED_PRICING_VERSION, "BLOCK_PRICING_VERSION")
        long_context = _mapping(pricing.get("long_context"), "BLOCK_LONG_CONTEXT_PRICING")
        threshold = long_context.get("threshold_input_tokens_exclusive")
        _need(type(threshold) is int and threshold > 0, "BLOCK_LONG_CONTEXT_PRICING")
        long_context_multiplier_applied = request_bytes > threshold
        cost = runtime_cost_upper_bound_usd(
            model="gpt-5.6-terra",
            input_tokens_upper_bound=request_bytes,
            output_tokens_upper_bound=8192,
            call_count=1,
            pricing=pricing,
        )
    except Exception as exc:
        raise ReadinessBlocked("BLOCK_COST_PREFLIGHT") from exc

    artifact: dict[str, object] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": STATUS,
        "readiness_repository_head": readiness_repository_head,
        "canonical_base_head": FROZEN_JUDGE_SOURCE_COMMIT_SHA,
        "source_judge_code_commit_sha": FROZEN_JUDGE_SOURCE_COMMIT_SHA,
        "request_identity_independent_of_readiness_repository_head": True,
        "frozen_judge_source_verified": True,
        "frozen_judge_source_file_sha256": frozen_source_files,
        "ttl_status": "TTL_EXPIRED",
        "trigger": "TTL_EXPIRY",
        "historical_decision_reactivated": False,
        "historical_ttl_refreshed": False,
        "source_raw_b4_canonical_hash": lineage.raw_response_hash,
        "source_recovered_b4_artifact_hash": lineage.recovered_artifact_hash,
        "source_historical_provider_response_id": lineage.provider_response_id,
        "source_ttl_lineage_receipt_hash": receipt["artifact_hash"],
        "source_ttl_expiry_preflight_hash": ttl_preflight_hash,
        "decision_created_at_utc": _utc_text(lineage.decision_created_at_utc),
        "decision_expires_at_utc": expires,
        "proposal_policy_hash": policy_hash,
        "proposal_active": False,
        "proposal_status": "DRAFT_NOT_AUTHORITY",
        "prospective_model_run_ref": MODEL_RUN_REF,
        "prospective_judge_input_hash": prospective_context.judge_input_hash,
        "prospective_judge_context_hash": prospective_context.context_hash,
        "prospective_request_hash": request.request_hash,
        "historical_request_hash": HISTORICAL_REQUEST_HASH,
        "historical_request_hash_reused": False,
        "historical_judge_response_in_model_input": False,
        "historical_judge_raw_hash_in_model_input": False,
        "source_b3_final_closure_hash": source_entry["b3_final_closure_hash"],
        "source_initial_freeze_hash": source_entry["current_initial_freeze_hash"],
        "source_rebuttal_freeze_hash": source_entry["current_rebuttal_freeze_hash"],
        "source_v03_entry_hash": source_entry["artifact_hash"],
        "source_v04_entry_hash": entry["artifact_hash"],
        "source_v04_gate_hash": gate["artifact_hash"],
        "pricing_hash": pricing_hash,
        "pricing_version": EXPECTED_PRICING_VERSION,
        "input_token_upper_bound_method": "CONSERVATIVE_ONE_UTF8_BYTE_PER_INPUT_TOKEN_ALL_INPUT_CACHE_WRITE",
        "long_context_multiplier_applied": long_context_multiplier_applied,
        "request_body_utf8_bytes": request_bytes,
        "input_tokens_upper_bound": request_bytes,
        "max_output_tokens": 8192,
        "max_call_count": 1,
        "automatic_retries": 0,
        "judge_max_cost_usd": format(cost, "f"),
        "provider_refresh_required_before_model": False,
        "provider_reads_authorized": False,
        "activation_status": "NOT_GRANTED",
        "cost_approval_status": "NOT_GRANTED",
        "owner_activation_required": True,
        "owner_paid_approval_required": True,
        "model_calls_authorized": False,
        "broker_write_authority": False,
        "live_execution": False,
        "watch_b5_started": False,
        "abstain_b5_started": False,
        "fresh_invest_requires_fresh_b5": True,
        "historical_b5_selection_is_lineage_only": True,
        "model_calls": 0,
        "openai_calls": 0,
        "provider_reads": 0,
        "alpaca_reads": 0,
        "network_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "paid_llm_cost_usd": "0",
        "b6_started": False,
        "paper_order_sent": False,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    return artifact


def verify_readiness(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = payload.get("artifact_hash")
    _need(isinstance(observed, str), "BLOCK_ARTIFACT_HASH")
    _need(observed == canonical_sha256(payload, exclude_fields=("artifact_hash",)), "BLOCK_ARTIFACT_SELF_HASH")
    _need(dict(payload) == build_readiness(**inputs), "BLOCK_ARTIFACT_DRIFT")
    return observed


def write_artifact_exclusive(path: Path, artifact: Mapping[str, object]) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ReadinessBlocked("BLOCK_ARTIFACT_EXISTS") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(artifact, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def default_artifact_path(repository: Path, readiness_repository_head: str) -> Path:
    return repository / ".aic-runtime" / f"b4_ttl_judge_activation_readiness_zero_call_v0_1__{readiness_repository_head[:7]}.json"


def main(argv: Sequence[str] | None = None, *, output: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--evaluation-time-utc", default=DEFAULT_EVALUATION_TIME_UTC)
    parser.add_argument("--artifact-path", type=Path)
    arguments = parser.parse_args(argv)
    destination = output if output is not None else sys.stdout
    try:
        repository = arguments.repository.resolve()
        artifact = build_readiness(
            repository=repository,
            evaluation_time_utc=_utc(arguments.evaluation_time_utc),
            readiness_repository_head=current_head(repository),
        )
        path = arguments.artifact_path or default_artifact_path(
            repository, str(artifact["readiness_repository_head"])
        )
        write_artifact_exclusive(path, artifact)
    except ReadinessBlocked as exc:
        print(f"TTL_JUDGE_ACTIVATION_READINESS_STATUS={exc}", file=destination)
        return 1
    for key in (
        "ttl_status", "trigger", "proposal_policy_hash", "prospective_model_run_ref",
        "prospective_judge_input_hash", "prospective_request_hash", "request_body_utf8_bytes",
        "input_tokens_upper_bound", "max_output_tokens", "max_call_count", "automatic_retries",
        "judge_max_cost_usd", "activation_status", "cost_approval_status",
    ):
        print(f"{key.upper()}={artifact[key]}", file=destination)
    print(f"READINESS_ARTIFACT_PATH={path}", file=destination)
    print(f"READINESS_ARTIFACT_HASH={artifact['artifact_hash']}", file=destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
