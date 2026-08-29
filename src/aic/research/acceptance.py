from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .handoff import EXPECTED_TOP3, load_real_event_handoff
from .independent_review import ATTACK_CLASSES
from .mandate import load_competition_investment_mandate
from .model_selection import load_selected_model_authority, verify_model_eval_artifact
from .plan_freeze import load_frozen_planner_batch


MANIFEST_VERSION = "B3_ACCEPTANCE_MANIFEST_v0_1"
ACCEPTANCE_ARTIFACT_VERSION = "B3_FINAL_ACCEPTANCE_ARTIFACT_v0_1"
ACCEPTANCE_RUN_CLASS = "B3_FINAL_ZERO_CALL_ACCEPTANCE_GATE"
DEFAULT_MANIFEST_PATH = Path("config/event/b3_acceptance_manifest_v1.json")
DEFAULT_HANDOFF_PATH = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_PLANNER_PATH = Path(".aic-runtime/b3_planner_batch.json")
DEFAULT_RETRIEVAL_PATH = Path(".aic-runtime/b3_retrieval_batch.json")
DEFAULT_MODEL_EVAL_PATH = Path(".aic-runtime/b3_model_eval.json")
DEFAULT_MODEL_AUTHORITY_PATH = Path("config/event/b3_selected_model_v1.json")
DEFAULT_RECONCILIATION_PATH = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_REVIEW_PATH = Path(".aic-runtime/b3_independent_review.json")
DEFAULT_MANDATE_PATH = Path("config/event/investment_mandate_competition_v1.json")
EXPECTED_SOURCE_GAP = "ALPACA_NEWS_PAGINATION_INCOMPLETE"
EXPECTED_CHECK_IDS = tuple(f"B3-V{index:03d}" for index in range(1, 49))
EXPECTED_EVAL_IDS = frozenset(f"E{index}" for index in range(1, 13))
ALLOWED_RUNTIME_REFS = frozenset(
    {
        "RETRIEVAL_PARTIAL_PAGINATION",
        "RECONCILIATION_SOURCE_GAPS",
        "RECONCILIATION_TOP3_ISOLATED",
        "RECONCILIATION_LINEAGE",
        "RECONCILIATION_REPAIR_BINDING",
        "NO_UNAPPROVED_ESCALATION",
        "HISTORICAL_BLOCKED_MODEL_EVAL",
        "RECONCILIATION_REFERENCES",
        "RECONCILIATION_ALL_INCOMPLETE",
        "FINAL_PROMPT_MANIFEST_EVAL_ALL_PASS",
        "MODEL_EVAL_SELECTION_M2",
        "MODEL_EVAL_USAGE_COST_LATENCY",
        "RECONCILIATION_RECONSTRUCTIBLE",
        "INDEPENDENT_REVIEW_RECONSTRUCTIBLE",
    }
)


