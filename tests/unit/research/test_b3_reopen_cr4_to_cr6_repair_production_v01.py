from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aic.research import reopen_judge_cr4_to_cr6_repair_production_v01 as runtime


def _journal(tmp_path: Path) -> runtime.RepairReadJournal:
    return runtime.RepairReadJournal(
        path=tmp_path / "journal.jsonl",
        raw_dir=tmp_path / "raw",
        authorization_hash="a" * 64,
    )


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_source_dry_verifier_uses_frozen_dry_code_sha(monkeypatch):
    observed = []

    def fake_verify(payload, *, expected_code_commit_sha):
        observed.append(expected_code_commit_sha)
        return runtime.EXPECTED_DRY_HASH

    monkeypatch.setattr(runtime.dry_v01, "verify_dry", fake_verify)
    assert runtime.verify_source_dry({}) == runtime.EXPECTED_DRY_HASH
    assert observed == [runtime.EXPECTED_DRY_CODE_SHA]


def test_build_authorization_binds_exact_short_path(monkeypatch):
    monkeypatch.setattr(runtime, "verify_preflight", lambda _p: runtime.EXPECTED_PREFLIGHT_HASH)
    monkeypatch.setattr(runtime, "verify_source_dry", lambda _p: runtime.EXPECTED_DRY_HASH)
    monkeypatch.setattr(
        runtime,
        "verify_installed_cli",
        lambda _p: {"alpaca_binary_sha256": runtime.EXPECTED_ALPACA_BINARY_SHA256},
    )

    auth = runtime.build_authorization(
        preflight={},
        dry={},
        owner_approval_id=runtime.OWNER_APPROVAL_ID,
        owner_approval_at_utc="2026-08-31T13:30:00Z",
        code_commit_sha="f" * 40,
    )

    assert auth["source_runner_dry_hash"] == runtime.EXPECTED_DRY_HASH
    assert auth["source_preflight_artifact_hash"] == runtime.EXPECTED_PREFLIGHT_HASH
    assert auth["request_manifest_hash"] == runtime.EXPECTED_REQUEST_MANIFEST_HASH
    assert auth["provider_dispatch_attempts_max"] == 6
    assert auth["logical_provider_read_bundle_ids"] == list(runtime.BUNDLE_IDS)
    assert auth["msft_news_reread_allowed"] is False
    assert auth["meta_news_reread_allowed"] is False
    assert auth["positions_reread_allowed"] is False
    assert auth["model_calls_authorized"] is False
    assert auth["broker_writes_authorized"] is False
    assert auth["alpaca_orders_authorized"] is False


def test_journal_enforces_global_and_per_bundle_ceiling(tmp_path: Path):
    journal = _journal(tmp_path)
    for index in range(4):
        journal.dispatch(
            bundle_id="RR3_NVDA_NEWS_CONTINUATION",
            request_hash=f"{index:064x}"[-64:],
        )
    journal.dispatch(
        bundle_id="RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR",
        request_hash="1" * 64,
    )
    journal.dispatch(
        bundle_id="RR2_DYNAMIC_MARKET_CONTEXT",
        request_hash="2" * 64,
    )
    assert journal.attempt_count == 6
    with pytest.raises(runtime.CR4ToCR6RepairProductionError):
        journal.dispatch(
            bundle_id="RR3_NVDA_NEWS_CONTINUATION",
            request_hash="3" * 64,
        )


def test_nonzero_cli_snapshots_stderr_before_raise(monkeypatch, tmp_path: Path):
    journal = _journal(tmp_path)
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: "/usr/local/bin/alpaca")
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["alpaca"],
            returncode=1,
            stdout=b"",
            stderr=b"bad timeframe",
        ),
    )

    with pytest.raises(runtime.CR4ToCR6RepairProductionError, match="exit 1"):
        runtime._run_cli_json(
            bundle_id="RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR",
            command=["account", "portfolio", "--timeframe", "1D"],
            journal=journal,
        )

    rows = _rows(journal.path)
    assert [row["event_type"] for row in rows] == [
        "PROVIDER_DISPATCH_ATTEMPT",
        "PROVIDER_RAW_RESPONSE_SNAPSHOT",
    ]
    assert rows[1]["stream"] == "stderr"
    assert list(journal.raw_dir.iterdir())[0].read_bytes() == b"bad timeframe"


def test_invalid_json_is_snapshotted_before_parse_failure(monkeypatch, tmp_path: Path):
    journal = _journal(tmp_path)
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: "/usr/local/bin/alpaca")
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["alpaca"],
            returncode=0,
            stdout=b"{not-json",
            stderr=b"",
        ),
    )

    with pytest.raises(runtime.CR4ToCR6RepairProductionError, match="invalid JSON"):
        runtime._run_cli_json(
            bundle_id="RR2_DYNAMIC_MARKET_CONTEXT",
            command=["data", "multi-bars"],
            journal=journal,
        )

    rows = _rows(journal.path)
    assert [row["event_type"] for row in rows] == [
        "PROVIDER_DISPATCH_ATTEMPT",
        "PROVIDER_RAW_RESPONSE_SNAPSHOT",
        "PROVIDER_RESPONSE_RECEIPT",
    ]
    assert rows[1]["stream"] == "provider_response"
    assert list(journal.raw_dir.iterdir())[0].read_bytes() == b"{not-json"


