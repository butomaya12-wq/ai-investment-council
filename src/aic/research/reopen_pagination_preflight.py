from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.research.models import AlpacaNewsWindowParameters, ResearchNeedType
from aic.research.plan_freeze import FrozenPlannerBatch, load_frozen_planner_batch

from aic.data.providers.alpaca_news_reopen import MAX_REOPEN_NEWS_PAGES


PREFLIGHT_VERSION = "B3_REOPEN_PAGINATION_ZERO_CALL_PREFLIGHT_v0_1"
PREFLIGHT_STATUS = "B3_REOPEN_PAGINATION_ZERO_CALL_ENGINEERING_PASS"
EXPECTED_S00_STATUS = "B3_RESEARCH_REOPEN_S00_LINKED"
EXPECTED_REQUIRED_REF = "ALPACA_NEWS_PAGINATION_INCOMPLETE"
EXPECTED_NEXT_LIFECYCLE = "B3_RESEARCH_REOPEN_LINKED_S00"
EXPECTED_S00_NEXT_GATE = "B3_REOPEN_PAGINATION_ZERO_CALL_ENGINEERING"
REQUIRED_NEWS_FLAGS = (
    "--symbols",
    "--start",
    "--end",
    "--limit",
    "--include-content",
    "--exclude-contentless",
    "--page-token",
)


class ReopenPaginationPreflightError(ValueError):
    pass


def _canonical_utc(value) -> str:
    return value.isoformat().replace("+00:00", "Z")


