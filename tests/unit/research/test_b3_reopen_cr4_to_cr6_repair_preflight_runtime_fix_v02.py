from __future__ import annotations

from pathlib import Path

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_cr4_to_cr6_repair_preflight_runtime_fix_v02 as fix
from aic.research import reopen_judge_cr4_to_cr6_repair_preflight_v01 as v01


def test_version_probe_accepts_real_upstream_shape_without_alpaca_word(monkeypatch, tmp_path: Path):
    binary = tmp_path / "alpaca"
    binary.write_bytes(b"fake-alpaca-binary")
    monkeypatch.setattr(fix.shutil, "which", lambda _name: str(binary))

    outputs = {
        ("version",): b"v0.3.1\n",
        ("account", "portfolio", "--help"): (
            b"alpaca account portfolio --start --end --timeframe 1D "
            b"--intraday-reporting --profile --quiet\n"
        ),
        ("data", "multi-bars", "--help"): (
            b"alpaca data multi-bars --symbols --start --end --timeframe "
            b"--feed --sort --limit --profile --quiet\n"
        ),
        ("data", "news", "--help"): (
            b"alpaca data news --symbols --start --end --sort --limit "
            b"--include-content --exclude-contentless --page-token --profile --quiet\n"
        ),
    }

    def fake_probe(*, executable: str, args, timeout_seconds: int = 10):
        assert executable == str(binary.resolve())
        assert timeout_seconds == 10
        return outputs[tuple(args)]

    monkeypatch.setattr(v01, "_run_local_probe", fake_probe)
    probe = fix.probe_local_alpaca_cli()

    assert probe["version_output_first_line"] == "v0.3.1"
    assert probe["portfolio_timeframe_1d_confirmed"] is True
    assert probe["provider_reads_during_probe"] == 0
    assert probe["model_calls_during_probe"] == 0
    assert probe["capability_probe_hash"] == canonical_sha256(
        probe, exclude_fields=("capability_probe_hash",)
    )


def test_runtime_fix_build_delegates_with_corrected_probe(monkeypatch):
    probe = {
        "capability_probe_hash": "a" * 64,
        "alpaca_binary_sha256": "b" * 64,
    }
    observed = {}

    def fake_build(**kwargs):
        observed.update(kwargs)
        return {"artifact_hash": "c" * 64}

    monkeypatch.setattr(v01, "build_preflight", fake_build)
    result = fix.build_preflight(
        reconciliation={"x": 1},
        original_result={"y": 2},
        code_commit_sha="f" * 40,
        capability_probe=probe,
    )

    assert result == {"artifact_hash": "c" * 64}
    assert observed["capability_probe"] == probe
    assert observed["code_commit_sha"] == "f" * 40


def test_v02_runner_has_no_provider_execution_switch():
    text = Path(
        "scripts/b3_research_reopen_cr4_to_cr6_repair_preflight_zero_call_v02.py"
    ).read_text(encoding="utf-8")
    assert "--execute-provider-reads" not in text
    assert '"order", "submit"' not in text
    assert "build_preflight(" in text
    assert "verify_preflight(" in text
