from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aic.council.input_bundle import build_council_input_freeze
from aic.research.handoff import load_real_event_handoff
from aic.research.mandate import load_competition_investment_mandate


DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_council_input_freeze.json")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact root must be object: {path}")
    return value


def _public_summary(artifact: object, *, output_path: Path) -> dict[str, Any]:
    payload = artifact.model_dump(mode="json")
    return {
        "artifact_version": payload["artifact_version"],
        "run_class": payload["run_class"],
        "status": "B4_COUNCIL_INPUTS_FROZEN",
        "b3_reconciliation_artifact_hash": payload["b3_reconciliation_artifact_hash"],
        "b2_handoff_hash": payload["b2_handoff_hash"],
        "mandate_version": payload["mandate_version"],
        "candidate_order": payload["candidate_order"],
        "bundles": [
            {
                "candidate": bundle["candidate_id"],
                "bundle_id": bundle["bundle_id"],
                "bundle_hash": bundle["bundle_hash"],
                "candidate_packet_id": bundle["candidate_packet_id"],
                "candidate_packet_hash": bundle["candidate_packet_hash"],
                "research_snapshot_hash": bundle["research_snapshot_hash"],
                "allowed_material_claim_count": len(bundle["allowed_material_claim_ids"]),
                "allowed_computed_value_count": len(bundle["allowed_computed_value_ids"]),
                "allowed_conflict_count": len(bundle["allowed_conflict_ids"]),
            }
            for bundle in payload["bundles"]
        ],
        "model_calls": payload["model_calls"],
        "provider_reads": payload["provider_reads"],
        "broker_writes": payload["broker_writes"],
        "alpaca_orders": payload["alpaca_orders"],
        "live_money": payload["live_money"],
        "artifact_hash": payload["artifact_hash"],
        "output_path": str(output_path),
    }


def main() -> int:
    try:
        reconciliation = _read_json(DEFAULT_RECONCILIATION)
        handoff = load_real_event_handoff(DEFAULT_HANDOFF)
        mandate = load_competition_investment_mandate()
        artifact = build_council_input_freeze(
            reconciliation,
            expected_handoff_hash=handoff.handoff_hash,
            mandate_version=mandate.version,
            created_at=datetime.now(UTC),
        )
        DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUTPUT.write_text(
            json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(json.dumps(_public_summary(artifact, output_path=DEFAULT_OUTPUT), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"B4 Council input freeze failed closed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
