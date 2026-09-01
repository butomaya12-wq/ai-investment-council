from __future__ import annotations

import ast
import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest

from aic.council import post_research_reopen_judge_current_v04 as judge


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "b4_post_research_reopen_judge_execute_production_v04.py"
)
SPEC = importlib.util.spec_from_file_location("b4_paid_judge_v04_runner", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

CODE = "a" * 40


def _write(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _artifacts(tmp_path: Path) -> Namespace:
    source = MODULE._reconstruct_source_inputs(CODE)
    gate = judge.build_gate(
        source_entry=source["source_entry"], source_context=source["source_context"]
    )
    entry = judge.build_entry(
        code_commit_sha=CODE,
        source_entry=source["source_entry"],
        source_context=source["source_context"],
        gate=gate,
    )
    context = judge.build_context(
        entry=entry,
        source_entry=source["source_entry"],
        source_context=source["source_context"],
        gate=gate,
    )
    inputs = {
        "entry": entry,
        "context": context,
        "source_entry": source["source_entry"],
        "source_context": source["source_context"],
        "gate": gate,
        "pricing": source["pricing"],
        "historical_request_hashes": source["historical_request_hashes"],
    }
    preflight = judge.build_preflight(code_commit_sha=CODE, **inputs)
    readiness = judge.build_readiness(
        code_commit_sha=CODE, preflight=preflight, **inputs
    )
    approval = judge.build_owner_approval(
        code_commit_sha=CODE,
        readiness_hash=readiness["artifact_hash"],
        preflight=preflight,
        entry=entry,
        gate=gate,
        owner_approval_id="TEST_OWNER_APPROVAL",
        owner_approval_at_utc="2026-09-01T00:00:00Z",
    )
    return Namespace(
        execute_paid_judge=True,
        gate=_write(tmp_path / "gate.json", gate),
        entry=_write(tmp_path / "entry.json", entry),
        preflight=_write(tmp_path / "preflight.json", preflight),
        readiness=_write(tmp_path / "readiness.json", readiness),
        owner_approval=_write(tmp_path / "approval.json", approval),
        ledger=tmp_path / "ledger.json",
        raw=tmp_path / "raw.json",
        result=tmp_path / "result.json",
    )


def _canonical_git(*args: str) -> str:
    values = {
        ("branch", "--show-current"): "hackathon/alpaca-2026",
        ("rev-parse", "HEAD"): CODE,
        ("status", "--porcelain=v1", "--untracked-files=no"): "",
    }
    return values[args]


def test_runner_is_v04_only_and_has_no_b5_b6_or_broker_authority() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    ]
    assert MODULE.judge is judge
    assert "post_research_reopen_judge_current_v04 as judge" in source
    assert "v03.execute_paid" not in source
    assert all("b5" not in module.lower() for module in imports)
    assert all("b6" not in module.lower() for module in imports)
    assert all("broker" not in module.lower() for module in imports)


def test_flag_and_every_artifact_path_are_explicit() -> None:
    with pytest.raises(SystemExit):
        MODULE.parse_args([])
    with pytest.raises(SystemExit):
        MODULE.parse_args(
            [
                "--execute-paid-judge",
                "--gate",
                "gate.json",
                "--entry",
                "entry.json",
                "--preflight",
                "preflight.json",
                "--readiness",
                "readiness.json",
                "--owner-approval",
                "approval.json",
                "--ledger",
                "ledger.json",
                "--raw",
                "raw.json",
            ]
        )


def test_missing_execute_flag_stops_before_preparation_or_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _artifacts(tmp_path)
    args.execute_paid_judge = False
    monkeypatch.setattr(MODULE, "_reconstruct_source_inputs", pytest.fail)
    with pytest.raises(SystemExit, match="--execute-paid-judge"):
        MODULE.run(args, transport_factory=pytest.fail)


@pytest.mark.parametrize("field", ["entry", "preflight", "readiness"])
def test_wrong_head_stops_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    args = _artifacts(tmp_path)
    value = json.loads(getattr(args, field).read_text(encoding="utf-8"))
    value["code_commit_sha"] = "b" * 40
    _write(getattr(args, field), value)
    monkeypatch.setattr(MODULE, "_git", _canonical_git)
    with pytest.raises(SystemExit, match="HEAD mismatch"):
        MODULE.run(args, transport_factory=pytest.fail)


def test_wrong_approval_and_request_hash_stop_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _artifacts(tmp_path)
    approval = json.loads(args.owner_approval.read_text(encoding="utf-8"))
    approval["request_hash"] = "0" * 64
    _write(args.owner_approval, approval)
    monkeypatch.setattr(MODULE, "_git", _canonical_git)
    with pytest.raises(Exception, match="artifact_hash mismatch"):
        MODULE.run(args, transport_factory=pytest.fail)


def test_tracked_dirty_blocks_but_untracked_runtime_evidence_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _artifacts(tmp_path)

    def dirty_git(*values: str) -> str:
        if values == ("status", "--porcelain=v1", "--untracked-files=no"):
            return " M tracked.py"
        return _canonical_git(*values)

    monkeypatch.setattr(MODULE, "_git", dirty_git)
    with pytest.raises(SystemExit, match="tracked worktree must be clean"):
        MODULE.run(args, transport_factory=pytest.fail)

    calls: list[tuple[str, ...]] = []

    def clean_git(*values: str) -> str:
        calls.append(values)
        return _canonical_git(*values)

    monkeypatch.setattr(MODULE, "_git", clean_git)
    monkeypatch.setattr(judge, "execute_paid", lambda **_: {"status": "FAKE"})
    assert MODULE.run(args, transport_factory=pytest.fail) == {"status": "FAKE"}
    assert ("status", "--porcelain=v1", "--untracked-files=no") in calls


@pytest.mark.parametrize("field", ["ledger", "raw", "result"])
def test_existing_paid_output_blocks_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    args = _artifacts(tmp_path)
    getattr(args, field).write_text("existing", encoding="utf-8")
    monkeypatch.setattr(MODULE, "_git", _canonical_git)
    with pytest.raises(SystemExit, match="exclusive paid output exists"):
        MODULE.run(args, transport_factory=pytest.fail)


def test_successful_fake_dispatch_uses_one_transport_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _artifacts(tmp_path)
    monkeypatch.setattr(MODULE, "_git", _canonical_git)
    calls = 0

    def sender(_: object) -> dict:
        nonlocal calls
        calls += 1
        return {"fake": "response"}

    def fake_execute_paid(**kwargs: object) -> dict:
        assert kwargs["transport_factory"]()({"request": "fake"}) == {"fake": "response"}
        return {"automatic_retries": 0, "broker_writes": 0, "alpaca_orders": 0}

    monkeypatch.setattr(judge, "execute_paid", fake_execute_paid)
    result = MODULE.run(args, transport_factory=lambda: sender)
    assert calls == 1
    assert result["automatic_retries"] == 0
    assert result["broker_writes"] == 0
    assert result["alpaca_orders"] == 0


def test_transport_exception_is_ambiguous_one_call_and_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _artifacts(tmp_path)
    monkeypatch.setattr(MODULE, "_git", _canonical_git)
    calls = 0

    def sender(_: object) -> dict:
        nonlocal calls
        calls += 1
        raise RuntimeError("simulated provider disconnect")

    with pytest.raises(judge.CurrentJudgeV04Error, match="ambiguous provider outcome"):
        MODULE.run(args, transport_factory=lambda: sender)

    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    row = ledger["entries"][0]
    assert calls == 1
    assert row["state"] == "DISPATCH_STARTED_UNKNOWN"
    assert row["automatic_retry_permitted"] is False
    assert row["stop_reason"] == "AMBIGUOUS_PROVIDER_OUTCOME:RuntimeError"
    assert not args.raw.exists()
    assert not args.result.exists()
