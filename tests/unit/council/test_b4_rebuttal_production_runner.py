from __future__ import annotations

from pathlib import Path


def test_production_runner_is_dry_by_default_and_loads_api_key_only_after_paid_gate() -> None:
    text = Path("scripts/b4_run_rebuttal_runtime_v01.py").read_text(encoding="utf-8")
    dry_gate = text.index("if not args.execute_paid_rebuttal")
    api_key_load = text.index("load_openai_api_key()")
    durable_auth = text.index("_write_durable_new(args.authorization_output, authorization)")
    assert dry_gate < durable_auth < api_key_load
    assert 'parser.add_argument("--execute-paid-rebuttal", action="store_true")' in text
    assert '"production_rebuttal_authorized": False' in text
    assert '"model_calls": 0' in text
    assert '"provider_reads": 0' in text


def test_production_runner_binds_every_owner_approval_authority() -> None:
    text = Path("scripts/b4_run_rebuttal_runtime_v01.py").read_text(encoding="utf-8")
    for flag in (
        "--approve-initial-freeze-hash",
        "--approve-selection-hash",
        "--approve-source-preflight-hash",
        "--approve-source-request-manifest-hash",
        "--approve-runtime-request-preflight-hash",
        "--approve-runtime-request-manifest-hash",
        "--approve-runtime-cost-artifact-hash",
        "--approve-runner-dry-artifact-hash",
        "--approve-max-usd",
        "--owner-approval-id",
        "--owner-approval-at-utc",
    ):
        assert flag in text
    assert "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT" in text
    assert '"scope_exhausts_after_dispatch_attempts": EXPECTED_PRODUCTION_CALLS' in text
    assert '"rerun_authorized": False' in text
    assert '"judge_authorized": False' in text


def test_production_runner_receipts_are_durable_and_no_repair_exists() -> None:
    text = Path("scripts/b4_run_rebuttal_runtime_v01.py").read_text(encoding="utf-8")
    assert 'with path.open("x", encoding="utf-8") as handle:' in text
    assert 'with path.open("a", encoding="utf-8") as handle:' in text
    assert text.count("os.fsync(handle.fileno())") >= 2
    assert '"automatic_repair_calls_authorized": False' in text
    assert '"automatic_repair_attempted": False' in text
    assert '"automatic_repair_calls": 0' in text
    assert "repair_transport" not in text
    assert "retry" not in text.lower()


def test_production_runner_stops_on_first_provider_cost_or_validation_failure() -> None:
    text = Path("scripts/b4_run_rebuttal_runtime_v01.py").read_text(encoding="utf-8")
    for status in (
        "BLOCKED_UNKNOWN_PROVIDER_DISPATCH",
        "BLOCKED_INCOMPLETE_COST_RECEIPT",
        "BLOCKED_APPROVED_COST_CEILING_EXCEEDED",
        "BLOCKED_REBUTTAL_VALIDATION_FAILED",
    ):
        assert status in text
    assert "if dispatch_attempts >= EXPECTED_PRODUCTION_CALLS" in text
    assert "production Rebuttal dispatch count is not exactly three" in text
    assert "production Rebuttal receipt count is not exactly three" in text
    assert "production Rebuttal validated record count is not exactly three" in text


def test_production_runner_has_no_broker_or_tool_surface() -> None:
    text = Path("scripts/b4_run_rebuttal_runtime_v01.py").read_text(encoding="utf-8")
    assert "StdlibResponsesTransport" in text
    assert '"hosted_tools": False' in text
    assert '"general_web_search": False' in text
    assert '"remote_mcp": False' in text
    assert '"broker_api": False' in text
    assert '"broker_writes": 0' in text
    assert '"alpaca_orders": 0' in text
    assert '"live_money": "PROHIBITED"' in text
