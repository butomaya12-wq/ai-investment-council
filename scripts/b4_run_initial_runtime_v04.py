from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from aic.council import initial_runtime as initial_runtime_module
from aic.council import initial_runtime_authorization as authorization_runtime
from aic.council.initial_schema_repair_v04 import (
    INITIAL_SCHEMA_REPAIR_VERSION,
    INITIAL_SCHEMA_VERSION,
    PROMOTION_SEMANTICS_CONTRACT_VERSION,
    build_bounded_initial_request_v04,
)


PAID_AUTHORIZATION_VERSION = "B4_INITIAL_RUNTIME_PAID_AUTHORIZATION_ARTIFACT_v0_4"
PAID_RECEIPT_VERSION = "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_4"
DEFAULT_OUTPUT = Path(".aic-runtime/b4_initial_council_freeze_v0_4.json")
DEFAULT_AUTHORIZATION_OUTPUT = Path(
    ".aic-runtime/b4_initial_runtime_paid_authorization_v0_4.json"
)
DEFAULT_RECEIPT_JOURNAL = Path(
    ".aic-runtime/b4_initial_runtime_paid_receipts_v0_4.jsonl"
)
DEFAULT_RUNTIME_PREFLIGHT = Path(
    ".aic-runtime/b4_initial_runtime_request_preflight_v0_1.json"
)


def _load_v02_runner():
    path = Path(__file__).with_name("b4_run_initial_runtime_v02.py")
    spec = importlib.util.spec_from_file_location("_aic_b4_run_initial_runtime_v02", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load frozen B4 Initial production runner v0.2")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v02 = _load_v02_runner()


def _arg_path(flag: str, default: Path) -> Path:
    if flag not in sys.argv:
        return default
    index = sys.argv.index(flag)
    if index + 1 >= len(sys.argv):
        raise ValueError(f"missing value after {flag}")
    return Path(sys.argv[index + 1])


def _patch_runner() -> None:
    initial_runtime_module.build_bounded_initial_request = build_bounded_initial_request_v04

    authorization_runtime.INITIAL_RUNTIME_PAID_AUTHORIZATION_VERSION = (
        PAID_AUTHORIZATION_VERSION
    )
    authorization_runtime.INITIAL_RUNTIME_PAID_RECEIPT_VERSION = PAID_RECEIPT_VERSION

    v02.PAID_AUTHORIZATION_VERSION = PAID_AUTHORIZATION_VERSION
    v02.PAID_RECEIPT_VERSION = PAID_RECEIPT_VERSION
    v02.legacy.INITIAL_RUNTIME_PAID_RECEIPT_VERSION = PAID_RECEIPT_VERSION
    v02.legacy.DEFAULT_OUTPUT = DEFAULT_OUTPUT
    v02.legacy.DEFAULT_AUTHORIZATION_OUTPUT = DEFAULT_AUTHORIZATION_OUTPUT
    v02.legacy.DEFAULT_RECEIPT_JOURNAL = DEFAULT_RECEIPT_JOURNAL


_patch_runner()


def _verify_schema_repair_binding() -> None:
    path = _arg_path("--runtime-preflight", DEFAULT_RUNTIME_PREFLIGHT)
    runtime = json.loads(path.read_text(encoding="utf-8"))
    if runtime.get("initial_schema_repair_version") != INITIAL_SCHEMA_REPAIR_VERSION:
        raise ValueError("runtime preflight does not bind Initial v0.4 schema repair version")
    if runtime.get("initial_schema_version") != INITIAL_SCHEMA_VERSION:
        raise ValueError("runtime preflight does not bind repaired Initial v0.4 schema version")
    if (
        runtime.get("initial_promotion_semantics_contract_version")
        != PROMOTION_SEMANTICS_CONTRACT_VERSION
    ):
        raise ValueError("runtime preflight does not bind Initial promotion semantics contract")


def main() -> int:
    try:
        _verify_schema_repair_binding()
    except Exception as exc:
        print(
            f"B4 Initial runtime v0.4 failed closed before dispatch: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    return v02.main()


if __name__ == "__main__":
    raise SystemExit(main())
