"""Tests for the safe, offline submission demo replay."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / "demo" / "competition_watch_snapshot_v1.json"
SCRIPT_PATH = ROOT / "scripts" / "demo_competition_watch_v1.py"

EXPECTED_HASHES = {
    "b3_closure_hash": "ad1f477df9094e40c26186a045c6ca5794cc1cf4c415929bb1453cd09b1e4149",
    "initial_freeze_hash": "9138746e122b494e3a2eb84695b98870299145d5d806d2aa9da62ecb010cd394",
    "rebuttal_freeze_hash": "18b854261c9b49c1fcd2addfd66af52fda54e71b0949416f7dc1cdfed3e8fd9e",
    "judge_freeze_hash": "e3eac844b71bea54b22b5a4f14825f2bffd756cae16d3fd0cd5f66704d7bed49",
    "judge_proposal_hash": "aec665384f62604b3981e09c93dfbf3627119a0a019423ad5dca4ee75586ae0a",
}


def test_submission_snapshot_is_safe_derived_watch_evidence() -> None:
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert snapshot["snapshot_kind"] == "SUBMISSION_DEMO_DERIVED_SNAPSHOT"
    assert snapshot["canonical_trading_authority"] is False
    assert snapshot["candidate_ids"] == ["NVDA", "MSFT", "META"]
    assert snapshot["final_outcome"] == "WATCH"
    assert snapshot["next_directive"] == "MONITOR"
    assert snapshot["b5_handoff_eligible"] is False
    assert snapshot["broker_writes"] == 0
    assert snapshot["alpaca_orders"] == 0
    assert snapshot["automatic_retries"] == 0
    assert snapshot["live_money"] == "PROHIBITED"
    assert snapshot["known_actual_b4_cost_usd"] == "3.089588"
    assert snapshot["source_hashes"] == EXPECTED_HASHES
    assert snapshot["canonical_final_decision_promotion_status"] == (
        "BLOCKED_MISSING_DECISION_DRAFT_B4_v0_4.created_at"
    )


def test_demo_replay_uses_only_local_standard_library_imports() -> None:
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert imported <= {"__future__", "json", "pathlib"}
    assert not {"openai", "requests", "httpx", "urllib", "alpaca", "aic"} & imported


def test_demo_replay_exits_zero_and_reports_safe_watch_path() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "FINAL VERDICT\nWATCH" in result.stdout
    assert "B5 eligible: NO" in result.stdout
    assert "Broker writes: 0" in result.stdout
    assert "Alpaca orders: 0" in result.stdout
    assert "SYSTEM STOPPED SAFELY BEFORE EXECUTION" in result.stdout
    assert "FINAL VERDICT\nINVEST" not in result.stdout