def load_s00_artifact(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReopenPaginationPreflightError("unable to read B3 reopen S00 artifact") from exc
    if not isinstance(payload, dict):
        raise ReopenPaginationPreflightError("B3 reopen S00 artifact root must be an object")
    observed = payload.get("artifact_hash")
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    if observed != expected:
        raise ReopenPaginationPreflightError("B3 reopen S00 artifact self-hash mismatch")
    if payload.get("status") != EXPECTED_S00_STATUS:
        raise ReopenPaginationPreflightError("B3 reopen S00 status drift")
    if payload.get("required_source_ref_ids") != [EXPECTED_REQUIRED_REF]:
        raise ReopenPaginationPreflightError("B3 reopen S00 required source-ref drift")
    if payload.get("next_lifecycle") != EXPECTED_NEXT_LIFECYCLE:
        raise ReopenPaginationPreflightError("B3 reopen S00 lifecycle drift")
    if payload.get("next_gate") != EXPECTED_S00_NEXT_GATE:
        raise ReopenPaginationPreflightError("B3 reopen S00 next-gate drift")
    if payload.get("model_calls") != 0 or payload.get("provider_reads") != 0:
        raise ReopenPaginationPreflightError("B3 reopen S00 must remain zero-call")
    if payload.get("broker_writes") != 0 or payload.get("alpaca_orders") != 0:
        raise ReopenPaginationPreflightError("B3 reopen S00 side-effect drift")
    if payload.get("live_money") != "PROHIBITED":
        raise ReopenPaginationPreflightError("B3 reopen S00 live-money boundary drift")
    return payload


def build_candidate_news_windows(batch: FrozenPlannerBatch | Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_candidates: set[str] = set()
    for result in batch.results:
        plan = result.research_plan
        if plan.candidate_id in seen_candidates:
            raise ReopenPaginationPreflightError("duplicate candidate in frozen planner batch")
        seen_candidates.add(plan.candidate_id)
        news_needs = [
            need
            for need in plan.requested_needs
            if need.need_type is ResearchNeedType.NEED_ALPACA_NEWS_WINDOW
        ]
        if len(news_needs) != 1:
            raise ReopenPaginationPreflightError(
                f"frozen candidate {plan.candidate_id} must contain exactly one Alpaca news need"
            )
        need = news_needs[0]
        if not isinstance(need.parameters, AlpacaNewsWindowParameters):
            raise ReopenPaginationPreflightError("frozen Alpaca news need parameter type drift")
        if not 1 <= need.max_items <= 5:
            raise ReopenPaginationPreflightError("frozen Alpaca news page size outside 1..5")
        if need.parameters.window_end > plan.research_cutoff:
            raise ReopenPaginationPreflightError("frozen Alpaca news window exceeds research cutoff")
        rows.append(
            {
                "candidate_id": plan.candidate_id,
                "need_id": need.need_id,
                "window_start": _canonical_utc(need.parameters.window_start),
                "window_end": _canonical_utc(need.parameters.window_end),
                "research_cutoff": _canonical_utc(plan.research_cutoff),
                "page_size": need.max_items,
                "max_pages": MAX_REOPEN_NEWS_PAGES,
                "planned_provider_reads_max": MAX_REOPEN_NEWS_PAGES,
            }
        )
    if len(rows) != 3:
        raise ReopenPaginationPreflightError("reopen pagination requires exact frozen top three")
    return rows


def inspect_alpaca_news_help(
    *,
    executable: str = "alpaca",
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, Any]:
    executable_path = which(executable)
    if executable_path is None:
        raise ReopenPaginationPreflightError("Alpaca CLI executable is unavailable")
    try:
        completed = runner(
            [executable_path, "data", "news", "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReopenPaginationPreflightError("unable to inspect Alpaca CLI news help") from exc
    if completed.returncode != 0:
        raise ReopenPaginationPreflightError("Alpaca CLI news help returned non-zero status")
    raw = bytes(completed.stdout or b"") + b"\n" + bytes(completed.stderr or b"")
    if not raw.strip():
        raise ReopenPaginationPreflightError("Alpaca CLI news help returned empty output")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReopenPaginationPreflightError("Alpaca CLI news help is not UTF-8") from exc
    missing = [flag for flag in REQUIRED_NEWS_FLAGS if flag not in text]
    if missing:
        raise ReopenPaginationPreflightError(
            "Alpaca CLI news help is missing required flags: " + ", ".join(missing)
        )
    return {
        "alpaca_cli_path": executable_path,
        "alpaca_news_help_sha256": hashlib.sha256(raw).hexdigest(),
        "required_news_flags": list(REQUIRED_NEWS_FLAGS),
        "page_token_flag_present": True,
    }


def build_zero_call_preflight_artifact(
    *,
    code_commit_sha: str,
    s00_path: str | Path,
    planner_path: str | Path,
    executable: str = "alpaca",
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise ReopenPaginationPreflightError("code_commit_sha must be lowercase 40-char git SHA")
    s00 = load_s00_artifact(s00_path)
    planner = load_frozen_planner_batch(planner_path)
    windows = build_candidate_news_windows(planner)
    cli = inspect_alpaca_news_help(executable=executable, which=which, runner=runner)
    planned_reads_max = sum(int(row["planned_provider_reads_max"]) for row in windows)
    artifact: dict[str, Any] = {
        "artifact_version": PREFLIGHT_VERSION,
        "status": PREFLIGHT_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_s00_artifact_hash": s00["artifact_hash"],
        "source_production_judge_result_hash": s00["source_production_judge_result_hash"],
        "source_research_reopen_request_hash": s00["source_research_reopen_request_hash"],
        "required_source_ref_ids": [EXPECTED_REQUIRED_REF],
        "frozen_planner_artifact_hash": planner.artifact_hash,
        "candidate_news_windows": windows,
        "pagination_engineering_version": "B3_ALPACA_NEWS_REOPEN_PAGINATION_v0_1",
        "max_pages_per_candidate": MAX_REOPEN_NEWS_PAGES,
        "planned_provider_reads_max": planned_reads_max,
        "provider_reads_authorized": False,
        "next_gate": "B3_REOPEN_PAGINATED_PROVIDER_READ_OWNER_APPROVAL",
        **cli,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
