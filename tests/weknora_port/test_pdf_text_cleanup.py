"""Plain PDF text cleanup must retain facts without page-position evidence."""
from __future__ import annotations

import pytest

from backend.weknora_port.ingestion import upstream_pdf_parser as parser


@pytest.mark.parametrize("line_ending", ["\n", "\r\n", "\r"])
def test_arxiv_cleanup_preserves_standalone_numeric_facts(line_ending: str) -> None:
    lines = ["Recorded counts", "0", "Group A", "7", "Group B", "42", "Total", "315"]

    cleaned = parser._strip_arxiv_and_page_num_lines(line_ending.join(lines))

    assert cleaned.splitlines() == lines


def test_arxiv_cleanup_retains_numeric_facts_around_standalone_and_inline_markings() -> None:
    text = (
        "First group\n63\narXiv: 2501.01234v2 [cs.LG] 3 Jan 2025\n"
        "Second group\n29\nStudy notes arXiv: 2502.05678 [cs.AI] 4 Feb 2025"
    )

    cleaned = parser._strip_arxiv_and_page_num_lines(text)

    assert cleaned.splitlines() == ["First group", "63", "Second group", "29", "Study notes"]


def test_pdf_postprocessing_preserves_labelled_numeric_facts() -> None:
    text = "Recorded count\n63\nRemaining count\n29\nEnd of record."

    assert parser._postprocess_pdf_text(text) == text


@pytest.mark.parametrize("separator", ["\n", "\n\n"])
def test_pdf_cleanup_preserves_numeric_blocks_without_chart_markers(separator: str) -> None:
    values = ["0", "7", "42", "315", "0.25", "-4"]
    text = "Observed measurements\n" + separator.join(values) + "\nEnd of record."

    assert parser._strip_chart_text_debris(text) == text
    assert parser._postprocess_pdf_text(text) == text


def test_pdf_cleanup_still_removes_runs_with_explicit_chart_debris_markers() -> None:
    text = "Body text.\n0\n1\n2\n3-layer\ntraining error (%)\niter. (1e4)\nEnd of record."

    assert parser._postprocess_pdf_text(text) == "Body text.\nEnd of record."


def test_pdf_cleanup_retains_existing_figure_caption_guard() -> None:
    paragraph = "This paragraph contains enough words to describe the experimental procedure."
    text = paragraph + "\nInput labels\n32-layer\nFigure 1: Network overview."

    assert parser._postprocess_pdf_text(text) == paragraph + "\nFigure 1: Network overview."
