from __future__ import annotations

from aic.data.providers import sec_filings
from aic.data.providers.sec_runtime_repair import apply_sec_heading_boundary_repair


def test_mda_inline_item_8_reference_does_not_end_section() -> None:
    apply_sec_heading_boundary_repair()
    body = "Operating results, liquidity, trends, and uncertainties. " * 40
    text = "\n".join(
        (
            "Item 7.Management's Discussion and Analysis of Financial Condition and Results of Operations",
            'Read this discussion with Part II, Item 8, "Financial Statements and Supplementary Data".',
            body,
            "Item 7A.Quantitative and Qualitative Disclosures About Market Risk",
            "Market-risk content",
        )
    )

    section = sec_filings._extract_section(text, "MD&A")

    assert "Part II, Item 8" in section
    assert "Operating results" in section
    assert "Item 7A" not in section
    assert len(section) >= 500


def test_mda_curly_apostrophe_heading_is_supported() -> None:
    apply_sec_heading_boundary_repair()
    body = "Management analysis of financial performance and known uncertainties. " * 30
    text = "\n".join(
        (
            "ITEM 7. MANAGEMENT’S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS",
            body,
            "ITEM 8. FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA",
        )
    )

    section = sec_filings._extract_section(text, "MD&A")

    assert "MANAGEMENT’S DISCUSSION" in section
    assert "ITEM 8" not in section
    assert len(section) >= 500
