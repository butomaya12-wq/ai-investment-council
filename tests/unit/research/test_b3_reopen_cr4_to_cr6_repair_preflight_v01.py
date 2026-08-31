from __future__ import annotations

from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_cr4_to_cr6_repair_preflight_v01 as preflight


def _probe() -> dict:
    payload = {
        "installed_cli_source_of_truth_rule": preflight.UPSTREAM_CLI_SOURCE_OF_TRUTH_RULE,
        "upstream_cli_reference_commit": preflight.UPSTREAM_CLI_REFERENCE_COMMIT,
        "alpaca_executable_path": "/usr/local/bin/alpaca",
        "alpaca_binary_sha256": "a" * 64,
        "alpaca_binary_bytes": 123,
        "version_probe_command": ["alpaca", "version"],
        "version_output_sha256": "b" * 64,
        "version_output_bytes": 12,
        "version_output_first_line": "alpaca test",
        "portfolio_help_probe_command": ["alpaca", "account", "portfolio", "--help"],
        "portfolio_help_sha256": "c" * 64,
        "portfolio_help_bytes": 100,
        "portfolio_required_flags": list(preflight.PORTFOLIO_REQUIRED_FLAGS),
        "portfolio_timeframe_1d_confirmed": True,
        "multi_bars_help_probe_command": ["alpaca", "data", "multi-bars", "--help"],
        "multi_bars_help_sha256": "d" * 64,
        "multi_bars_help_bytes": 100,
        "multi_bars_required_flags": list(preflight.MULTI_BARS_REQUIRED_FLAGS),
        "news_help_probe_command": ["alpaca", "data", "news", "--help"],
        "news_help_sha256": "e" * 64,
        "news_help_bytes": 100,
        "news_required_flags": list(preflight.NEWS_REQUIRED_FLAGS),
        "credentials_removed_from_probe_environment": list(preflight.CREDENTIAL_ENV_KEYS),
        "provider_reads_during_probe": 0,
        "model_calls_during_probe": 0,
    }
    payload["capability_probe_hash"] = canonical_sha256(payload)
    return payload


def test_build_preflight_binds_only_remaining_rr1_rr3_and_ceiling_six(monkeypatch):
    monkeypatch.setattr(
        preflight,
        "verify_failure_reconciliation",
        lambda _payload: preflight.EXPECTED_RECONCILIATION_HASH,
    )
    monkeypatch.setattr(
        preflight,
        "verify_original_result",
        lambda _payload: {
            "result_artifact_hash": preflight.EXPECTED_ORIGINAL_RESULT_HASH,
            "nvda_terminal_next_page_token": preflight.EXPECTED_NVDA_CONTINUATION_TOKEN,
            "nvda_retained_article_count": preflight.EXPECTED_NVDA_RETAINED_ARTICLE_COUNT,
        },
    )

    artifact = preflight.build_preflight(
        reconciliation={},
        original_result={},
        code_commit_sha="f" * 40,
        capability_probe=_probe(),
    )

    assert artifact["remaining_provider_read_bundle_ids"] == list(preflight.BUNDLE_IDS)
    assert artifact["provider_dispatch_attempts_max"] == 6
    assert artifact["provider_dispatch_ceiling_by_bundle"] == {
        "RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR": 1,
        "RR2_DYNAMIC_MARKET_CONTEXT": 1,
        "RR3_NVDA_NEWS_CONTINUATION": 4,
    }
    assert artifact["msft_news_reread_allowed"] is False
    assert artifact["meta_news_reread_allowed"] is False
    assert artifact["positions_reread_allowed"] is False
    assert artifact["frozen_current_equity_position_symbols"] == []
    assert artifact["provider_reads_authorized"] is False
    assert artifact["model_calls_authorized"] is False
    assert artifact["cost_usd"] == "0"

    rr1, rr2, rr3 = artifact["request_templates"]
    assert rr1["request_contract"]["timeframe"] == "1D"
    assert rr1["request_contract"]["start_utc"] == "2026-08-24T08:58:17Z"
    assert rr2["request_contract"]["symbols"] == ["MSFT", "META"]
    assert rr2["request_contract"]["start_utc"] == "2026-07-17T08:58:17Z"
    assert rr2["request_contract"]["timeframe"] == "1Hour"
    assert rr3["request_contract"]["starting_page_token"] == preflight.EXPECTED_NVDA_CONTINUATION_TOKEN
    assert rr3["request_contract"]["retained_pages_replay_allowed"] is False
    assert rr3["request_contract"]["max_additional_pages"] == 4
    assert artifact["all_remaining_bundles_independent_after_frozen_empty_positions"] is True
    assert artifact["automatic_retries"] == 0
    assert artifact["conditional_followup_reads_authorized"] is False


def test_help_validation_rejects_missing_required_flag():
    with pytest.raises(preflight.CR4ToCR6RepairPreflightError, match="--timeframe"):
        preflight._validate_help(
            text="Usage: alpaca account portfolio --start --end",
            required_flags=("--start", "--end", "--timeframe"),
            label="portfolio help",
        )


def test_local_cli_probe_binds_binary_and_all_three_help_surfaces(monkeypatch, tmp_path: Path):
    binary = tmp_path / "alpaca"
    binary.write_bytes(b"fake-alpaca-binary")
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: str(binary))

    outputs = {
        ("version",): b"alpaca version 1.2.3\n",
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

    monkeypatch.setattr(preflight, "_run_local_probe", fake_probe)
    probe = preflight.probe_local_alpaca_cli()

    assert probe["alpaca_binary_bytes"] == len(b"fake-alpaca-binary")
    assert probe["portfolio_timeframe_1d_confirmed"] is True
    assert probe["provider_reads_during_probe"] == 0
    assert probe["model_calls_during_probe"] == 0
    assert probe["capability_probe_hash"] == canonical_sha256(
        probe, exclude_fields=("capability_probe_hash",)
    )


def test_probe_environment_removes_credentials_and_live_flag(monkeypatch):
    for key in preflight.CREDENTIAL_ENV_KEYS:
        monkeypatch.setenv(key, "secret")
    env = preflight._sanitized_probe_env()
    for key in preflight.CREDENTIAL_ENV_KEYS:
        assert key not in env
    assert env["ALPACA_QUIET"] == "1"


def test_runner_is_zero_call_only_and_has_no_execution_switch():
    text = Path(
        "scripts/b3_research_reopen_cr4_to_cr6_repair_preflight_zero_call_v01.py"
    ).read_text(encoding="utf-8")
    assert "--execute-provider-reads" not in text
    assert '"order", "submit"' not in text
    assert "build_preflight(" in text
    assert "verify_preflight(" in text
