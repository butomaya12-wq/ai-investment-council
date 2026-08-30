from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import json
from pathlib import Path
import sys

from aic.council.initial_schema_repair_v03 import (
    INITIAL_SCHEMA_REPAIR_VERSION,
    INITIAL_SCHEMA_VERSION,
    build_bounded_initial_request_v03,
)
from aic.domain.canonical import canonical_sha256


def _load_legacy():
    path = Path(__file__).with_name("b4_initial_request_preflight.py")
    spec = importlib.util.spec_from_file_location("_aic_b4_initial_request_preflight_v01", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load frozen B4 Initial source preflight")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


legacy = _load_legacy()
legacy.build_bounded_initial_request = build_bounded_initial_request_v03


def main() -> int:
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
        raise ValueError("source preflight artifact root must be object")
    artifact["initial_schema_repair_version"] = INITIAL_SCHEMA_REPAIR_VERSION
    artifact["initial_schema_version"] = INITIAL_SCHEMA_VERSION
    artifact.pop("artifact_hash", None)
    artifact["artifact_hash"] = canonical_sha256(artifact)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
