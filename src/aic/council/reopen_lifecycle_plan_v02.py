from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from . import reopen_lifecycle_plan as v01
from .model_selection import InitialSelectedModelAuthority


ARTIFACT_VERSION = "B4_REOPEN_LIFECYCLE_PLAN_v0_2"
PASS_STATUS = v01.PASS_STATUS
NEXT_GATE = v01.NEXT_GATE
INITIAL_AUTHORITY_VALIDATION_CONTRACT = (
    "B4_INITIAL_SELECTED_MODEL_AUTHORITY_TYPED_CANONICAL_REPLAY_v0_1"
)


B4ReopenLifecyclePlanError = v01.B4ReopenLifecyclePlanError


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenLifecyclePlanError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise B4ReopenLifecyclePlanError(f"{label} root must be an object")
    return value


def normalize_initial_selected_model_authority(
    authority: Mapping[str, Any],
) -> dict[str, Any]:
    if authority.get("selection_hash") != v01.EXPECTED_INITIAL_SELECTION_HASH:
        raise B4ReopenLifecyclePlanError("Initial selected-model authority hash drift")
    try:
        typed = InitialSelectedModelAuthority.model_validate(dict(authority))
    except ValueError as exc:
        raise B4ReopenLifecyclePlanError(
            "Initial selected-model authority typed validation failed"
        ) from exc

    normalized = typed.model_dump(mode="python", exclude_none=False)
    if canonical_sha256(
        normalized,
        exclude_fields=("selection_hash",),
    ) != typed.selection_hash:
        raise B4ReopenLifecyclePlanError(
            "Initial selected-model authority typed canonical replay mismatch"
        )
    return normalized


def build_b4_reopen_lifecycle_plan_v02(
    *,
    code_commit_sha: str,
    overlay: Mapping[str, Any],
    initial_selected_model_authority: Mapping[str, Any],
) -> dict[str, Any]:
    normalized_initial = normalize_initial_selected_model_authority(
        initial_selected_model_authority
    )
    artifact = v01.build_b4_reopen_lifecycle_plan(
        code_commit_sha=code_commit_sha,
        overlay=overlay,
        initial_selected_model_authority=normalized_initial,
    )
    artifact = dict(artifact)
    artifact["artifact_version"] = ARTIFACT_VERSION
    artifact["initial_selected_model_authority_validation_contract"] = (
        INITIAL_AUTHORITY_VALIDATION_CONTRACT
    )
    artifact["source_initial_selected_model_selection_hash"] = (
        v01.EXPECTED_INITIAL_SELECTION_HASH
    )
    artifact["historical_v0_1_failure_class"] = (
        "RAW_JSON_DECIMAL_STRING_CANONICALIZATION_MISMATCH"
    )
    artifact["artifact_hash"] = canonical_sha256(
        artifact,
        exclude_fields=("artifact_hash",),
    )
    return artifact


def load_and_build_b4_reopen_lifecycle_plan_v02(
    *,
    code_commit_sha: str,
    overlay_path: str | Path,
    initial_selected_model_authority_path: str | Path,
) -> dict[str, Any]:
    return build_b4_reopen_lifecycle_plan_v02(
        code_commit_sha=code_commit_sha,
        overlay=_read_object(overlay_path, label="B4 reopen input overlay"),
        initial_selected_model_authority=_read_object(
            initial_selected_model_authority_path,
            label="Initial selected-model authority",
        ),
    )
