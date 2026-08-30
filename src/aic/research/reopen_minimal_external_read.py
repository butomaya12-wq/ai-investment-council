from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.research.reopen_minimal_external_preflight import inspect_alpaca_cli_help


AUTHORITY_VERSION = "B3_REOPEN_MINIMAL_EXTERNAL_READ_AUTHORITY_v0_1"
AUTHORIZATION_ARTIFACT_VERSION = "B3_REOPEN_MINIMAL_EXTERNAL_READ_AUTHORIZATION_v0_1"
RESULT_ARTIFACT_VERSION = "B3_REOPEN_MINIMAL_EXTERNAL_READ_RESULT_v0_1"
RECEIPT_EVENT_VERSION = "B3_REOPEN_MINIMAL_EXTERNAL_READ_RECEIPT_EVENT_v0_1"

EXPECTED_PREFLIGHT_HASH = "a37ed5891f760c5959177c515d64e078b392b45e9dd70f1f1870e79d7b601067"
EXPECTED_PREFLIGHT_STATUS = "B3_REOPEN_MINIMAL_EXTERNAL_READ_PREFLIGHT_ZERO_CALL_PASS"
EXPECTED_PREFLIGHT_CODE_SHA = "ecfadb396da73de2578baa825e916c13f2b98a5a"
EXPECTED_OWNER_APPROVAL_ID = "OWNER-B3-REOPEN-MINIMAL-EXTERNAL-READ-V01"
EXPECTED_AUTH_MODE = "CLI_PROFILE:paper"
EXPECTED_READ_IDS = (
    "R1_CURRENT_POSITIONS_ANCHOR",
    "R2_POST_CUTOFF_ACCOUNT_ACTIVITIES_FIRST_PAGE",
    "R3_B2_CUTOFF_PORTFOLIO_EQUITY",
    "R4_MSFT_META_POINT_IN_TIME_BARS",
)
EXPECTED_SOURCE_LOCAL_PRIMITIVES_HASH = "64c76249a36d650c79e95c80720061f3cbe48be900c6d1cdab2fda44240a5ee7"
EXPECTED_SOURCE_EVIDENCE_PLAN_HASH = "13c6e5da3e5d2b9b2369a8998abb9285d20e91a7c86452539a623301805e4b61"
EXPECTED_SOURCE_SCOPE_HASH = "948d3dbd28200d94726e97e39abd7955a0aa428ece22ee7b1ad6bbec6d20ba4a"

PLANNED_PROVIDER_READS_MAX = 4
ACTIVITY_PAGE_SIZE = 100
B2_CUTOFF = "2026-08-27T20:00:00Z"
PORTFOLIO_START = "2026-08-27T19:55:00Z"
PORTFOLIO_END = B2_CUTOFF
MARKET_START = "2026-08-27T19:55:00Z"
MARKET_END = "2026-08-28T17:34:00Z"

SUCCESS_STATUS = "B3_REOPEN_MINIMAL_EXTERNAL_READ_CAPTURE_COMPLETE"
PARTIAL_STATUS = "B3_REOPEN_MINIMAL_EXTERNAL_READ_CAPTURE_PARTIAL"
BLOCKED_STATUS = "B3_REOPEN_MINIMAL_EXTERNAL_READ_CAPTURE_BLOCKED"


class MinimalExternalReadError(RuntimeError):
    pass


def _utc_now(now: Callable[[], datetime]) -> str:
    value = now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise MinimalExternalReadError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _read_json_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MinimalExternalReadError(f"unable to read {label}") from exc
    if not isinstance(payload, dict):
        raise MinimalExternalReadError(f"{label} root must be an object")
    return payload


def _verify_self_hash(payload: Mapping[str, Any], *, label: str) -> str:
    observed = payload.get("artifact_hash")
    if not isinstance(observed, str) or len(observed) != 64:
        raise MinimalExternalReadError(f"{label} artifact_hash missing")
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    if observed != expected:
        raise MinimalExternalReadError(f"{label} self-hash mismatch")
    return observed


