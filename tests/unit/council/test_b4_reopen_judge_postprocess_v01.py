from __future__ import annotations

from pathlib import Path

from aic.council.reopen_judge_postprocess_v01 import (
    EXPECTED_REOPEN_REASONS,
    NEXT_GATE,
    POSTPROCESS_STATUS,
    REOPEN_REQUEST_ID,
    build_postprocess_artifact,
    build_research_reopen_request,
)
from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import RESEARCH_REOPEN_REQUEST_V1


FINISHED = "2026-08-31T06:27:54.582282Z"


def _source() -> tuple[dict, dict, dict, dict]:
    result = {
        "artifact_hash": "1" * 64,
        "code_commit_sha": "2" * 40,
        "run_id": "AIC-B4-REOPEN-JUDGE-SYNTHETIC",
        "judge_proposal_hash": "3" * 64,
        "research_reopen_reason_codes": list(EXPECTED_REOPEN_REASONS),
    }
    authorization = {"artifact_hash": "4" * 64}
    attempt = {"event_hash": "5" * 64}
    receipt = {
        "receipt_hash": "6" * 64,
        "dispatch_finished_at_utc": FINISHED,
    }
    return result, authorization, attempt, receipt


def test_reopen_request_is_canonical_deterministic_s00_lineage() -> None:
    result, _, _, receipt = _source()
    first = build_research_reopen_request(result, receipt)
    second = build_research_reopen_request(result, receipt)

    assert first == second
    assert first.reopen_request_id == REOPEN_REQUEST_ID
    assert first.parent_run_id == result["run_id"]
    assert first.parent_decision_id is None
    assert first.trigger_bundle_id is None
    assert tuple(first.reason_codes) == EXPECTED_REOPEN_REASONS
    assert tuple(first.source_ref_ids) == EXPECTED_REOPEN_REASONS
    assert first.new_run_start_state == "S00"

    payload = first.model_dump(mode="json", exclude_none=False, warnings=False)
    RESEARCH_REOPEN_REQUEST_V1.model_validate(payload)
    assert payload["request_hash"] == canonical_sha256(
        payload, exclude_fields=("request_hash",)
    )


def test_postprocess_persists_one_reopen_and_blocks_final_decision_b5() -> None:
    result, authorization, attempt, receipt = _source()
    reopen = build_research_reopen_request(result, receipt)
    artifact = build_postprocess_artifact(
        result=result,
        authorization=authorization,
        attempt=attempt,
        receipt=receipt,
        reopen_request=reopen,
        code_commit_sha="a" * 40,
    )

    assert artifact["status"] == POSTPROCESS_STATUS
    assert artifact["research_reopen_request_count"] == 1
    assert artifact["research_reopen_request_hash"] == reopen.request_hash
    assert artifact["new_run_start_state"] == "S00"
    assert artifact["research_run_started"] is False
    assert artifact["final_decision_created"] is False
    assert artifact["final_decision_allowed"] is False
    assert artifact["b5_handoff_created"] is False
    assert artifact["b5_handoff_allowed"] is False
    assert artifact["execution_authority"] is False
    assert artifact["paid_model_calls_authorized"] is False
    assert artifact["provider_reads_authorized"] is False
    assert artifact["next_gate"] == NEXT_GATE
    assert artifact["model_calls"] == 0
    assert artifact["provider_reads"] == 0
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert artifact["live_money"] == "PROHIBITED"
    assert artifact["artifact_hash"] == canonical_sha256(
        artifact, exclude_fields=("artifact_hash",)
    )


def test_postprocess_uses_frozen_receipt_time_not_wall_clock() -> None:
    result, _, _, receipt = _source()
    reopen = build_research_reopen_request(result, receipt)
    payload = reopen.model_dump(mode="json", exclude_none=False, warnings=False)
    assert payload["requested_at"] == FINISHED


def test_runner_has_no_provider_or_paid_execution_surface() -> None:
    source = Path(
        "scripts/b4_reopen_judge_proposal_postprocess_zero_call_v01.py"
    ).read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in source
    assert "urlopen" not in source
    assert "StdlibResponsesTransport" not in source
    assert "execute_paid" not in source
    assert "alpaca data" not in source.lower()
