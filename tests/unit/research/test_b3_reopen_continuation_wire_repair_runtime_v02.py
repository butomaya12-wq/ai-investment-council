from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aic.research import reopen_judge_continuation_wire_repair_runtime_v02 as runtime


class FakeNewsDelegate:
    def __init__(self, raw: bytes):
        self.raw = raw

    def get(self, *, endpoint, query, api_key_id, api_secret_key):
        return 200, self.raw


def _journal(tmp_path: Path) -> runtime.DurableWireRepairJournal:
    return runtime.DurableWireRepairJournal(
        path=tmp_path / "journal.jsonl",
        raw_dir=tmp_path / "raw",
        authorization_hash="a" * 64,
    )


def _journal_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_news_transport_persists_raw_snapshot_before_receipt(tmp_path: Path):
    raw = b'{"news":[],"next_page_token":""}'
    journal = _journal(tmp_path)
    transport = runtime.AuditedWireRepairNewsTransport(
        delegate=FakeNewsDelegate(raw),
        journal=journal,
        bundle_id="CR1_MSFT_NEWS_REFRESH",
    )

    status, observed = transport.get(
        endpoint="/v1beta1/news",
        query={"symbols": "MSFT"},
        api_key_id="profile",
        api_secret_key="profile",
    )

    assert status == 200
    assert observed == raw
    rows = _journal_rows(journal.path)
    assert [row["event_type"] for row in rows] == [
        "PROVIDER_DISPATCH_ATTEMPT",
        "PROVIDER_RAW_RESPONSE_SNAPSHOT",
        "PROVIDER_RESPONSE_RECEIPT",
    ]
    snapshot = rows[1]
    receipt = rows[2]
    assert snapshot["response_sha256"] == receipt["response_sha256"]
    assert snapshot["response_bytes"] == len(raw)
    raw_file = journal.raw_dir / snapshot["raw_snapshot_file"]
    assert raw_file.read_bytes() == raw
    assert journal.attempt_count == 1
    assert journal.raw_snapshot_count == 1


def test_invalid_cli_json_is_durably_retained_before_parse_failure(monkeypatch, tmp_path: Path):
    raw = b"{not-json"
    journal = _journal(tmp_path)

    monkeypatch.setattr(runtime.shutil, "which", lambda _name: "/usr/local/bin/alpaca")
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["alpaca"],
            returncode=0,
            stdout=raw,
            stderr=b"",
        ),
    )

    with pytest.raises(runtime.ContinuationWireRepairRuntimeError, match="invalid JSON"):
        runtime._run_cli_json(
            bundle_id="CR3_CURRENT_PAPER_POSITIONS",
            command=["position", "list"],
            journal=journal,
        )

    rows = _journal_rows(journal.path)
    assert [row["event_type"] for row in rows] == [
        "PROVIDER_DISPATCH_ATTEMPT",
        "PROVIDER_RAW_RESPONSE_SNAPSHOT",
        "PROVIDER_RESPONSE_RECEIPT",
    ]
    raw_files = list(journal.raw_dir.iterdir())
    assert len(raw_files) == 1
    assert raw_files[0].read_bytes() == raw


def test_multi_bars_empty_terminal_token_normalizes_to_null_without_mutating_input():
    source = {"bars": {"MSFT": []}, "next_page_token": ""}
    normalized = runtime._normalize_terminal_token_in_mapping(source)
    assert source["next_page_token"] == ""
    assert normalized["next_page_token"] is None


def test_nonempty_multi_bars_token_is_not_silently_normalized():
    source = {"bars": {}, "next_page_token": "more"}
    normalized = runtime._normalize_terminal_token_in_mapping(source)
    assert normalized["next_page_token"] == "more"


def test_build_dry_binds_consumed_failure_and_raw_persistence(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "verify_failure_reconciliation",
        lambda _payload: runtime.EXPECTED_FAILURE_RECONCILIATION_HASH,
    )
    monkeypatch.setattr(
        runtime,
        "verify_continuation_preflight",
        lambda _payload: runtime.EXPECTED_CONTINUATION_PREFLIGHT_HASH,
    )
    monkeypatch.setattr(
        runtime,
        "verify_original_preflight",
        lambda _payload: runtime.EXPECTED_ORIGINAL_PREFLIGHT_HASH,
    )
    monkeypatch.setattr(
        runtime,
        "verify_original_result",
        lambda _payload: {
            "result_artifact_hash": runtime.EXPECTED_ORIGINAL_RESULT_HASH,
            "retained_response_hash": runtime.EXPECTED_RETAINED_NVDA_EVIDENCE_HASH,
        },
    )

    dry = runtime.build_dry(
        failure_reconciliation={},
        continuation_preflight={},
        original_preflight={},
        original_result={},
        code_commit_sha="f" * 40,
    )

    assert dry["source_failure_reconciliation_hash"] == runtime.EXPECTED_FAILURE_RECONCILIATION_HASH
    assert dry["source_consumed_authorization_hash"] == runtime.EXPECTED_CONSUMED_AUTHORIZATION_HASH
    assert dry["source_consumed_result_hash"] == runtime.EXPECTED_CONSUMED_RESULT_HASH
    assert dry["msft_reread_from_frozen_window_start"] is True
    assert dry["wire_repair_rule"] == runtime.WIRE_REPAIR_RULE
    assert dry["raw_response_persistence_rule"] == runtime.RAW_PERSISTENCE_RULE
    assert dry["raw_response_snapshot_before_parse"] is True
    assert dry["raw_snapshot_event_before_response_receipt"] is True
    assert dry["provider_reads_authorized"] is False
    assert dry["model_calls_authorized"] is False
    assert dry["provider_dispatch_attempts_max"] == 11


def test_failure_reconciliation_verifier_rejects_wrong_hash_before_any_execution():
    with pytest.raises(runtime.ContinuationWireRepairRuntimeError, match="artifact_hash"):
        runtime.verify_failure_reconciliation({"artifact_hash": "0" * 64})


def test_runner_dry_branch_precedes_authorization_and_execution_and_binds_failure_hash():
    text = Path(
        "scripts/b3_research_reopen_execute_continuation_wire_repair_v02.py"
    ).read_text(encoding="utf-8")
    dry_branch = text.index("if not args.execute_provider_reads:")
    auth_build = text.index("authorization = build_authorization(")
    execute_call = text.index("result = execute_once(")
    assert dry_branch < auth_build < execute_call
    assert "--execute-provider-reads" in text
    assert "--approve-failure-reconciliation-hash" in text
    assert "--approve-continuation-preflight-hash" in text
    assert "--approve-continuation-request-manifest-hash" in text
    assert "--raw-response-dir" in text


def test_wire_repair_runtime_has_no_order_execution_surface():
    text = Path(
        "src/aic/research/reopen_judge_continuation_wire_repair_runtime_v02.py"
    ).read_text(encoding="utf-8")
    assert '"order", "submit"' not in text
    assert '"order", "cancel"' not in text
    assert '"position", "close"' not in text
    assert "ALPACA_LIVE_TRADE" in text