def test_rr1_command_uses_repaired_1d(monkeypatch, tmp_path: Path):
    seen = []

    monkeypatch.setattr(
        runtime,
        "_template",
        lambda _p, _b: {
            "start_utc": "2026-08-24T08:58:17Z",
            "end_utc": "2026-08-31T08:58:17Z",
            "timeframe": "1D",
            "intraday_reporting": "market_hours",
        },
    )

    def fake_run(*, bundle_id, command, journal, timeout_seconds=45):
        seen.extend(command)
        return {"equity": [1]}, "a" * 64, "b" * 64

    monkeypatch.setattr(runtime, "_run_cli_json", fake_run)
    row = runtime._execute_rr1(preflight={}, journal=_journal(tmp_path))
    assert row["status"] == "PASS"
    assert "1D" in seen
    assert "1Day" not in seen


def test_execute_continues_only_frozen_independent_bundles_after_failure(monkeypatch, tmp_path: Path):
    calls = []

    monkeypatch.setattr(runtime, "verify_preflight", lambda _p: runtime.EXPECTED_PREFLIGHT_HASH)
    monkeypatch.setattr(runtime, "verify_source_dry", lambda _p: runtime.EXPECTED_DRY_HASH)

    class Retained:
        terminal_next_page_token = "TOKEN"

    monkeypatch.setattr(runtime, "verify_original_result", lambda _p: Retained())
    monkeypatch.setattr(
        runtime,
        "_template",
        lambda _p, bundle_id: {"starting_page_token": "TOKEN"}
        if bundle_id == "RR3_NVDA_NEWS_CONTINUATION"
        else {},
    )

    def rr1(**kwargs):
        calls.append("RR1")
        raise runtime.CR4ToCR6RepairProductionError("rr1 failed")

    def rr2(**kwargs):
        calls.append("RR2")
        return {"bundle_id": "RR2_DYNAMIC_MARKET_CONTEXT", "status": "PASS"}

    def rr3(**kwargs):
        calls.append("RR3")
        return {"bundle_id": "RR3_NVDA_NEWS_CONTINUATION", "status": "PARTIAL_PAGINATION_BOUND"}

    monkeypatch.setattr(runtime, "_execute_rr1", rr1)
    monkeypatch.setattr(runtime, "_execute_rr2", rr2)
    monkeypatch.setattr(runtime, "_execute_rr3", rr3)
    monkeypatch.setattr(
        runtime,
        "_self_hash",
        lambda _p, field_name="artifact_hash": "c" * 64,
    )

    authorization = {
        "source_runner_dry_hash": runtime.EXPECTED_DRY_HASH,
        "source_preflight_artifact_hash": runtime.EXPECTED_PREFLIGHT_HASH,
        "source_original_provider_result_hash": runtime.EXPECTED_SOURCE_ORIGINAL_RESULT_HASH,
        "request_manifest_hash": runtime.EXPECTED_REQUEST_MANIFEST_HASH,
        "provider_dispatch_attempts_max": 6,
        "model_calls_authorized": False,
        "broker_writes_authorized": False,
        "alpaca_orders_authorized": False,
    }

    result = runtime.execute_once(
        preflight={},
        dry={},
        original_result={},
        authorization=authorization,
        journal_path=tmp_path / "journal.jsonl",
        raw_dir=tmp_path / "raw",
    )

    assert calls == ["RR1", "RR2", "RR3"]
    assert result["status"] == runtime.BLOCKED_STATUS
    assert result["failed_bundle_ids"] == ["RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR"]
    assert result["next_gate"] == runtime.NEXT_GATE
    assert result["automatic_retries"] == 0
    assert result["model_calls"] == 0


def test_production_script_requires_exact_owner_gate_before_execution():
    text = Path(
        "scripts/b3_research_reopen_execute_cr4_to_cr6_repair_v01.py"
    ).read_text(encoding="utf-8")
    assert "--execute-provider-reads" in text
    assert "--approve-preflight-hash" in text
    assert "--approve-request-manifest-hash" in text
    assert "--approve-capability-probe-hash" in text
    assert "--approve-runner-dry-hash" in text
    assert "--approve-alpaca-binary-sha256" in text
    assert "--approve-max-dispatch-attempts" in text
    assert "--owner-approval-id" in text
    assert "authorization = build_authorization(" in text
    assert text.index("authorization = build_authorization(") < text.index("result = execute_once(")


def test_exact_runtime_has_no_broad_reread_or_order_surface():
    text = Path(
        "src/aic/research/reopen_judge_cr4_to_cr6_repair_production_v01.py"
    ).read_text(encoding="utf-8")
    assert "CR1_MSFT_NEWS_REFRESH" not in text
    assert "CR2_META_NEWS_REFRESH" not in text
    assert "CR3_CURRENT_PAPER_POSITIONS" not in text
    assert '"order", "submit"' not in text
    assert '"order", "cancel"' not in text
    assert '"position", "close"' not in text
    assert '"model_calls": 0' in text
    assert "MAX_DISPATCH_ATTEMPTS = dry_v01.MAX_DISPATCH_ATTEMPTS" in text
    assert "NEXT_GATE = \"B3_RESEARCH_REOPEN_CR4_TO_CR6_POST_READ_EVIDENCE_RECONCILIATION_ZERO_CALL\"" in text
