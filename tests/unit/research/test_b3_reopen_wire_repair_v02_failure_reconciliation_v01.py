from __future__ import annotations

from pathlib import Path

import pytest

from aic.research import reopen_judge_residual_external_read_plan_v01 as original_plan
from aic.research import reopen_judge_wire_repair_v02_failure_reconciliation_v01 as reconciliation


def test_original_er5_contract_proves_timeframe_spelling_defect() -> None:
    er5 = next(
        row
        for row in original_plan._read_bundles()
        if row["bundle_id"] == "ER5_CURRENT_PORTFOLIO_EQUITY"
    )
    assert er5["request_contract"]["cli_command"] == [
        "alpaca",
        "account",
        "portfolio",
    ]
    assert er5["request_contract"]["timeframe"] == "1Day"
    assert reconciliation.FROZEN_PORTFOLIO_TIMEFRAME_INVALID == "1Day"
    assert reconciliation.PORTFOLIO_TIMEFRAME_REPAIR_CANDIDATE == "1D"
    assert (
        reconciliation.FROZEN_PORTFOLIO_TIMEFRAME_INVALID
        != reconciliation.PORTFOLIO_TIMEFRAME_REPAIR_CANDIDATE
    )


def test_portfolio_contract_helper_rejects_missing_or_duplicate_er5() -> None:
    with pytest.raises(
        reconciliation.WireRepairV02FailureReconciliationError,
        match="missing or duplicated",
    ):
        reconciliation._portfolio_contract_from_original_preflight(
            {"request_preflights": []}
        )

    row = {
        "bundle_id": "ER5_CURRENT_PORTFOLIO_EQUITY",
        "resolved_request_contract": {"timeframe": "1Day"},
    }
    with pytest.raises(
        reconciliation.WireRepairV02FailureReconciliationError,
        match="missing or duplicated",
    ):
        reconciliation._portfolio_contract_from_original_preflight(
            {"request_preflights": [row, row]}
        )


def test_build_reconciliation_retains_completed_bundles_and_shrinks_future_ceiling(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        reconciliation,
        "_verify_original_portfolio_contract",
        lambda _payload: None,
    )
    monkeypatch.setattr(
        reconciliation,
        "_verify_authorization",
        lambda _payload: reconciliation.EXPECTED_AUTH_HASH,
    )
    monkeypatch.setattr(
        reconciliation,
        "_verify_result",
        lambda _payload, authorization_hash: reconciliation.EXPECTED_RESULT_HASH,
    )
    monkeypatch.setattr(
        reconciliation,
        "_verify_journal_and_raw",
        lambda _rows, raw_dir, authorization_hash: None,
    )

    artifact = reconciliation.build_reconciliation(
        authorization={},
        result={},
        original_preflight={},
        journal_rows=[],
        raw_dir=tmp_path,
        code_commit_sha="f" * 40,
    )

    assert artifact["authority_consumed"] is True
    assert artifact["authority_reusable"] is False
    assert artifact["production_rerun_allowed"] is False
    assert artifact["completed_bundle_count"] == 3
    assert artifact["completed_bundle_ids"] == [
        "CR1_MSFT_NEWS_REFRESH",
        "CR2_META_NEWS_REFRESH",
        "CR3_CURRENT_PAPER_POSITIONS",
    ]
    assert artifact["completed_bundle_reread_allowed"] is False
    assert artifact["msft_news_reread_allowed"] is False
    assert artifact["meta_news_reread_allowed"] is False
    assert artifact["positions_reread_allowed"] is False
    assert artifact["current_paper_equity_position_symbols"] == []
    assert artifact["failed_bundle_id"] == "CR4_CURRENT_PORTFOLIO_EQUITY"
    assert artifact["cr4_stderr_durably_retained"] is False
    assert artifact["portfolio_timeframe_defect_proven_as_sole_runtime_failure_cause"] is False
    assert artifact["local_cli_capability_probe_required_before_new_owner_gate"] is True
    assert artifact["future_cli_nonzero_stdout_stderr_snapshot_required"] is True
    assert artifact["future_provider_read_bundle_ids"] == [
        "RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR",
        "RR2_DYNAMIC_MARKET_CONTEXT",
        "RR3_NVDA_NEWS_CONTINUATION",
    ]
    assert artifact["future_provider_dispatch_attempts_max"] == 6
    assert artifact["future_dispatch_ceiling_breakdown"] == {
        "RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR": 1,
        "RR2_DYNAMIC_MARKET_CONTEXT": 1,
        "RR3_NVDA_NEWS_CONTINUATION": 4,
    }
    assert artifact["provider_reads_this_step"] == 0
    assert artifact["model_calls_this_step"] == 0
    assert artifact["b5_handoff_created"] is False
    assert artifact["next_gate"] == reconciliation.NEXT_GATE


def test_expected_journal_shape_ends_with_cr4_attempt_then_failure() -> None:
    assert len(reconciliation.EXPECTED_EVENT_TYPES) == 17
    assert reconciliation.EXPECTED_EVENT_TYPES[-2:] == (
        "PROVIDER_DISPATCH_ATTEMPT",
        "BUNDLE_FAILURE",
    )
    assert reconciliation.EXPECTED_ATTEMPT_BUNDLES[-1] == (
        "CR4_CURRENT_PORTFOLIO_EQUITY"
    )
    assert len(reconciliation.EXPECTED_SNAPSHOT_KEYS) == 5
    assert all(
        key[0] != "CR4_CURRENT_PORTFOLIO_EQUITY"
        for key in reconciliation.EXPECTED_SNAPSHOT_KEYS
    )


def test_runner_is_zero_call_only_and_uses_v02_durable_inputs() -> None:
    text = Path(
        "scripts/"
        "b3_research_reopen_wire_repair_v02_failure_reconciliation_zero_call_v01.py"
    ).read_text(encoding="utf-8")
    assert "b3_research_reopen_continuation_wire_repair_authorization_v0_2.json" in text
    assert "b3_research_reopen_continuation_wire_repair_receipts_v0_2.jsonl" in text
    assert "b3_research_reopen_continuation_wire_repair_raw_v0_2" in text
    assert "b3_research_reopen_continuation_wire_repair_result_v0_2.json" in text
    assert "--execute-provider-reads" not in text
    assert '"alpaca"' not in text
    assert "PROVIDER_READS=0" in text
    assert "MODEL_CALLS=0" in text


def test_reconciliation_has_no_order_or_broker_write_surface() -> None:
    text = Path(
        "src/aic/research/"
        "reopen_judge_wire_repair_v02_failure_reconciliation_v01.py"
    ).read_text(encoding="utf-8")
    assert '"order", "submit"' not in text
    assert '"order", "cancel"' not in text
    assert '"position", "close"' not in text
    assert reconciliation.FUTURE_PROVIDER_DISPATCH_ATTEMPTS_MAX == 6
