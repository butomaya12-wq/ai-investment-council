from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aic.research.acceptance import verify_b3_final_acceptance


DEFAULT_OUTPUT = Path(".aic-runtime/b3_final_acceptance.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the frozen B3-V001..V048 acceptance inventory and exact runtime artifacts. "
            "This gate performs no model call, provider read, broker write, or order."
        )
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = _args()
    try:
        artifact = verify_b3_final_acceptance(repo_root=Path("."))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(json.dumps({**artifact, "output_path": str(args.output)}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            f"B3 final acceptance gate failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