class B3AcceptanceError(ValueError):
    pass


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B3AcceptanceError(f"unable to read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise B3AcceptanceError(f"{label} root must be an object")
    return value


def _verify_artifact_hash(payload: Mapping[str, Any], *, label: str) -> str:
    actual = payload.get("artifact_hash")
    if not isinstance(actual, str) or len(actual) != 64:
        raise B3AcceptanceError(f"{label} artifact_hash missing")
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    if actual != expected:
        raise B3AcceptanceError(f"{label} artifact_hash mismatch")
    return actual


def _candidate_map(payload: Mapping[str, Any], *, label: str) -> dict[str, Mapping[str, Any]]:
    raw = payload.get("candidates")
    if not isinstance(raw, list):
        raise B3AcceptanceError(f"{label} candidates missing")
    items = {
        item.get("candidate"): item
        for item in raw
        if isinstance(item, Mapping) and isinstance(item.get("candidate"), str)
    }
    if tuple(items) != EXPECTED_TOP3:
        raise B3AcceptanceError(f"{label} must contain exact frozen top-3 order")
    return items


def _validate_evidence_ref(ref: str, *, repo_root: Path) -> None:
    if ":" not in ref:
        raise B3AcceptanceError(f"acceptance evidence ref lacks namespace: {ref}")
    kind, value = ref.split(":", 1)
    if kind in {"TEST", "STATIC"}:
        path = repo_root / value
        if not path.is_file():
            raise B3AcceptanceError(f"acceptance evidence file missing: {value}")
        return
    if kind == "EVAL":
        if value not in EXPECTED_EVAL_IDS:
            raise B3AcceptanceError(f"unknown frozen eval case in acceptance manifest: {value}")
        return
    if kind == "REVIEW":
        if value not in ATTACK_CLASSES:
            raise B3AcceptanceError(f"unknown independent-review attack class: {value}")
        return
    if kind == "RUNTIME":
        if value not in ALLOWED_RUNTIME_REFS:
            raise B3AcceptanceError(f"unknown runtime evidence class: {value}")
        return
    raise B3AcceptanceError(f"unknown acceptance evidence namespace: {kind}")


def load_and_validate_acceptance_manifest(
    path: Path = DEFAULT_MANIFEST_PATH,
    *,
    repo_root: Path = Path("."),
) -> dict[str, Any]:
    manifest = _read_json(path, label="B3 acceptance manifest")
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise B3AcceptanceError("B3 acceptance manifest version drift")
    if manifest.get("required_check_count") != 48:
        raise B3AcceptanceError("B3 acceptance manifest must require exactly 48 checks")
    raw_checks = manifest.get("checks")
    if not isinstance(raw_checks, list):
        raise B3AcceptanceError("B3 acceptance checks missing")
    ids = tuple(
        item.get("check_id")
        for item in raw_checks
        if isinstance(item, Mapping)
    )
    if ids != EXPECTED_CHECK_IDS:
        raise B3AcceptanceError("B3 acceptance checks must be exact ordered B3-V001..B3-V048")
    for item in raw_checks:
        if not isinstance(item, Mapping):
            raise B3AcceptanceError("B3 acceptance check must be object")
        check_id = item.get("check_id")
        if item.get("status") != "PASS":
            raise B3AcceptanceError(f"{check_id} is not PASS")
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref for ref in refs):
            raise B3AcceptanceError(f"{check_id} has no evidence refs")
        if not isinstance(item.get("basis"), str) or not item.get("basis"):
            raise B3AcceptanceError(f"{check_id} has no acceptance basis")
        for ref in refs:
            _validate_evidence_ref(ref, repo_root=repo_root)
    return manifest


def _require_zero_write_safety(payload: Mapping[str, Any], *, label: str) -> None:
    if payload.get("broker_writes") != 0:
        raise B3AcceptanceError(f"{label} broker_writes must be zero")
    if payload.get("alpaca_orders") != 0:
        raise B3AcceptanceError(f"{label} alpaca_orders must be zero")
    if payload.get("live_money") != "PROHIBITED":
        raise B3AcceptanceError(f"{label} live_money must be PROHIBITED")


