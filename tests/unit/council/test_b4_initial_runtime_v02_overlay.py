from __future__ import annotations

import subprocess
import sys


def test_v02_paid_runner_overlay_binds_all_billing_gates() -> None:
    code = r'''
import importlib.util
from pathlib import Path
import sys

path = Path("scripts/b4_run_initial_runtime_v02.py")
spec = importlib.util.spec_from_file_location("_test_b4_initial_runtime_v02", path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.DEFAULT_COST_PREFLIGHT == Path(
    ".aic-runtime/b4_initial_runtime_cost_preflight_v0_2.json"
)
assert module.DEFAULT_OUTPUT == Path(
    ".aic-runtime/b4_initial_council_freeze_v0_2.json"
)
assert module.DEFAULT_AUTHORIZATION_OUTPUT == Path(
    ".aic-runtime/b4_initial_runtime_paid_authorization_v0_2.json"
)
assert module.DEFAULT_RECEIPT_JOURNAL == Path(
    ".aic-runtime/b4_initial_runtime_paid_receipts_v0_2.jsonl"
)

assert (
    module.authorization_runtime.verify_initial_runtime_cost_preflight
    is module.verify_initial_runtime_cost_preflight
)
assert (
    module.initial_runtime_module.verify_initial_runtime_cost_preflight
    is module.verify_initial_runtime_cost_preflight
)
assert (
    module.legacy.verify_initial_runtime_cost_preflight
    is module.verify_initial_runtime_cost_preflight
)
assert module.legacy.load_openai_text_pricing is module.load_initial_runtime_pricing
assert (
    module.legacy.process_initial_provider_response
    is module.process_initial_provider_response
)
assert module.legacy._build_receipt is module._build_receipt_v02
assert module.legacy._blocked_artifact is module._blocked_artifact_v02

assert (
    module.authorization_runtime.INITIAL_RUNTIME_PAID_AUTHORIZATION_VERSION
    == "B4_INITIAL_RUNTIME_PAID_AUTHORIZATION_ARTIFACT_v0_2"
)
assert (
    module.authorization_runtime.INITIAL_RUNTIME_PAID_RECEIPT_VERSION
    == "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_2"
)
assert (
    module.legacy.INITIAL_RUNTIME_PAID_RECEIPT_VERSION
    == "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_2"
)

pricing = module.load_initial_runtime_pricing()
assert pricing["pricing_version"] == (
    "OPENAI_TEXT_PRICING_2026_08_30_CACHE_WRITE_AWARE"
)
assert pricing["cache_write"]["input_rate_multiplier"] == "1.25"
assert pricing["cache_write"]["implicit_prompt_caching_default"] is True
print("V02_OVERLAY_BINDING_PASS")
'''
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "V02_OVERLAY_BINDING_PASS"
