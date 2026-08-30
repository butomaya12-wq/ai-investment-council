from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import json
from pathlib import Path
import sys

from aic.council.initial_schema_repair_v04 import (
    INITIAL_SCHEMA_REPAIR_VERSION,
    INITIAL_SCHEMA_VERSION,
    PROMOTION_SEMANTICS_CONTRACT_VERSION,
)
from aic.domain.canonical import canonical_sha256


def _load_legacy():
    path = Path(__file__).with_name("b4_initial_runtime_request_preflight.py")
    spec = importlib.util.spec_from_file_location(
        "_aic_b4_initial_runtime_request_preflight_v01", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load frozen B4 Initial runtime preflight")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


legacy = _load_legacy()


def main() -> int:
    source = json.loads(legacy.DEFAULT_SOURCE_PREFLIGHT.read_text(encoding="utf-8"))
    if source.get("initial_schema_repair_version") != INITIAL_SCHEMA_REPAIR_VERSION:
        raise ValueError("source preflight does not bind Initial v0.4 schema repair version")
    if source.get("initial_schema_version") != INITIAL_SCHEMA_VERSION:
        raise ValueError("source preflight does not bind repaired Initial v0.4 schema version")
    if (
        source.get("initial_promotion_semantics_contract_version")
        != PROMOTION_SEMANTICS_CONTRACT_VERSION
    ):
        raise ValueError("source preflight does not bind Initial promotion semantics contract")

    captured = StringIO()
    with redirect_stdout(captured):
        rc = legacy.main()
    if rc != 0:
        text = captured.getvalue()
        if text:
            print(text, end="")
        return rc

    path = legacy.DEFAULT_OUTPUT
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(artifact, dict):
        raise ValueError("runtime preflight artifact root must be object")
    artifact["initial_schema_repair_version"] = INITIAL_SCHEMA_REPAIR_VERSION
    artifact["initial_schema_version"] = INITIAL_SCHEMA_VERSION
    artifact["initial_promotion_semantics_contract_version"] = (
        PROMOTION_SEMANTICS_CONTRACT_VERSION
    )
    artifact.pop("artifact_hash", None)
    artifact["artifact_hash"] = canonical_sha256(artifact)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"B4 Initial runtime request preflight v0.4 failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2)
