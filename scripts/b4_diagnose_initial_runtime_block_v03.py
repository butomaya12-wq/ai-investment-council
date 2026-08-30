from __future__ import annotations

import argparse
import json
from pathlib import Path

from aic.council.initial_runtime_diagnosis_v03 import (
    diagnose_blocked_initial_runtime_v03,
)
from aic.council.initial_runtime_preflight import verify_initial_runtime_request_preflight
from aic.council.initial_schema_repair_v03 import (
    INITIAL_SCHEMA_REPAIR_VERSION,
    INITIAL_SCHEMA_VERSION,
)
from aic.council.model_input import build_initial_model_inputs
from aic.council.model_selection import load_initial_selected_model_authority
from aic.council.models import CouncilInputFreezeArtifact
from aic.domain.canonical import canonical_sha256
from aic.research.handoff import load_real_event_handoff


DEFAULT_BLOCKED = Path(".aic-runtime/b4_initial_council_freeze_v0_3.json")
DEFAULT_RECEIPTS = Path(".aic-runtime/b4_initial_runtime_paid_receipts_v0_3.jsonl")
DEFAULT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_RUNTIME_PREFLIGHT = Path(".aic-runtime/b4_initial_runtime_request_preflight_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_initial_runtime_block_diagnosis_v0_3.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blocked", type=Path, default=DEFAULT_BLOCKED)
    parser.add_argument("--receipts", type=Path, default=DEFAULT_RECEIPTS)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--reconciliation", type=Path, default=DEFAULT_RECONCILIATION)
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--runtime-preflight", type=Path, default=DEFAULT_RUNTIME_PREFLIGHT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _read_json(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return raw


def _read_receipts(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError("receipt journal line must be object")
        rows.append(raw)
    return rows


def main() -> int:
    args = _args()
    blocked = _read_json(args.blocked)
    receipts = _read_receipts(args.receipts)
    freeze = CouncilInputFreezeArtifact.model_validate(_read_json(args.freeze))
    reconciliation = _read_json(args.reconciliation)
    handoff = load_real_event_handoff(args.handoff)
    runtime_preflight = _read_json(args.runtime_preflight)
    verify_initial_runtime_request_preflight(runtime_preflight)
    if runtime_preflight.get("initial_schema_repair_version") != INITIAL_SCHEMA_REPAIR_VERSION:
        raise ValueError("runtime preflight does not bind v0.3 schema repair")
    if runtime_preflight.get("initial_schema_version") != INITIAL_SCHEMA_VERSION:
        raise ValueError("runtime preflight does not bind v0.3 repaired schema")
    authority = load_initial_selected_model_authority()
    model_inputs = build_initial_model_inputs(freeze, reconciliation, handoff)

    artifact = diagnose_blocked_initial_runtime_v03(
        blocked_artifact=blocked,
        receipts=receipts,
        freeze=freeze,
        model_inputs=model_inputs,
        runtime_preflight=runtime_preflight,
        authority=authority,
    )
    expected_hash = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    if artifact["artifact_hash"] != expected_hash:
        raise ValueError("v0.3 diagnosis artifact hash mismatch")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    public = {
        "status": artifact["status"],
        "source_run_id": artifact["source_run_id"],
        "source_blocked_artifact_hash": artifact["source_blocked_artifact_hash"],
        "recorded_blocked_reason": artifact["recorded_blocked_reason"],
        "exact_promotion_error_reproduced": artifact["exact_promotion_error_reproduced"],
        "missing_inference_provenance_count": artifact["missing_inference_provenance_count"],
        "promotion_rule_failure_count": artifact["promotion_rule_failure_count"],
        "contract_gap_signal": artifact["contract_gap_signal"],
        "replay_records": artifact["replay_records"],
        "model_calls_performed_by_diagnosis": 0,
        "provider_reads_performed_by_diagnosis": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "rerun_authorized": False,
        "artifact_hash": artifact["artifact_hash"],
        "output_path": str(args.output),
    }
    print(json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