def validate_preflight_payload(
    payload: Mapping[str, Any],
    *,
    expected_hash: str = EXPECTED_PREFLIGHT_HASH,
) -> dict[str, Any]:
    preflight = dict(payload)
    observed_hash = _verify_self_hash(preflight, label="minimal external-read preflight")
    if observed_hash != expected_hash:
        raise MinimalExternalReadError("minimal external-read preflight hash is not approved")
    if preflight.get("status") != EXPECTED_PREFLIGHT_STATUS:
        raise MinimalExternalReadError("minimal external-read preflight status drift")
    if preflight.get("code_commit_sha") != EXPECTED_PREFLIGHT_CODE_SHA:
        raise MinimalExternalReadError("minimal external-read preflight code SHA drift")
    if preflight.get("source_local_primitives_hash") != EXPECTED_SOURCE_LOCAL_PRIMITIVES_HASH:
        raise MinimalExternalReadError("local primitives lineage drift")
    if preflight.get("source_evidence_plan_hash") != EXPECTED_SOURCE_EVIDENCE_PLAN_HASH:
        raise MinimalExternalReadError("evidence-plan lineage drift")
    if preflight.get("source_remaining_gaps_scope_hash") != EXPECTED_SOURCE_SCOPE_HASH:
        raise MinimalExternalReadError("remaining-gap scope lineage drift")
    if preflight.get("target_candidates") != ["MSFT", "META"]:
        raise MinimalExternalReadError("target candidate scope drift")
    if preflight.get("non_target_candidate_ids") != ["NVDA"]:
        raise MinimalExternalReadError("non-target candidate scope drift")
    if preflight.get("planned_provider_reads_max") != PLANNED_PROVIDER_READS_MAX:
        raise MinimalExternalReadError("provider-read ceiling drift")
    if preflight.get("provider_reads_authorized") is not False:
        raise MinimalExternalReadError("preflight unexpectedly authorizes provider reads")
    if preflight.get("provider_reads") != 0 or preflight.get("model_calls") != 0:
        raise MinimalExternalReadError("preflight is not zero-call")
    if preflight.get("broker_writes") != 0 or preflight.get("alpaca_orders") != 0:
        raise MinimalExternalReadError("preflight side-effect boundary drift")
    if preflight.get("live_money") != "PROHIBITED":
        raise MinimalExternalReadError("preflight live-money boundary drift")
    if preflight.get("automatic_retries") != 0 or preflight.get("rerun_authorized") is not False:
        raise MinimalExternalReadError("preflight retry/rerun boundary drift")
    if preflight.get("authorization_consumption_rule") != "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT":
        raise MinimalExternalReadError("preflight consumption-rule drift")
    if preflight.get("owner_approval_required") is not True:
        raise MinimalExternalReadError("preflight owner-approval requirement drift")
    if preflight.get("next_gate") != "B3_REOPEN_MINIMAL_EXTERNAL_READ_OWNER_APPROVAL":
        raise MinimalExternalReadError("preflight next-gate drift")

    read_plan = preflight.get("provider_read_plan")
    if not isinstance(read_plan, list) or len(read_plan) != PLANNED_PROVIDER_READS_MAX:
        raise MinimalExternalReadError("provider read plan missing or wrong length")
    read_ids = tuple(
        row.get("read_id") if isinstance(row, Mapping) else None
        for row in read_plan
    )
    if read_ids != EXPECTED_READ_IDS:
        raise MinimalExternalReadError("provider read-plan identity/order drift")
    if any(
        not isinstance(row, Mapping) or row.get("max_dispatch_attempts") != 1
        for row in read_plan
    ):
        raise MinimalExternalReadError("provider read-plan dispatch bound drift")
    activity = read_plan[1]
    if (
        activity.get("page_size") != ACTIVITY_PAGE_SIZE
        or activity.get("max_pages") != 1
        or activity.get("pagination_continuation_authorized") is not False
        or activity.get("after_exclusive") != B2_CUTOFF
    ):
        raise MinimalExternalReadError("account-activity bound drift")
    market = read_plan[3]
    if (
        market.get("symbols") != ["MSFT", "META"]
        or market.get("max_pages") != 1
        or market.get("pagination_continuation_authorized") is not False
        or market.get("start") != MARKET_START
        or market.get("end") != MARKET_END
        or market.get("feed") != "iex"
        or market.get("timeframe") != "1Min"
        or market.get("limit") != 1000
    ):
        raise MinimalExternalReadError("market-data bound drift")
    return preflight


