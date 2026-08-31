from __future__ import annotations

from pathlib import Path
import runpy

from aic.council import reopen_rebuttal_runtime as base
from aic.council import reopen_rebuttal_runtime_v02 as v02


base.RUNTIME_VERSION = v02.RUNTIME_VERSION
base.DRY_VERSION = v02.DRY_VERSION
base.AUTH_VERSION = v02.AUTH_VERSION
base.EVENT_VERSION = v02.EVENT_VERSION
base.RECEIPT_VERSION = v02.RECEIPT_VERSION
base.FREEZE_VERSION = v02.FREEZE_VERSION
base.BLOCKED_VERSION = v02.BLOCKED_VERSION
base.load_and_build_reopen_rebuttal_runtime_plan = (
    v02.load_and_build_reopen_rebuttal_runtime_plan
)

runpy.run_path(
    str(Path(__file__).with_name("b4_reopen_run_rebuttal_runtime_v01.py")),
    run_name="__main__",
)
