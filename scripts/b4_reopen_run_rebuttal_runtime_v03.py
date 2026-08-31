from __future__ import annotations

from pathlib import Path
import runpy

from aic.council import reopen_rebuttal_runtime as base
from aic.council import reopen_rebuttal_runtime_v03 as v03


base.RUNTIME_VERSION = v03.RUNTIME_VERSION
base.DRY_VERSION = v03.DRY_VERSION
base.AUTH_VERSION = v03.AUTH_VERSION
base.EVENT_VERSION = v03.EVENT_VERSION
base.RECEIPT_VERSION = v03.RECEIPT_VERSION
base.FREEZE_VERSION = v03.FREEZE_VERSION
base.BLOCKED_VERSION = v03.BLOCKED_VERSION

base.load_and_build_reopen_rebuttal_runtime_plan = (
    v03.load_and_build_reopen_rebuttal_runtime_plan
)
base.build_dry_artifact = v03.build_dry_artifact
base.verify_dry_artifact = v03.verify_dry_artifact
base.build_paid_authorization = v03.build_paid_authorization
base.build_attempt_event = v03.build_attempt_event
base.build_result_receipt = v03.build_result_receipt
base.durable_finalize_inputs_from_journal = v03.durable_finalize_inputs_from_journal
base.build_freeze_artifact = v03.build_freeze_artifact
base.build_blocked_artifact = v03.build_blocked_artifact

runpy.run_path(
    str(Path(__file__).with_name("b4_reopen_run_rebuttal_runtime_v01.py")),
    run_name="__main__",
)