def verify_b3_final_acceptance(
    *,
    repo_root: Path = Path("."),
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    handoff_path: Path = DEFAULT_HANDOFF_PATH,
    planner_path: Path = DEFAULT_PLANNER_PATH,
    retrieval_path: Path = DEFAULT_RETRIEVAL_PATH,
    model_eval_path: Path = DEFAULT_MODEL_EVAL_PATH,
    model_authority_path: Path = DEFAULT_MODEL_AUTHORITY_PATH,
    reconciliation_path: Path = DEFAULT_RECONCILIATION_PATH,
    review_path: Path = DEFAULT_REVIEW_PATH,
    mandate_path: Path = DEFAULT_MANDATE_PATH,
) -> dict[str, Any]:
    manifest = load_and_validate_acceptance_manifest(manifest_path, repo_root=repo_root)
    expected = manifest.get("expected_artifacts")
    if not isinstance(expected, Mapping):
        raise B3AcceptanceError("acceptance manifest expected_artifacts missing")

    handoff = load_real_event_handoff(handoff_path)
    plans = load_frozen_planner_batch(planner_path)
    retrieval = _read_json(retrieval_path, label="retrieval artifact")
    model_eval = _read_json(model_eval_path, label="model eval artifact")
    reconciliation = _read_json(reconciliation_path, label="reconciliation artifact")
    review = _read_json(review_path, label="independent review artifact")
    mandate = load_competition_investment_mandate(mandate_path)
    authority = load_selected_model_authority(model_authority_path)

    retrieval_hash = _verify_artifact_hash(retrieval, label="retrieval")
    reconciliation_hash = _verify_artifact_hash(reconciliation, label="reconciliation")
    review_hash = _verify_artifact_hash(review, label="independent review")
    verify_model_eval_artifact(model_eval, authority=authority)

    exact_hashes = {
        "handoff_hash": handoff.handoff_hash,
        "planner_artifact_hash": plans.artifact_hash,
        "retrieval_artifact_hash": retrieval_hash,
        "model_eval_artifact_hash": model_eval.get("artifact_hash"),
        "selected_model_authority_hash": authority.selection_hash,
        "reconciliation_artifact_hash": reconciliation_hash,
        "independent_review_artifact_hash": review_hash,
    }
    for key, actual in exact_hashes.items():
        if expected.get(key) != actual:
            raise B3AcceptanceError(f"frozen acceptance hash mismatch: {key}")

    if plans.handoff_hash != handoff.handoff_hash:
        raise B3AcceptanceError("planner artifact does not bind frozen handoff")
    if retrieval.get("handoff_hash") != handoff.handoff_hash:
        raise B3AcceptanceError("retrieval artifact does not bind frozen handoff")
    if retrieval.get("planner_artifact_hash") != plans.artifact_hash:
        raise B3AcceptanceError("retrieval artifact does not bind frozen planner")
    _require_zero_write_safety(retrieval, label="retrieval")

    retrieval_candidates = _candidate_map(retrieval, label="retrieval")
    for candidate, record in retrieval_candidates.items():
        if record.get("status") != "PARTIAL":
            raise B3AcceptanceError(f"{candidate} frozen retrieval status must remain PARTIAL")
        receipts = record.get("provider_receipts")
        if not isinstance(receipts, list) or not receipts:
            raise B3AcceptanceError(f"{candidate} retrieval provider receipts missing")
        alpaca_partial = any(
            isinstance(receipt, Mapping)
            and receipt.get("provider") == "ALPACA"
            and receipt.get("pagination_complete") is False
            and receipt.get("error") is None
            for receipt in receipts
        )
        if not alpaca_partial:
            raise B3AcceptanceError(f"{candidate} expected bounded Alpaca pagination gap missing")

    selected = manifest.get("selected_model")
    if not isinstance(selected, Mapping):
        raise B3AcceptanceError("selected model acceptance authority missing")
    actual_selected = authority.selected_candidate.model_dump(mode="json")
    for field in ("candidate_key", "model", "reasoning_effort"):
        if actual_selected.get(field) != selected.get(field):
            raise B3AcceptanceError(f"selected model mismatch: {field}")
    if mandate.version != manifest.get("mandate_version"):
        raise B3AcceptanceError("accepted mandate version mismatch")

    eval_records = model_eval.get("candidates")
    if not isinstance(eval_records, list) or len(eval_records) != 3:
        raise B3AcceptanceError("model eval full ladder missing")
    for record in eval_records:
        if not isinstance(record, Mapping):
            raise B3AcceptanceError("model eval record malformed")
        cases = record.get("cases")
        if record.get("all_required_checks_passed") is not True or record.get("critical_safety_failures") != 0:
            raise B3AcceptanceError("model eval candidate does not satisfy frozen quality/safety bar")
        if not isinstance(cases, list) or len(cases) != 12 or not all(
            isinstance(case, Mapping) and case.get("passed") is True for case in cases
        ):
            raise B3AcceptanceError("model eval E1-E12 coverage is not all PASS")

    if reconciliation.get("handoff_hash") != handoff.handoff_hash:
        raise B3AcceptanceError("reconciliation handoff lineage mismatch")
    if reconciliation.get("planner_artifact_hash") != plans.artifact_hash:
        raise B3AcceptanceError("reconciliation planner lineage mismatch")
    if reconciliation.get("retrieval_artifact_hash") != retrieval_hash:
        raise B3AcceptanceError("reconciliation retrieval lineage mismatch")
    if reconciliation.get("model_eval_artifact_hash") != model_eval.get("artifact_hash"):
        raise B3AcceptanceError("reconciliation model-eval lineage mismatch")
    if reconciliation.get("selected_model_authority_hash") != authority.selection_hash:
        raise B3AcceptanceError("reconciliation selected-model authority mismatch")
    if reconciliation.get("mandate_version") != mandate.version:
        raise B3AcceptanceError("reconciliation mandate lineage mismatch")
    if reconciliation.get("canonical_reconciliation") != "ALL_THREE_SELECTED_MODEL_CANDIDATE_PACKETS_RECONCILED":
        raise B3AcceptanceError("canonical reconciliation is incomplete")
    if reconciliation.get("reconstructibility_status") != "PASS":
        raise B3AcceptanceError("reconciliation is not reconstructible")
    _require_zero_write_safety(reconciliation, label="reconciliation")

    reconciliation_candidates = _candidate_map(reconciliation, label="reconciliation")
    forbidden_packet_fields = {"investment_decision", "trade_action", "position_size", "broker_command"}
    candidate_summary: list[dict[str, Any]] = []
    for candidate, record in reconciliation_candidates.items():
        if record.get("status") != "CANONICAL_RECONCILED":
            raise B3AcceptanceError(f"{candidate} is not CANONICAL_RECONCILED")
        if record.get("research_status") != "INCOMPLETE":
            raise B3AcceptanceError(f"{candidate} must preserve INCOMPLETE research status")
        if record.get("source_gaps") != [EXPECTED_SOURCE_GAP]:
            raise B3AcceptanceError(f"{candidate} source gap drift")
        if record.get("repair_attempts") not in (0, 1):
            raise B3AcceptanceError(f"{candidate} repair bound violated")
        if record.get("reconstructibility_status") != "PASS":
            raise B3AcceptanceError(f"{candidate} reconciliation not reconstructible")
        packet = record.get("candidate_packet")
        receipt = record.get("model_run_receipt")
        if not isinstance(packet, Mapping) or not isinstance(receipt, Mapping):
            raise B3AcceptanceError(f"{candidate} canonical packet/receipt missing")
        if forbidden_packet_fields.intersection(packet):
            raise B3AcceptanceError(f"{candidate} packet contains forbidden B3 trade/decision field")
        if receipt.get("store") is not False or receipt.get("tools_enabled") is not False:
            raise B3AcceptanceError(f"{candidate} model receipt violates store/tool boundary")
        if not isinstance(receipt.get("openai_response_id"), str) or not receipt.get("openai_response_id"):
            raise B3AcceptanceError(f"{candidate} model response id missing")
        candidate_summary.append(
            {
                "candidate": candidate,
                "packet_hash": packet.get("packet_hash"),
                "research_status": record.get("research_status"),
                "repair_attempts": record.get("repair_attempts"),
                "source_gaps": record.get("source_gaps"),
                "reconstructibility_status": record.get("reconstructibility_status"),
            }
        )

    if review.get("review_status") != "PASS":
        raise B3AcceptanceError("independent review is not PASS")
    if review.get("review_reconstructibility_status") != "PASS":
        raise B3AcceptanceError("independent review is not reconstructible")
    if review.get("repair_attempts") != 0 or review.get("provider_reads") != 0:
        raise B3AcceptanceError("independent review violated read-only/no-repair boundary")
    _require_zero_write_safety(review, label="independent review")
    model_call = review.get("model_call")
    if not isinstance(model_call, Mapping) or not isinstance(model_call.get("response_id"), str) or not model_call.get("response_id"):
        raise B3AcceptanceError("independent review model_call.response_id missing")
    review_output = review.get("review")
    if not isinstance(review_output, Mapping):
        raise B3AcceptanceError("independent review output missing")
    attacks = review_output.get("attack_results")
    if not isinstance(attacks, list):
        raise B3AcceptanceError("independent review attack results missing")
    attack_ids = tuple(
        item.get("attack_class") for item in attacks if isinstance(item, Mapping)
    )
    if attack_ids != ATTACK_CLASSES or not all(
        isinstance(item, Mapping) and item.get("status") == "PASS" for item in attacks
    ):
        raise B3AcceptanceError("independent review must be exact 15/15 PASS")
    if review_output.get("material_gap_summary") != [] or review_output.get("inconclusive_summary") != []:
        raise B3AcceptanceError("independent review contains material gaps or inconclusive findings")

    checks = manifest["checks"]
    artifact: dict[str, Any] = {
        "artifact_version": ACCEPTANCE_ARTIFACT_VERSION,
        "run_class": ACCEPTANCE_RUN_CLASS,
        "status": "B3_IMPLEMENTATION_PASS",
        "manifest_hash": canonical_sha256(manifest),
        "required_checks": 48,
        "checks_passed": 48,
        "checks_failed": 0,
        "checks_not_run": 0,
        "check_ids": [item["check_id"] for item in checks],
        "artifact_lineage": exact_hashes,
        "selected_model": selected,
        "mandate_version": mandate.version,
        "candidates": candidate_summary,
        "independent_review": {
            "artifact_hash": review_hash,
            "response_id": model_call.get("response_id"),
            "attack_class_count": len(attacks),
            "material_gap_count": 0,
            "inconclusive_count": 0,
            "reconstructibility_status": review.get("review_reconstructibility_status"),
        },
        "model_calls_performed_by_this_gate": 0,
        "provider_reads_performed_by_this_gate": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": "B4_AUTHORIZED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
