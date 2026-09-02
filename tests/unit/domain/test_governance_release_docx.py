"""Structural fallback QA for release DOCX when LibreOffice is unavailable."""

from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest


RELEASE_DIR = (
    Path(__file__).parents[3] / "docs" / "governance" / "releases" / "v1.1"
)
DOCX_EXPECTATIONS = {
    "WERKcrew_Constitution_v1.1.docx": (
        "WERKcrew Constitution v1.1",
        "TRUTH ≠ AUTHORITY ≠ EXECUTION",
        "NO_DECEPTION",
        "RECOVERY_REQUIRED",
    ),
    "WERKcrew_State_Transition_Matrix_v1.1.docx": (
        "WERKcrew State & Transition Matrix v1.1",
        "DOM/KLINIKA/LOFT",
        "EXECUTE_WITH_RECORDED_RISK",
        "FORBIDDEN",
    ),
    "WERKcrew_Decision_Assurance_Pack_v0.1.docx": (
        "WERKcrew Decision Assurance Pack v0.1",
        "DRAFT_TEXT",
        "APPROVED_TEXT",
        "SENT_MESSAGE",
        "ACTUAL_CLAIM",
    ),
}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _document(
    path: Path,
) -> tuple[ElementTree.Element, ElementTree.Element, str, set[str]]:
    with ZipFile(path) as package:
        entries = set(package.namelist())
        root = ElementTree.fromstring(package.read("word/document.xml"))
        settings = ElementTree.fromstring(package.read("word/settings.xml"))
        text = " ".join(node.text or "" for node in root.iter(f"{W}t"))
    return root, settings, text, entries


@pytest.mark.parametrize(("filename", "phrases"), DOCX_EXPECTATIONS.items())
def test_release_docx_is_complete_ooxml_and_contains_normative_terms(
    filename: str,
    phrases: tuple[str, ...],
) -> None:
    root, settings, text, entries = _document(RELEASE_DIR / filename)

    assert {
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
        "word/_rels/document.xml.rels",
        "word/styles.xml",
        "word/numbering.xml",
        "word/header1.xml",
        "word/header2.xml",
        "word/header3.xml",
        "word/footer1.xml",
        "word/footer2.xml",
        "word/footer3.xml",
    } <= entries
    assert all(phrase in text for phrase in phrases)
    assert not any(marker in text for marker in ("TODO", "TBD", "Lorem ipsum"))
    assert root.find(f".//{W}sectPr") is not None
    assert root.find(f".//{W}pgSz") is not None
    assert root.find(f".//{W}pgMar") is not None
    assert settings.find(f".//{W}evenAndOddHeaders") is not None


@pytest.mark.parametrize("filename", DOCX_EXPECTATIONS)
def test_release_docx_has_no_tracked_changes_comments_or_fixed_row_clipping(
    filename: str,
) -> None:
    path = RELEASE_DIR / filename
    root, _, _, _ = _document(path)

    assert root.find(f".//{W}ins") is None
    assert root.find(f".//{W}del") is None
    assert root.find(f".//{W}commentRangeStart") is None
    assert root.find(f".//{W}commentReference") is None
    exact_heights = [
        node
        for node in root.findall(f".//{W}trHeight")
        if node.attrib.get(f"{W}hRule") == "exact"
    ]
    assert exact_heights == []


@pytest.mark.parametrize("filename", DOCX_EXPECTATIONS)
def test_release_docx_tables_declare_grid_and_cell_widths(filename: str) -> None:
    root, _, _, _ = _document(RELEASE_DIR / filename)

    for table in root.findall(f".//{W}tbl"):
        assert table.find(f"./{W}tblGrid") is not None
        assert table.find(f"./{W}tblPr/{W}tblW") is not None
        cells = table.findall(f".//{W}tc")
        assert cells
        assert all(cell.find(f"./{W}tcPr/{W}tcW") is not None for cell in cells)
