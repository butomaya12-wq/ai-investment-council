from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aic.research import reopen_judge_cr4_to_cr6_repair_runner_dry_v01 as runtime


def test_build_dry_binds_exact_preflight_cli_and_ceiling(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "verify_preflight",
        lambda _payload: runtime.EXPECTED_PREFLIGHT_HASH,
    )
    monkeypatch.setattr(
        runtime,
        "verify_installed_alpaca_binary",
        lambda _payload: {
            "alpaca_executable_path": "/opt/homebrew/Cellar/cli/0.0.13/bin/alpaca",
            "alpaca_binary_sha256": runtime.EXPECTED_ALPACA_BINARY_SHA256,
            "alpaca_binary_bytes": runtime.EXPECTED_ALPACA_BINARY_BYTES,
            "alpaca_version_frozen": runtime.EXPECTED_ALPACA_VERSION,
            "binary_reverification_provider_reads": 0,
            "binary_reverification_model_calls": 0,
        },
    )

    dry = runtime.build_dry(preflight={}, code_commit_sha="f" * 40)

    assert dry["source_preflight_artifact_hash"] == runtime.EXPECTED_PREFLIGHT_HASH
    assert dry["request_manifest_hash"] == runtime.EXPECTED_REQUEST_MANIFEST_HASH
    assert dry["capability_probe_hash"] == runtime.EXPECTED_CAPABILITY_PROBE_HASH
    assert dry["alpaca_binary_sha256"] == runtime.EXPECTED_ALPACA_BINARY_SHA256
    assert dry["alpaca_version"] == "0.0.13"
    assert dry["provider_dispatch_attempts_max"] == 6
    assert dry["logical_provider_read_bundle_ids"] == list(runtime.BUNDLE_IDS)
    assert dry["msft_news_reread_allowed"] is False
    assert dry["meta_news_reread_allowed"] is False
    assert dry["positions_reread_allowed"] is False
    assert dry["provider_reads_authorized"] is False
    assert dry["model_calls_authorized"] is False
    assert dry["next_gate"] == runtime.NEXT_GATE
    assert runtime.verify_dry(dry, expected_code_commit_sha="f" * 40) == dry["artifact_hash"]


def test_installed_binary_reverification_is_local_and_exact(monkeypatch, tmp_path: Path):
    binary = tmp_path / "alpaca"
    raw = b"local-cli-binary"
    binary.write_bytes(raw)
    expected_sha = hashlib.sha256(raw).hexdigest()

    monkeypatch.setattr(runtime.shutil, "which", lambda _name: str(binary))
    monkeypatch.setattr(runtime, "EXPECTED_ALPACA_BINARY_SHA256", expected_sha)
    monkeypatch.setattr(runtime, "EXPECTED_ALPACA_BINARY_BYTES", len(raw))

    preflight = {
        "local_cli_capability_probe": {
            "alpaca_executable_path": str(binary),
        }
    }
    observed = runtime.verify_installed_alpaca_binary(preflight)
    assert observed["alpaca_binary_sha256"] == expected_sha
    assert observed["alpaca_binary_bytes"] == len(raw)
    assert observed["binary_reverification_provider_reads"] == 0
    assert observed["binary_reverification_model_calls"] == 0


def test_installed_binary_drift_blocks_dry(monkeypatch, tmp_path: Path):
    binary = tmp_path / "alpaca"
    binary.write_bytes(b"different")
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: str(binary))

    preflight = {
        "local_cli_capability_probe": {
            "alpaca_executable_path": str(binary),
        }
    }
    with pytest.raises(runtime.CR4ToCR6RepairRunnerDryError, match="binary size drift|binary SHA256 drift"):
        runtime.verify_installed_alpaca_binary(preflight)


def test_preflight_verifier_fails_closed_before_dry_on_invalid_payload():
    with pytest.raises(runtime.CR4ToCR6RepairRunnerDryError):
        runtime.verify_preflight({})


def test_runner_dry_script_has_no_provider_execution_surface():
    text = Path(
        "scripts/b3_research_reopen_cr4_to_cr6_repair_runner_dry_zero_call_v01.py"
    ).read_text(encoding="utf-8")
    assert "--execute-provider-reads" not in text
    assert "build_authorization" not in text
    assert "execute_once" not in text
    assert "PROVIDER_READS=0" in text
    assert "MODEL_CALLS=0" in text
    assert "production evidence unexpectedly exists before runner dry" in text


def test_runtime_has_no_order_or_live_execution_surface():
    text = Path(
        "src/aic/research/reopen_judge_cr4_to_cr6_repair_runner_dry_v01.py"
    ).read_text(encoding="utf-8")
    assert '"order", "submit"' not in text
    assert '"order", "cancel"' not in text
    assert '"position", "close"' not in text
    assert '"provider_reads_authorized": False' in text
