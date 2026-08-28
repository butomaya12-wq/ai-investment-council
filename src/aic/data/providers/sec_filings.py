from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


SEC_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
SEC_ARCHIVE_PREFIX = "https://www.sec.gov/Archives/edgar/data/"
SEC_SECTION_NORMALIZATION_VERSION = "B3_SEC_SECTION_TEXT_v0_1"
_SEC_ARCHIVE_RE = re.compile(
    r"^https://www\.sec\.gov/Archives/edgar/data/(?P<cik>[0-9]+)/(?P<rest>.+)$"
)
_ACCESSION_RE = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")


class SecFilingReadError(RuntimeError):
    pass


class SecProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SecFilingSection(SecProviderModel):
    section_name: str
    text: str
    content_hash: str

    @field_validator("section_name", "text")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError("SEC section fields must be non-empty and trimmed")
        return value

    @field_validator("content_hash")
    @classmethod
    def _hash(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("SEC section content_hash must be lowercase SHA-256")
        return value


class SecFilingSectionRead(SecProviderModel):
    accession: str
    source_uri: str
    accepted_at: datetime
    retrieved_at: datetime
    sections: tuple[SecFilingSection, ...]
    raw_payload_hash: str
    http_status: int
    normalization_version: str = SEC_SECTION_NORMALIZATION_VERSION

    @field_validator("accepted_at", "retrieved_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("SEC timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _identity(self):
        if _ACCESSION_RE.fullmatch(self.accession) is None:
            raise ValueError("SEC accession must use canonical dashed form")
        if not self.source_uri.startswith(SEC_ARCHIVE_PREFIX):
            raise ValueError("SEC source_uri must be an official archive URL")
        if not self.sections:
            raise ValueError("SEC filing-section read must contain at least one section")
        if len({section.section_name for section in self.sections}) != len(self.sections):
            raise ValueError("SEC filing-section names must be unique")
        if re.fullmatch(r"[0-9a-f]{64}", self.raw_payload_hash) is None:
            raise ValueError("SEC raw_payload_hash must be lowercase SHA-256")
        if self.http_status != 200:
            raise ValueError("successful SEC filing-section read requires HTTP 200")
        return self


class SecHttpTransport(Protocol):
    def get(self, *, url: str, user_agent: str, accept: str) -> tuple[int, bytes]:
        ...


@dataclass(frozen=True, slots=True)
class StdlibSecHttpTransport:
    timeout_seconds: int = 30
    min_interval_seconds: float = 0.11

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.timeout_seconds > 60:
            raise ValueError("SEC timeout_seconds must be within 1..60")
        if self.min_interval_seconds < 0.1:
            raise ValueError("SEC min interval must remain at or below the 10 requests/second policy ceiling")

    def get(self, *, url: str, user_agent: str, accept: str) -> tuple[int, bytes]:
        time.sleep(self.min_interval_seconds)
        request = Request(
            url,
            method="GET",
            headers={
                "User-Agent": user_agent,
                "Accept": accept,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return int(response.status), response.read()
        except HTTPError as exc:
            raise SecFilingReadError(f"SEC HTTP failure: {exc.code}") from exc
        except URLError as exc:
            raise SecFilingReadError("SEC network failure") from exc


def load_sec_user_agent(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    value = source.get("AIC_SEC_USER_AGENT")
    if value is None:
        raise SecFilingReadError("AIC_SEC_USER_AGENT is not present in the runtime environment")
    if not isinstance(value, str) or not value or value != value.strip():
        raise SecFilingReadError("AIC_SEC_USER_AGENT must be a non-empty trimmed string")
    return value


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored_depth += 1
        elif self._ignored_depth == 0 and tag.lower() in {
            "p",
            "div",
            "br",
            "tr",
            "td",
            "th",
            "li",
            "h1",
            "h2",
            "h3",
            "h4",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif self._ignored_depth == 0 and tag.lower() in {"p", "div", "tr", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self.parts.append(data)


def _normalize_visible_text(raw_html: bytes) -> str:
    try:
        decoded = raw_html.decode("utf-8")
    except UnicodeDecodeError:
        decoded = raw_html.decode("latin-1")
    parser = _VisibleTextParser()
    parser.feed(decoded)
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


_SECTION_PATTERNS: dict[str, tuple[tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]]] = {
    "Business": (
        (re.compile(r"\bITEM\s+1\s*[.:\-]?\s*BUSINESS\b", re.IGNORECASE),),
        (
            re.compile(r"\bITEM\s+1A\s*[.:\-]?\s*RISK\s+FACTORS\b", re.IGNORECASE),
            re.compile(r"\bITEM\s+1B\b", re.IGNORECASE),
        ),
    ),
    "Risk Factors": (
        (re.compile(r"\bITEM\s+1A\s*[.:\-]?\s*RISK\s+FACTORS\b", re.IGNORECASE),),
        (
            re.compile(r"\bITEM\s+1B\b", re.IGNORECASE),
            re.compile(r"\bITEM\s+1C\b", re.IGNORECASE),
            re.compile(r"\bITEM\s+2\s*[.:\-]?\s*PROPERTIES\b", re.IGNORECASE),
        ),
    ),
    "MD&A": (
        (
            re.compile(
                r"\bITEM\s+7\s*[.:\-]?\s*MANAGEMENT(?:'|’)?S\s+DISCUSSION\s+AND\s+ANALYSIS\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\bITEM\s+2\s*[.:\-]?\s*MANAGEMENT(?:'|’)?S\s+DISCUSSION\s+AND\s+ANALYSIS\b",
                re.IGNORECASE,
            ),
        ),
        (
            re.compile(r"\bITEM\s+7A\b", re.IGNORECASE),
            re.compile(r"\bITEM\s+3\b", re.IGNORECASE),
            re.compile(r"\bITEM\s+8\b", re.IGNORECASE),
        ),
    ),
}


def _extract_section(text: str, section_name: str) -> str:
    patterns = _SECTION_PATTERNS.get(section_name)
    if patterns is None:
        raise SecFilingReadError(f"unsupported SEC section: {section_name}")
    start_patterns, end_patterns = patterns
    candidates: list[str] = []
    for start_pattern in start_patterns:
        for match in start_pattern.finditer(text):
            end_positions: list[int] = []
            for end_pattern in end_patterns:
                end_match = end_pattern.search(text, match.end())
                if end_match is not None:
                    end_positions.append(end_match.start())
            if not end_positions:
                continue
            candidate = text[match.start() : min(end_positions)].strip()
            if len(candidate) >= 500:
                candidates.append(candidate)
    if not candidates:
        raise SecFilingReadError(f"SEC section not found or too short: {section_name}")
    return max(candidates, key=len)


def _parse_accepted_at(submissions_payload: Mapping[str, object], *, accession: str) -> datetime:
    filings = submissions_payload.get("filings")
    if not isinstance(filings, Mapping):
        raise SecFilingReadError("SEC submissions payload missing filings")
    recent = filings.get("recent")
    if not isinstance(recent, Mapping):
        raise SecFilingReadError("SEC submissions payload missing filings.recent")
    accessions = recent.get("accessionNumber")
    accepted = recent.get("acceptanceDateTime")
    if not isinstance(accessions, list) or not isinstance(accepted, list) or len(accessions) != len(accepted):
        raise SecFilingReadError("SEC submissions accession/time arrays are invalid")
    try:
        index = accessions.index(accession)
    except ValueError as exc:
        raise SecFilingReadError("requested SEC accession is absent from current submissions metadata") from exc
    raw = accepted[index]
    if not isinstance(raw, str) or not raw:
        raise SecFilingReadError("SEC acceptanceDateTime must be a non-empty string")
    text = raw.strip().replace("Z", "+00:00")
    if re.fullmatch(r"[0-9]{14}", text):
        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SecFilingReadError("SEC acceptanceDateTime is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SecFilingReadError("SEC acceptanceDateTime must be timezone-aware")
    return parsed.astimezone(UTC)


def read_sec_filing_sections(
    *,
    accession: str,
    source_uri: str,
    section_names: tuple[str, ...],
    research_cutoff: datetime,
    user_agent: str,
    transport: SecHttpTransport | None = None,
) -> SecFilingSectionRead:
    if _ACCESSION_RE.fullmatch(accession) is None:
        raise SecFilingReadError("SEC accession must use canonical dashed form")
    match = _SEC_ARCHIVE_RE.fullmatch(source_uri)
    if match is None:
        raise SecFilingReadError("SEC source URI is outside the official archive allowlist")
    if not section_names or len(set(section_names)) != len(section_names):
        raise SecFilingReadError("SEC section_names must be non-empty and unique")
    if research_cutoff.tzinfo is None or research_cutoff.utcoffset() is None:
        raise SecFilingReadError("research_cutoff must be timezone-aware")
    cutoff = research_cutoff.astimezone(UTC)

    cik = int(match.group("cik"))
    submissions_uri = f"{SEC_SUBMISSIONS_BASE}/CIK{cik:010d}.json"
    runtime_transport = StdlibSecHttpTransport() if transport is None else transport
    metadata_status, metadata_raw = runtime_transport.get(
        url=submissions_uri,
        user_agent=user_agent,
        accept="application/json",
    )
    filing_status, filing_raw = runtime_transport.get(
        url=source_uri,
        user_agent=user_agent,
        accept="text/html,application/xhtml+xml",
    )
    if metadata_status != 200 or filing_status != 200:
        raise SecFilingReadError("SEC retrieval requires HTTP 200 for metadata and filing document")

    try:
        metadata = json.loads(metadata_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecFilingReadError("SEC submissions response is not valid UTF-8 JSON") from exc
    if not isinstance(metadata, Mapping):
        raise SecFilingReadError("SEC submissions response must be a JSON object")
    accepted_at = _parse_accepted_at(metadata, accession=accession)
    if accepted_at > cutoff:
        raise SecFilingReadError("requested SEC filing was not knowable by research cutoff")

    visible_text = _normalize_visible_text(filing_raw)
    sections: list[SecFilingSection] = []
    for section_name in section_names:
        section_text = _extract_section(visible_text, section_name)
        sections.append(
            SecFilingSection(
                section_name=section_name,
                text=section_text,
                content_hash=hashlib.sha256(section_text.encode("utf-8")).hexdigest(),
            )
        )

    combined_hash = hashlib.sha256(
        metadata_raw + b"\x00AIC_SEC_DOCUMENT\x00" + filing_raw
    ).hexdigest()
    return SecFilingSectionRead(
        accession=accession,
        source_uri=source_uri,
        accepted_at=accepted_at,
        retrieved_at=datetime.now(UTC),
        sections=tuple(sections),
        raw_payload_hash=combined_hash,
        http_status=200,
    )
