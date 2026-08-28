from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from .alpaca_news import ALPACA_NEWS_ENDPOINT, AlpacaNewsReadError


CLI_PROFILE_CREDENTIAL_PLACEHOLDER = "ALPACA_CLI_PROFILE_AUTH"


class AlpacaCliRunner(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        *,
        stdout: int,
        stderr: int,
        timeout: int,
        check: bool,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[bytes]:
        ...


@dataclass(frozen=True, slots=True)
class AlpacaCliNewsTransport:
    profile: str = "paper"
    executable: str = "alpaca"
    timeout_seconds: int = 30
    runner: AlpacaCliRunner = subprocess.run

    def __post_init__(self) -> None:
        if not self.profile or self.profile != self.profile.strip():
            raise ValueError("Alpaca CLI profile must be a non-empty trimmed string")
        if not self.executable or self.executable != self.executable.strip():
            raise ValueError("Alpaca CLI executable must be a non-empty trimmed string")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 60:
            raise ValueError("Alpaca CLI timeout_seconds must be within 1..60")

    def get(
        self,
        *,
        endpoint: str,
        query: Mapping[str, str],
        api_key_id: str,
        api_secret_key: str,
    ) -> tuple[int, bytes]:
        if endpoint != ALPACA_NEWS_ENDPOINT:
            raise AlpacaNewsReadError("Alpaca CLI news endpoint drift")
        if api_key_id != CLI_PROFILE_CREDENTIAL_PLACEHOLDER or api_secret_key != CLI_PROFILE_CREDENTIAL_PLACEHOLDER:
            raise AlpacaNewsReadError("Alpaca CLI transport requires profile-auth placeholder binding")

        expected_keys = {
            "symbols",
            "start",
            "end",
            "sort",
            "limit",
            "include_content",
            "exclude_contentless",
        }
        if set(query) != expected_keys:
            raise AlpacaNewsReadError("Alpaca CLI news query shape drift")
        if query["include_content"] != "true" or query["exclude_contentless"] != "false":
            raise AlpacaNewsReadError("Alpaca CLI news content flags drift")

        executable = shutil.which(self.executable)
        if executable is None:
            raise AlpacaNewsReadError("Alpaca CLI executable is unavailable")

        command = [
            executable,
            "data",
            "news",
            "--symbols",
            query["symbols"],
            "--start",
            query["start"],
            "--end",
            query["end"],
            "--sort",
            query["sort"],
            "--limit",
            query["limit"],
            "--include-content=true",
            "--exclude-contentless=false",
            "--profile",
            self.profile,
            "--quiet",
        ]
        runtime_env = dict(os.environ)
        runtime_env["ALPACA_QUIET"] = "1"
        runtime_env.pop("ALPACA_LIVE_TRADE", None)
        try:
            completed = self.runner(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                env=runtime_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise AlpacaNewsReadError("Alpaca CLI news request timed out") from exc
        except OSError as exc:
            raise AlpacaNewsReadError("Alpaca CLI news process failed to start") from exc

        if completed.returncode == 2:
            raise AlpacaNewsReadError("Alpaca CLI profile authentication failed")
        if completed.returncode != 0:
            raise AlpacaNewsReadError("Alpaca CLI news request failed")
        if not completed.stdout:
            raise AlpacaNewsReadError("Alpaca CLI news returned empty stdout")
        return 200, bytes(completed.stdout)