def load_approved_preflight(path: str | Path) -> dict[str, Any]:
    return validate_preflight_payload(_read_json_object(path, label="minimal external-read preflight"))


def verify_cli_help_still_bound(
    preflight: Mapping[str, Any],
    *,
    inspector: Callable[..., dict[str, Any]] = inspect_alpaca_cli_help,
) -> dict[str, Any]:
    observed = inspector()
    expected_checks = preflight.get("cli_help_checks")
    observed_checks = observed.get("cli_help_checks")
    if not isinstance(expected_checks, Mapping) or not isinstance(observed_checks, Mapping):
        raise MinimalExternalReadError("CLI help checks missing")
    if set(expected_checks) != set(observed_checks):
        raise MinimalExternalReadError("CLI help command surface drift")
    for name, expected in expected_checks.items():
        current = observed_checks.get(name)
        if not isinstance(expected, Mapping) or not isinstance(current, Mapping):
            raise MinimalExternalReadError(f"CLI help check malformed for {name}")
        if current.get("help_sha256") != expected.get("help_sha256"):
            raise MinimalExternalReadError(f"Alpaca CLI help changed for {name}")
        if current.get("required_flags") != expected.get("required_flags"):
            raise MinimalExternalReadError(f"Alpaca CLI required flags changed for {name}")
        if current.get("command") != expected.get("command"):
            raise MinimalExternalReadError(f"Alpaca CLI command changed for {name}")
    executable = observed.get("alpaca_cli_path")
    if not isinstance(executable, str) or not executable:
        raise MinimalExternalReadError("Alpaca CLI path unavailable")
    return observed


