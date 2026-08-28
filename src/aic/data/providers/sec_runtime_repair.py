from __future__ import annotations

import re

from . import sec_filings


_SEC_HEADING_FLAGS = re.IGNORECASE | re.MULTILINE


def _heading(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"^[ \t]*{pattern}", _SEC_HEADING_FLAGS)


def apply_sec_heading_boundary_repair() -> None:
    """Bind B3 SEC section extraction to actual filing-heading lines.

    Inline cross-references such as ``Part II, Item 8`` inside MD&A must not be
    interpreted as section boundaries. This narrows the existing extractor; it
    does not add a source, section, or retrieval capability.
    """

    sec_filings._SECTION_PATTERNS = {
        "Business": (
            (_heading(r"ITEM\s+1\s*[.:\-]?\s*BUSINESS\b"),),
            (
                _heading(r"ITEM\s+1A\s*[.:\-]?\s*RISK\s+FACTORS\b"),
                _heading(r"ITEM\s+1B\b"),
            ),
        ),
        "Risk Factors": (
            (_heading(r"ITEM\s+1A\s*[.:\-]?\s*RISK\s+FACTORS\b"),),
            (
                _heading(r"ITEM\s+1B\b"),
                _heading(r"ITEM\s+1C\b"),
                _heading(r"ITEM\s+2\s*[.:\-]?\s*PROPERTIES\b"),
            ),
        ),
        "MD&A": (
            (
                _heading(
                    r"ITEM\s+7\s*[.:\-]?\s*MANAGEMENT(?:'|’)?S\s+DISCUSSION\s+AND\s+ANALYSIS\b"
                ),
                _heading(
                    r"ITEM\s+2\s*[.:\-]?\s*MANAGEMENT(?:'|’)?S\s+DISCUSSION\s+AND\s+ANALYSIS\b"
                ),
            ),
            (
                _heading(r"ITEM\s+7A\b"),
                _heading(r"ITEM\s+3\b"),
                _heading(r"ITEM\s+8\b"),
            ),
        ),
    }
