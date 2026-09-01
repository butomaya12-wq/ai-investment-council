"""Runtime lineage gate for the production B5 read-only entry."""

from __future__ import annotations

from pathlib import Path
import subprocess

from aic.b5.production_readonly_v1 import B5Entry, B5ProductionBlocked, create_b5_entry, load_recovered_b4_artifact


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repository, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise B5ProductionBlocked("B5 runtime Git identity check failed")
    return completed.stdout


def create_entry_at_clean_expected_head(
    *, repository: Path, recovered_b4_artifact: Path, expected_commit_sha: str
) -> B5Entry:
    """Bind B5 entry to the actual clean checkout, never a caller-supplied parent SHA."""
    if _git(repository, "status", "--porcelain=v1", "--untracked-files=no"):
        raise B5ProductionBlocked("B5 runtime requires a tracked-clean worktree")
    actual_commit_sha = _git(repository, "rev-parse", "HEAD").strip()
    if actual_commit_sha != expected_commit_sha:
        raise B5ProductionBlocked("B5 runtime HEAD does not match the expected feature commit")
    return create_b5_entry(
        load_recovered_b4_artifact(recovered_b4_artifact), b5_code_commit_sha=actual_commit_sha
    )
