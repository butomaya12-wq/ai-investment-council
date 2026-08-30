from __future__ import annotations

from pathlib import Path
import py_compile


def test_b4_initial_production_runner_compiles() -> None:
    path = Path("scripts/b4_run_initial_runtime.py")
    assert path.is_file()
    py_compile.compile(str(path), doraise=True)
