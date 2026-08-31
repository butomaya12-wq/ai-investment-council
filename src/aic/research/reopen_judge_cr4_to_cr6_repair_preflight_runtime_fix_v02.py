from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_cr4_to_cr6_repair_preflight_v01 as v01


RUNTIME_FIX_VERSION = "B3_RESEARCH_REOPEN_CR4_TO_CR6_REPAIR_PREFLIGHT_RUNTIME_FIX_v0_2"
UPSTREAM_VERSION_OUTPUT_RULE = "ALPACA_VERSION_COMMAND_PRINTS_VERSION_VALUE_ONLY"


class CR4ToCR6RepairPreflightRuntimeFixError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise CR4ToCR6RepairPreflightRuntimeFixError(message)


def probe_local_alpaca_cli() -> dict[str, Any]:
    executable = shutil.which("alpaca")
    _need(executable is not None, "Alpaca CLI executable is unavailable")
    resolved = Path(executable).resolve()
    _need(resolved.is_file(), "resolved Alpaca CLI executable is not a file")
    binary = resolved.read_bytes()
    _need(bool(binary), "Alpaca CLI executable is empty")

    version_raw = v01._run_local_probe(executable=str(resolved), args=("version",))
    portfolio_raw = v01._run_local_probe(
        executable=str(resolved), args=("account", "portfolio", "--help")
    )
    multi_bars_raw = v01._run_local_probe(
        executable=str(resolved), args=("data", "multi-bars", "--help")
    )
    news_raw = v01._run_local_probe(
        executable=str(resolved), args=("data", "news", "--help")
    )

    version_text = v01._decode_probe(version_raw, label="Alpaca version output")
    portfolio_text = v01._decode_probe(portfolio_raw, label="portfolio help")
    multi_bars_text = v01._decode_probe(multi_bars_raw, label="multi-bars help")
    news_text = v01._decode_probe(news_raw, label="news help")

    version_lines = [line.strip() for line in version_text.splitlines() if line.strip()]
    _need(len(version_lines) == 1, "Alpaca version output must be exactly one non-empty line")
    version_line = version_lines[0]
    _need(len(version_line) <= 200, "Alpaca version output is unexpectedly long")
    _need(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]*", version_line) is not None,
        "Alpaca version output has unexpected characters",
    )

    v01._validate_help(
        text=portfolio_text,
        required_flags=v01.PORTFOLIO_REQUIRED_FLAGS,
        required_fragments=("1D",),
        label="account portfolio help",
    )
    v01._validate_help(
        text=multi_bars_text,
        required_flags=v01.MULTI_BARS_REQUIRED_FLAGS,
        required_fragments=("multi-bars",),
        label="data multi-bars help",
    )
    v01._validate_help(
        text=news_text,
        required_flags=v01.NEWS_REQUIRED_FLAGS,
        required_fragments=("news",),
        label="data news help",
    )

    artifact = {
        "installed_cli_source_of_truth_rule": v01.UPSTREAM_CLI_SOURCE_OF_TRUTH_RULE,
        "upstream_cli_reference_commit": v01.UPSTREAM_CLI_REFERENCE_COMMIT,
        "alpaca_executable_path": str(resolved),
        "alpaca_binary_sha256": hashlib.sha256(binary).hexdigest(),
        "alpaca_binary_bytes": len(binary),
        "version_probe_command": ["alpaca", "version"],
        "version_output_sha256": hashlib.sha256(version_raw).hexdigest(),
        "version_output_bytes": len(version_raw),
        "version_output_first_line": version_line,
        "portfolio_help_probe_command": ["alpaca", "account", "portfolio", "--help"],
        "portfolio_help_sha256": hashlib.sha256(portfolio_raw).hexdigest(),
        "portfolio_help_bytes": len(portfolio_raw),
        "portfolio_required_flags": list(v01.PORTFOLIO_REQUIRED_FLAGS),
        "portfolio_timeframe_1d_confirmed": True,
        "multi_bars_help_probe_command": ["alpaca", "data", "multi-bars", "--help"],
        "multi_bars_help_sha256": hashlib.sha256(multi_bars_raw).hexdigest(),
        "multi_bars_help_bytes": len(multi_bars_raw),
        "multi_bars_required_flags": list(v01.MULTI_BARS_REQUIRED_FLAGS),
        "news_help_probe_command": ["alpaca", "data", "news", "--help"],
        "news_help_sha256": hashlib.sha256(news_raw).hexdigest(),
        "news_help_bytes": len(news_raw),
        "news_required_flags": list(v01.NEWS_REQUIRED_FLAGS),
        "credentials_removed_from_probe_environment": list(v01.CREDENTIAL_ENV_KEYS),
        "provider_reads_during_probe": 0,
        "model_calls_during_probe": 0,
    }
    artifact["capability_probe_hash"] = canonical_sha256(artifact)
    return artifact


def build_preflight(
    *,
    reconciliation: Mapping[str, Any],
    original_result: Mapping[str, Any],
    code_commit_sha: str,
    capability_probe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    probe = dict(capability_probe or probe_local_alpaca_cli())
    return v01.build_preflight(
        reconciliation=reconciliation,
        original_result=original_result,
        code_commit_sha=code_commit_sha,
        capability_probe=probe,
    )


def verify_preflight(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    return v01.verify_preflight(payload, expected_code_commit_sha=expected_code_commit_sha)


ARTIFACT_VERSION = v01.ARTIFACT_VERSION
PASS_STATUS = v01.PASS_STATUS
NEXT_GATE = v01.NEXT_GATE
EXPECTED_RECONCILIATION_HASH = v01.EXPECTED_RECONCILIATION_HASH
EXPECTED_ORIGINAL_RESULT_HASH = v01.EXPECTED_ORIGINAL_RESULT_HASH
EXPECTED_REOPEN_CUTOFF_UTC = v01.EXPECTED_REOPEN_CUTOFF_UTC
EXPECTED_NVDA_CONTINUATION_TOKEN = v01.EXPECTED_NVDA_CONTINUATION_TOKEN
BUNDLE_IDS = v01.BUNDLE_IDS
PROVIDER_DISPATCH_CEILING_BY_BUNDLE = v01.PROVIDER_DISPATCH_CEILING_BY_BUNDLE
PROVIDER_DISPATCH_ATTEMPTS_MAX = v01.PROVIDER_DISPATCH_ATTEMPTS_MAX
