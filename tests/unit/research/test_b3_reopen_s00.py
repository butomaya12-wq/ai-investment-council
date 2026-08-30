from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import RESEARCH_REOPEN_REQUEST_V1
from aic.research import reopen_s00
from aic.research.reopen_s00 import (
    B3ResearchReopenAuthority,
    ResearchReopenS00Error,
    build_research_reopen_s00_artifact,
    load_reopen_authority,
)


def _synthetic() -> tuple[dict, B3ResearchReopenAuthority]:
    reopen = RESEARCH_REOPEN_REQUEST_V1.from_unhashed(
        reopen_request_id="REOPEN_TEST",
        parent_run_id="PARENT_B4",
        parent_decision_id=None,
        trigger_bundle_id=None,
        reason_codes=["ALPACA_NEWS_PAGINATION_INCOMPLETE"],
        source_ref_ids=["ALPACA_NEWS_PAGINATION_INCOMPLETE"],
        requested_at=datetime(2026, 8, 30, 16, 0, tzinfo=UTC),
        new_run_start_state="S00",
    )
    source_hash = canonical_sha256({"synthetic": "production-result"})
    production = {
        "artifact_hash": source_hash,
        "run_id": "B4_PRODUCTION_TEST",
        "research_reopen_request_hash": reopen.request_hash,
        "research_reopen_request": reopen.model_dump(
            mode="json", exclude_none=False, warnings=False
        ),
        "new_run_start_state": "S00",
        "next_lifecycle": "B3_RESEARCH_REOPEN_LINKED_S00",
        "final_decision_created": False,
        "b5_handoff_created": False,
    }
    authority = B3ResearchReopenAuthority(
        authority_version="B3_RESEARCH_REOPEN_AUTHORITY_v0_1",
        source_production_judge_result_hash=source_hash,
        source_research_reopen_request_hash=reopen.request_hash,
        required_source_ref_ids=("ALPACA_NEWS_PAGINATION_INCOMPLETE",),
        expected_new_run_start_state="S00",
        expected_next_lifecycle="B3_RESEARCH_REOPEN_LINKED_S00",
        final_decision_allowed=False,
        b5_handoff_allowed=False,
        paid_model_calls_authorized_at_s00=False,
        provider_reads_authorized_at_s00=False,
        broker_writes_authorized=False,
        alpaca_orders_authorized=False,
        live_money="PROHIBITED",
    )
    return production, authority


def test_event_reopen_authority_binds_exact_production_hashes() -> None:
    authority = load_reopen_authority(
        Path("config/event/b3_research_reopen_authority_v1.json")
    )
    assert authority.source_production_judge_result_hash == (
        "3354123bc0244ec258fad0cdab57d5551d5ed8e5d58088d11482bdcd489d259e"
    )
    assert authority.source_research_reopen_request_hash == (
        "eb4c06f47f372413d25b25632ba84a35057fdbb9d244c4f1960f6b7fb40dfeb1"
    )
    assert authority.required_source_ref_ids == (
        "ALPACA_NEWS_PAGINATION_INCOMPLETE",
    )
    assert authority.provider_reads_authorized_at_s00 is False


def test_s00_link_is_zero_call_and_preserves_reopen(monkeypatch) -> None:
    production, authority = _synthetic()
    monkeypatch.setattr(
        reopen_s00,
        "verify_judge_production_success_artifact",
        lambda payload: payload["artifact_hash"],
    )
    artifact = build_research_reopen_s00_artifact(
        production,
        authority=authority,
        code_commit_sha="a" * 40,
    )
    assert artifact["status"] == "B3_RESEARCH_REOPEN_S00_LINKED"
    assert artifact["source_research_reopen_request_hash"] == authority.source_research_reopen_request_hash
    assert artifact["required_source_ref_ids"] == ["ALPACA_NEWS_PAGINATION_INCOMPLETE"]
    assert artifact["new_run_start_state"] == "S00"
    assert artifact["next_lifecycle"] == "B3_RESEARCH_REOPEN_LINKED_S00"
    assert artifact["model_calls"] == 0
    assert artifact["provider_reads"] == 0
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert artifact["live_money"] == "PROHIBITED"
    assert artifact["artifact_hash"] == canonical_sha256(
        artifact, exclude_fields=("artifact_hash",)
    )


def test_s00_link_rejects_missing_required_gap(monkeypatch) -> None:
    production, authority = _synthetic()
    reopen = RESEARCH_REOPEN_REQUEST_V1.from_unhashed(
        reopen_request_id="REOPEN_TEST_OTHER",
        parent_run_id="PARENT_B4",
        parent_decision_id=None,
        trigger_bundle_id=None,
        reason_codes=["OTHER_GAP"],
        source_ref_ids=["OTHER_GAP"],
        requested_at=datetime(2026, 8, 30, 16, 0, tzinfo=UTC),
        new_run_start_state="S00",
    )
    production["research_reopen_request"] = reopen.model_dump(
        mode="json", exclude_none=False, warnings=False
    )
    production["research_reopen_request_hash"] = reopen.request_hash
    authority = authority.model_copy(
        update={"source_research_reopen_request_hash": reopen.request_hash}
    )
    monkeypatch.setattr(
        reopen_s00,
        "verify_judge_production_success_artifact",
        lambda payload: payload["artifact_hash"],
    )
    with pytest.raises(ResearchReopenS00Error, match="lost required source refs"):
        build_research_reopen_s00_artifact(
            production,
            authority=authority,
            code_commit_sha="b" * 40,
        )


def test_s00_script_has_no_paid_or_provider_execution_surface() -> None:
    source = Path("scripts/b3_research_reopen_s00_v01.py").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in source
    assert "execute_paid" not in source
    assert "urlopen" not in source
    assert "alpaca data" not in source.lower()