def build_authorization_artifact(
    *,
    code_commit_sha: str,
    preflight: Mapping[str, Any],
    owner_approval_id: str,
    approved_preflight_hash: str,
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise MinimalExternalReadError("code_commit_sha must be lowercase 40-char SHA")
    preflight_hash = str(preflight.get("artifact_hash") or "")
    if preflight_hash != EXPECTED_PREFLIGHT_HASH:
        raise MinimalExternalReadError("preflight hash drift before authorization")
    if approved_preflight_hash != preflight_hash:
        raise MinimalExternalReadError("owner-approved preflight hash mismatch")
    if owner_approval_id != EXPECTED_OWNER_APPROVAL_ID:
        raise MinimalExternalReadError("owner approval id mismatch")
    artifact: dict[str, Any] = {
        "artifact_version": AUTHORIZATION_ARTIFACT_VERSION,
        "authority_version": AUTHORITY_VERSION,
        "code_commit_sha": code_commit_sha,
        "owner_approval_id": owner_approval_id,
        "approved_preflight_hash": preflight_hash,
        "approved_auth_mode": EXPECTED_AUTH_MODE,
        "approved_read_ids": list(EXPECTED_READ_IDS),
        "approved_provider_dispatch_attempts_max": PLANNED_PROVIDER_READS_MAX,
        "activity_page_size": ACTIVITY_PAGE_SIZE,
        "activity_max_pages": 1,
        "activity_pagination_continuation_authorized": False,
        "market_max_pages": 1,
        "market_pagination_continuation_authorized": False,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_retries": 0,
        "rerun_authorized": False,
        "model_calls_authorized": 0,
        "broker_writes_authorized": 0,
        "alpaca_orders_authorized": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def _write_json_exclusive_fsync(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def write_authorization_artifact_exclusive(path: str | Path, payload: Mapping[str, Any]) -> None:
    _write_json_exclusive_fsync(Path(path), payload)


def _write_bytes_exclusive_fsync(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _append_jsonl_fsync(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with path.open("ab") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _with_receipt_hash(payload: Mapping[str, Any]) -> dict[str, Any]:
    event = dict(payload)
    event["receipt_hash"] = canonical_sha256(event)
    return event


@dataclass
class ProviderDispatchTracker:
    authority_hash: str
    preflight_hash: str
    receipt_path: Path
    max_dispatch_attempts: int = PLANNED_PROVIDER_READS_MAX
    dispatch_attempts: int = 0
    provider_reads: int = 0
    receipt_hashes: list[str] = field(default_factory=list)

    def _append(self, payload: Mapping[str, Any]) -> str:
        event = _with_receipt_hash(payload)
        _append_jsonl_fsync(self.receipt_path, event)
        receipt_hash = str(event["receipt_hash"])
        self.receipt_hashes.append(receipt_hash)
        return receipt_hash

    def begin(self, *, read_id: str, command: Sequence[str], attempted_at_utc: str) -> int:
        if self.dispatch_attempts >= self.max_dispatch_attempts:
            raise MinimalExternalReadError("approved provider dispatch ceiling exhausted")
        if read_id not in EXPECTED_READ_IDS:
            raise MinimalExternalReadError("unapproved read id")
        self.dispatch_attempts += 1
        self._append(
            {
                "receipt_event_version": RECEIPT_EVENT_VERSION,
                "event": "PROVIDER_DISPATCH_ATTEMPT",
                "authority_hash": self.authority_hash,
                "preflight_hash": self.preflight_hash,
                "global_dispatch_attempt": self.dispatch_attempts,
                "read_id": read_id,
                "command_hash": canonical_sha256({"argv": list(command)}),
                "attempted_at_utc": attempted_at_utc,
            }
        )
        return self.dispatch_attempts

    def received(
        self,
        *,
        read_id: str,
        stdout: bytes,
        stderr: bytes,
        received_at_utc: str,
    ) -> None:
        self.provider_reads += 1
        self._append(
            {
                "receipt_event_version": RECEIPT_EVENT_VERSION,
                "event": "PROVIDER_RESPONSE_RECEIVED",
                "authority_hash": self.authority_hash,
                "preflight_hash": self.preflight_hash,
                "global_dispatch_attempt": self.dispatch_attempts,
                "read_id": read_id,
                "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                "stdout_bytes": len(stdout),
                "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                "stderr_bytes": len(stderr),
                "received_at_utc": received_at_utc,
            }
        )

    def failed(self, *, read_id: str, error_class: str, failed_at_utc: str) -> None:
        self._append(
            {
                "receipt_event_version": RECEIPT_EVENT_VERSION,
                "event": "PROVIDER_DISPATCH_FAILED",
                "authority_hash": self.authority_hash,
                "preflight_hash": self.preflight_hash,
                "global_dispatch_attempt": self.dispatch_attempts,
                "read_id": read_id,
                "error_class": error_class,
                "failed_at_utc": failed_at_utc,
            }
        )


def _runtime_env() -> dict[str, str]:
    env = dict(os.environ)
    env["ALPACA_QUIET"] = "1"
    env.pop("ALPACA_LIVE_TRADE", None)
    env.pop("OPENAI_API_KEY", None)
    return env


def _decode_json(raw: bytes, *, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
        return json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MinimalExternalReadError(f"{label} is not valid UTF-8 JSON") from exc


def inspect_activity_page(raw: bytes) -> dict[str, Any]:
    payload = _decode_json(raw, label="account activity response")
    rows: Any
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        rows = payload.get("activities")
        if rows is None:
            rows = payload.get("data")
    else:
        rows = None
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise MinimalExternalReadError("account activity response shape unsupported")
    if len(rows) > ACTIVITY_PAGE_SIZE:
        raise MinimalExternalReadError("account activity response exceeds approved page size")

    unsupported: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        activity_type_raw = row.get("activity_type", row.get("type"))
        activity_type = (
            activity_type_raw.strip().upper()
            if isinstance(activity_type_raw, str) and activity_type_raw.strip()
            else None
        )
        symbol_present = isinstance(row.get("symbol"), str) and bool(str(row.get("symbol")).strip())
        qty_value = row.get("qty", row.get("quantity"))
        qty_present = qty_value is not None and str(qty_value).strip() not in ("", "0", "0.0")
        if activity_type != "FILL" and (symbol_present or qty_present):
            unsupported.append(
                {
                    "row_index": index,
                    "activity_type": activity_type,
                    "symbol_present": symbol_present,
                    "qty_present": qty_present,
                }
            )
    return {
        "record_count": len(rows),
        "page_bound_reached": len(rows) == ACTIVITY_PAGE_SIZE,
        "unsupported_security_affecting_non_fill_count": len(unsupported),
        "unsupported_security_affecting_non_fill_rows": unsupported[:20],
    }


def inspect_market_page(raw: bytes) -> dict[str, Any]:
    payload = _decode_json(raw, label="market multi-bars response")
    if not isinstance(payload, Mapping):
        raise MinimalExternalReadError("market multi-bars response root must be an object")
    if "next_page_token" not in payload:
        raise MinimalExternalReadError("market multi-bars response missing next_page_token")
    token = payload.get("next_page_token")
    if token is not None and (not isinstance(token, str) or not token.strip()):
        raise MinimalExternalReadError("market multi-bars next_page_token malformed")
    return {
        "next_page_token_present": token is not None,
        "next_page_token_hash": None if token is None else hashlib.sha256(token.encode("utf-8")).hexdigest(),
    }


def _run_once(
    *,
    command: Sequence[str],
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> subprocess.CompletedProcess[bytes]:
    try:
        return runner(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
            env=_runtime_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise MinimalExternalReadError("Alpaca CLI provider read timed out") from exc
    except OSError as exc:
        raise MinimalExternalReadError("Alpaca CLI provider read failed to start") from exc


def _capture_path(raw_dir: Path, read_id: str) -> Path:
    return raw_dir / f"{read_id}.json"


def _result_artifact(
    *,
    code_commit_sha: str,
    preflight: Mapping[str, Any],
    authorization: Mapping[str, Any],
    tracker: ProviderDispatchTracker,
    status: str,
    next_gate: str,
    stop_reason: str | None,
    captures: list[dict[str, Any]],
    activity_inspection: Mapping[str, Any] | None,
    market_inspection: Mapping[str, Any] | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "artifact_version": RESULT_ARTIFACT_VERSION,
        "status": status,
        "code_commit_sha": code_commit_sha,
        "source_preflight_hash": preflight["artifact_hash"],
        "source_authorization_hash": authorization["artifact_hash"],
        "owner_approval_id": authorization["owner_approval_id"],
        "authorization_consumed": tracker.dispatch_attempts > 0,
        "provider_dispatch_attempts": tracker.dispatch_attempts,
        "provider_reads": tracker.provider_reads,
        "approved_provider_dispatch_attempts_max": PLANNED_PROVIDER_READS_MAX,
        "receipt_hashes": list(tracker.receipt_hashes),
        "receipt_manifest_hash": canonical_sha256({"receipt_hashes": tracker.receipt_hashes}),
        "captures": captures,
        "activity_page_inspection": None if activity_inspection is None else dict(activity_inspection),
        "market_page_inspection": None if market_inspection is None else dict(market_inspection),
        "stop_reason": stop_reason,
        "gap_closed": False,
        "automatic_retries": 0,
        "rerun_authorized": False,
        "model_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": next_gate,
    }
    result["artifact_hash"] = canonical_sha256(result)
    return result


def execute_provider_reads(
    *,
    code_commit_sha: str,
    preflight: Mapping[str, Any],
    authorization: Mapping[str, Any],
    receipt_path: str | Path,
    result_path: str | Path,
    raw_dir: str | Path,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    executable = preflight.get("alpaca_cli_path")
    if not isinstance(executable, str) or not executable:
        raise MinimalExternalReadError("preflight Alpaca CLI path missing")
    resolved = which("alpaca")
    if resolved is None or resolved != executable:
        raise MinimalExternalReadError("Alpaca CLI executable path drift")
    if authorization.get("approved_preflight_hash") != preflight.get("artifact_hash"):
        raise MinimalExternalReadError("authorization/preflight lineage mismatch")
    if authorization.get("approved_provider_dispatch_attempts_max") != PLANNED_PROVIDER_READS_MAX:
        raise MinimalExternalReadError("authorization dispatch ceiling drift")
    if authorization.get("automatic_retries") != 0 or authorization.get("rerun_authorized") is not False:
        raise MinimalExternalReadError("authorization retry/rerun drift")

    receipts = Path(receipt_path)
    result_file = Path(result_path)
    raw_root = Path(raw_dir)
    if receipts.exists() or result_file.exists() or raw_root.exists():
        raise MinimalExternalReadError("provider-read output path already exists")
    raw_root.mkdir(parents=True, exist_ok=False)
    os.chmod(raw_root, 0o700)

    tracker = ProviderDispatchTracker(
        authority_hash=str(authorization["artifact_hash"]),
        preflight_hash=str(preflight["artifact_hash"]),
        receipt_path=receipts,
    )
    captures: list[dict[str, Any]] = []
    activity_inspection: dict[str, Any] | None = None
    market_inspection: dict[str, Any] | None = None

    def dispatch(read_id: str, command: Sequence[str]) -> tuple[bytes, str]:
        attempted_at = _utc_now(now)
        tracker.begin(read_id=read_id, command=command, attempted_at_utc=attempted_at)
        try:
            completed = _run_once(command=command, runner=runner)
        except MinimalExternalReadError as exc:
            tracker.failed(read_id=read_id, error_class=type(exc).__name__, failed_at_utc=_utc_now(now))
            raise
        if completed.returncode != 0:
            tracker.failed(
                read_id=read_id,
                error_class=f"AlpacaCliReturnCode{completed.returncode}",
                failed_at_utc=_utc_now(now),
            )
            raise MinimalExternalReadError(f"{read_id} Alpaca CLI returned non-zero status")
        stdout = bytes(completed.stdout or b"")
        stderr = bytes(completed.stderr or b"")
        if not stdout:
            tracker.failed(read_id=read_id, error_class="EmptyStdout", failed_at_utc=_utc_now(now))
            raise MinimalExternalReadError(f"{read_id} Alpaca CLI returned empty stdout")
        received_at = _utc_now(now)
        raw_path = _capture_path(raw_root, read_id)
        _write_bytes_exclusive_fsync(raw_path, stdout)
        tracker.received(read_id=read_id, stdout=stdout, stderr=stderr, received_at_utc=received_at)
        captures.append(
            {
                "read_id": read_id,
                "raw_path": str(raw_path),
                "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                "stdout_bytes": len(stdout),
                "response_received_at_utc": received_at,
            }
        )
        return stdout, received_at

    try:
        _, anchor = dispatch(
            EXPECTED_READ_IDS[0],
            [executable, "position", "list", "--profile", "paper", "--quiet"],
        )

        activity_stdout, _ = dispatch(
            EXPECTED_READ_IDS[1],
            [
                executable,
                "account",
                "activity",
                "list",
                "--after",
                B2_CUTOFF,
                "--until",
                anchor,
                "--direction",
                "asc",
                "--page-size",
                str(ACTIVITY_PAGE_SIZE),
                "--profile",
                "paper",
                "--quiet",
            ],
        )
        activity_inspection = inspect_activity_page(activity_stdout)
        if activity_inspection["page_bound_reached"]:
            result = _result_artifact(
                code_commit_sha=code_commit_sha,
                preflight=preflight,
                authorization=authorization,
                tracker=tracker,
                status=PARTIAL_STATUS,
                next_gate="B3_REOPEN_MINIMAL_EXTERNAL_READ_OWNER_REVIEW",
                stop_reason="ACCOUNT_ACTIVITY_PAGE_BOUND_REACHED",
                captures=captures,
                activity_inspection=activity_inspection,
                market_inspection=None,
            )
            _write_json_exclusive_fsync(result_file, result)
            return result
        if activity_inspection["unsupported_security_affecting_non_fill_count"]:
            result = _result_artifact(
                code_commit_sha=code_commit_sha,
                preflight=preflight,
                authorization=authorization,
                tracker=tracker,
                status=PARTIAL_STATUS,
                next_gate="B3_REOPEN_MINIMAL_EXTERNAL_READ_OWNER_REVIEW",
                stop_reason="UNSUPPORTED_SECURITY_AFFECTING_NON_FILL_ACTIVITY",
                captures=captures,
                activity_inspection=activity_inspection,
                market_inspection=None,
            )
            _write_json_exclusive_fsync(result_file, result)
            return result

        dispatch(
            EXPECTED_READ_IDS[2],
            [
                executable,
                "account",
                "portfolio",
                "--start",
                PORTFOLIO_START,
                "--end",
                PORTFOLIO_END,
                "--timeframe",
                "1Min",
                "--intraday-reporting",
                "market_hours",
                "--profile",
                "paper",
                "--quiet",
            ],
        )

        market_stdout, _ = dispatch(
            EXPECTED_READ_IDS[3],
            [
                executable,
                "data",
                "multi-bars",
                "--symbols",
                "MSFT,META",
                "--start",
                MARKET_START,
                "--end",
                MARKET_END,
                "--timeframe",
                "1Min",
                "--limit",
                "1000",
                "--feed",
                "iex",
                "--sort",
                "asc",
                "--profile",
                "paper",
                "--quiet",
            ],
        )
        market_inspection = inspect_market_page(market_stdout)
        if market_inspection["next_page_token_present"]:
            result = _result_artifact(
                code_commit_sha=code_commit_sha,
                preflight=preflight,
                authorization=authorization,
                tracker=tracker,
                status=PARTIAL_STATUS,
                next_gate="B3_REOPEN_MINIMAL_EXTERNAL_READ_OWNER_REVIEW",
                stop_reason="MARKET_BARS_PAGINATION_NOT_COMPLETE",
                captures=captures,
                activity_inspection=activity_inspection,
                market_inspection=market_inspection,
            )
            _write_json_exclusive_fsync(result_file, result)
            return result

        result = _result_artifact(
            code_commit_sha=code_commit_sha,
            preflight=preflight,
            authorization=authorization,
            tracker=tracker,
            status=SUCCESS_STATUS,
            next_gate="B3_REOPEN_MINIMAL_EXTERNAL_READ_RECONCILIATION_ZERO_CALL",
            stop_reason=None,
            captures=captures,
            activity_inspection=activity_inspection,
            market_inspection=market_inspection,
        )
        _write_json_exclusive_fsync(result_file, result)
        return result
    except Exception as exc:
        if result_file.exists():
            raise
        result = _result_artifact(
            code_commit_sha=code_commit_sha,
            preflight=preflight,
            authorization=authorization,
            tracker=tracker,
            status=BLOCKED_STATUS,
            next_gate="B3_REOPEN_MINIMAL_EXTERNAL_READ_OWNER_REVIEW",
            stop_reason=f"{type(exc).__name__}: {exc}",
            captures=captures,
            activity_inspection=activity_inspection,
            market_inspection=market_inspection,
        )
        _write_json_exclusive_fsync(result_file, result)
        return result
