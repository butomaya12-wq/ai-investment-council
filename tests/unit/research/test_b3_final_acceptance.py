import json
from pathlib import Path

import pytest

from aic.research.acceptance import (
    B3AcceptanceError,
    EXPECTED_CHECK_IDS,
    MANIFEST_VERSION,
    load_and_validate_acceptance_manifest,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "config" / "event" / "b3_acceptance_manifest_v1.json"
RUNNER_PATH = REPO_ROOT / "scripts" / "b3_acceptance_gate.py"
ACCEPTANCE_PATH = REPO_ROOT / "src" / "aic" / "research" / "acceptance.py"


def test_b3_acceptance_manifest_has_exact_48_pass_checks_and_real_evidence_files() -> None:
    manifest = load_and_validate_acceptance_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)
    assert manifest["manifest_version"] == MANIFEST_VERSION
    assert manifest["required_check_count"] == 48
    assert tuple(item["check_id"] for item in manifest["checks"]) == EXPECTED_CHECK_IDS
    assert all(item["status"] == "PASS" for item in manifest["checks"])
    assert all(item["evidence_refs"] for item in manifest["checks"])


def test_b3_acceptance_manifest_fails_closed_on_missing_or_not_run_check(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["checks"][17]["status"] = "NOT_RUN"
    bad_status = tmp_path / "bad_status.json"
    bad_status.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(B3AcceptanceError, match="B3-V018 is not PASS"):
        load_and_validate_acceptance_manifest(bad_status, repo_root=REPO_ROOT)

    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["checks"].pop()
    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(B3AcceptanceError, match="exact ordered"):
        load_and_validate_acceptance_manifest(missing, repo_root=REPO_ROOT)


def test_b3_acceptance_manifest_rejects_invented_evidence_reference(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["checks"][0]["evidence_refs"] = ["EVAL:E99"]
    path = tmp_path / "invented.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(B3AcceptanceError, match="unknown frozen eval case"):
        load_and_validate_acceptance_manifest(path, repo_root=REPO_ROOT)


def test_b3_final_acceptance_gate_has_zero_paid_provider_or_broker_call_surface() -> None:
    runner = RUNNER_PATH.read_text(encoding="utf-8")
    acceptance = ACCEPTANCE_PATH.read_text(encoding="utf-8")
    combined = runner + acceptance
    assert "verify_b3_final_acceptance" in runner
    assert "load_openai_api_key" not in combined
    assert "StdlibResponsesTransport" not in combined
    assert "execute_retrieval_plan" not in combined
    assert "execute_synthesis_runtime" not in combined
    assert "submit_order" not in combined
    assert "alpaca data" not in combined
    assert "requests.get" not in combined
    assert "urllib.request" not in combined
