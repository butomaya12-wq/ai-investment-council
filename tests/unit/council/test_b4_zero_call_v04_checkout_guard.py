from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "b4_post_research_reopen_current_judge_zero_call_v04.py"
SPEC = importlib.util.spec_from_file_location("b4_zero_call_v04_script", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_tracked_status_explicitly_ignores_untracked_runtime_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(MODULE, "_git", fake_git)
    assert MODULE._tracked_worktree_status() == ""
    assert calls == [("status", "--porcelain=v1", "--untracked-files=no")]


def test_verified_detached_feature_head_is_allowed() -> None:
    head = "a" * 40
    MODULE._verify_checkout_guard(
        branch="",
        head=head,
        feature_remote_head=head,
        status="",
    )


def test_detached_head_mismatch_fails_closed() -> None:
    with pytest.raises(SystemExit, match="detached at exact feature remote HEAD"):
        MODULE._verify_checkout_guard(
            branch="",
            head="a" * 40,
            feature_remote_head="b" * 40,
            status="",
        )


def test_tracked_dirty_worktree_fails_even_on_verified_detached_head() -> None:
    head = "a" * 40
    with pytest.raises(SystemExit, match="tracked worktree must be clean"):
        MODULE._verify_checkout_guard(
            branch="",
            head=head,
            feature_remote_head=head,
            status=" M tracked.txt",
        )


def test_named_feature_branch_remains_allowed_when_tracked_clean() -> None:
    MODULE._verify_checkout_guard(
        branch="hackathon/b4-positive-invest-gate",
        head="a" * 40,
        feature_remote_head="b" * 40,
        status="",
    )
