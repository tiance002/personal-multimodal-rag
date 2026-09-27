from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from xml.dom import minidom
from xml.parsers.expat import ExpatError
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import fitz


OFFICE_MEDIA_TYPES = {
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
OFFICE_SUFFIXES = {".doc", ".docx", ".xls", ".xlsx"}
SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


class OfficePreviewUnavailable(RuntimeError):
    """Raised when the local office renderer cannot produce a PDF preview."""


@dataclass(frozen=True)
class OfficePreviewFile:
    path: Path
    directory: Path
    file_name: str


def is_office_document(file_name: str, media_type: str) -> bool:
    return media_type.split(";", 1)[0].strip().lower() in OFFICE_MEDIA_TYPES or Path(file_name).suffix.lower() in OFFICE_SUFFIXES


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def cleanup_office_preview(preview: OfficePreviewFile) -> None:
    _cleanup(preview.directory)


def _prepare_xlsx_pages(input_path: Path) -> None:
    """Keep each sheet's columns together in a disposable preview copy.

    Calc otherwise obeys a workbook's portrait print settings and can put the
    right half of a row on the next PDF page. ZIP members other than worksheet
    XML, including embedded images, are copied byte for byte.
    """
    staged_path = input_path.with_suffix(".preview.xlsx")
    try:
        with ZipFile(input_path) as original, ZipFile(staged_path, "w") as staged:
            for entry in original.infolist():
                data = original.read(entry.filename)
                if entry.filename.startswith("xl/worksheets/sheet") and entry.filename.endswith(".xml"):
                    document = minidom.parseString(data)
                    sheet = document.documentElement
                    prefix = f"{sheet.prefix}:" if sheet.prefix else ""

                    def direct_child(name: str):
                        return next(
                            (
                                node for node in sheet.childNodes
                                if node.nodeType == node.ELEMENT_NODE
                                and node.namespaceURI == SPREADSHEET_NS
                                and node.localName == name
                            ),
                            None,
                        )

                    sheet_properties = direct_child("sheetPr")
                    if sheet_properties is None:
                        sheet_properties = document.createElementNS(SPREADSHEET_NS, f"{prefix}sheetPr")
                        sheet.insertBefore(sheet_properties, sheet.firstChild)
                    setup_properties = next(
                        (
                            node for node in sheet_properties.childNodes
                            if node.nodeType == node.ELEMENT_NODE and node.localName == "pageSetUpPr"
                        ),
                        None,
                    )
                    if setup_properties is None:
                        setup_properties = document.createElementNS(SPREADSHEET_NS, f"{prefix}pageSetUpPr")
                        sheet_properties.appendChild(setup_properties)
                    setup_properties.setAttribute("fitToPage", "1")

                    page_setup = direct_child("pageSetup")
                    if page_setup is None:
                        page_setup = document.createElementNS(SPREADSHEET_NS, f"{prefix}pageSetup")
                        margins = direct_child("pageMargins")
                        if margins is not None and margins.nextSibling is not None:
                            sheet.insertBefore(page_setup, margins.nextSibling)
                        else:
                            sheet.appendChild(page_setup)
                    page_setup.setAttribute("fitToWidth", "1")
                    page_setup.setAttribute("fitToHeight", "0")
                    page_setup.setAttribute("orientation", "landscape")
                    page_setup.setAttribute("paperSize", "8")  # A3, so wide tables remain legible.
                    if page_setup.hasAttribute("scale"):
                        page_setup.removeAttribute("scale")
                    data = document.toxml(encoding="utf-8")
                    document.unlink()
                staged.writestr(entry, data)
        os.replace(staged_path, input_path)
    except (BadZipFile, ExpatError, ValueError, OSError):
        staged_path.unlink(missing_ok=True)
        # LibreOffice can still attempt the untouched source if it is unusual.


def _crop_spreadsheet_whitespace(pdf_path: Path) -> None:
    """Remove page whitespace without removing cells, drawings or images."""
    staged_path = pdf_path.with_name(f"{pdf_path.stem}.cropped.pdf")
    try:
        with fitz.open(pdf_path) as pdf:
            for page in pdf:
                bounds = [fitz.Rect(block["bbox"]) for block in page.get_text("dict")["blocks"]]
                bounds.extend(fitz.Rect(drawing["rect"]) for drawing in page.get_drawings())
                bounds = [rect & page.rect for rect in bounds if not rect.is_empty]
                if not bounds:
                    continue
                content = bounds[0]
                for rect in bounds[1:]:
                    content |= rect
                padded = fitz.Rect(
                    max(page.rect.x0, content.x0 - 24),
                    max(page.rect.y0, content.y0 - 24),
                    min(page.rect.x1, content.x1 + 24),
                    min(page.rect.y1, content.y1 + 24),
                )
                if not padded.is_empty:
                    page.set_cropbox(padded)
            pdf.save(staged_path)
        os.replace(staged_path, pdf_path)
    finally:
        staged_path.unlink(missing_ok=True)


def convert_office_to_pdf(source_path: Path, file_name: str, media_type: str, timeout_seconds: int = 120) -> OfficePreviewFile:
    """Convert an immutable Office source to a temporary PDF for browser viewing.

    Content-addressed storage deliberately omits the original suffix, so the
    source is copied to a temporary path with its safe display name first. This
    lets LibreOffice select the correct Writer/Calc importer and keeps embedded
    images, tables, page breaks and other original layout information intact.
    """
    binary = shutil.which("soffice") or shutil.which("libreoffice")
    if not binary:
        raise OfficePreviewUnavailable("OFFICE_RENDERER_MISSING")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    safe_name = Path(file_name).name
    if safe_name in {"", ".", ".."}:
        safe_name = "document"
    suffix = Path(safe_name).suffix.lower()
    if suffix not in OFFICE_SUFFIXES:
        normalized_media_type = media_type.split(";", 1)[0].strip().lower()
        suffix_by_media = {
            "application/msword": ".doc",
            "application/vnd.ms-excel": ".xls",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        }
        safe_name = f"{Path(safe_name).stem}{suffix_by_media.get(normalized_media_type, '.docx')}"

    directory = Path(tempfile.mkdtemp(prefix="rag-office-preview-"))
    input_path = directory / safe_name
    profile_path = directory / "libreoffice-profile"
    try:
        shutil.copyfile(source_path, input_path)
        if input_path.suffix.lower() == ".xlsx":
            _prepare_xlsx_pages(input_path)
        result = subprocess.run(
            [
                binary,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                "--nolockcheck",
                f"-env:UserInstallation=file://{profile_path.resolve().as_posix()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(directory),
                str(input_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env={**os.environ, "HOME": str(directory / "home")},
        )
        output_path = directory / f"{input_path.stem}.pdf"
        if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
            raise OfficePreviewUnavailable("OFFICE_RENDER_FAILED")
        if input_path.suffix.lower() == ".xlsx":
            try:
                _crop_spreadsheet_whitespace(output_path)
            except (RuntimeError, ValueError, OSError):
                # The successfully converted full page remains usable.
                pass
        return OfficePreviewFile(path=output_path, directory=directory, file_name=f"{input_path.stem}.pdf")
    except (OSError, subprocess.SubprocessError) as exc:
        _cleanup(directory)
        if isinstance(exc, subprocess.TimeoutExpired):
            raise OfficePreviewUnavailable("OFFICE_RENDER_TIMEOUT") from exc
        raise OfficePreviewUnavailable("OFFICE_RENDER_FAILED") from exc
    except Exception:
        _cleanup(directory)
        raise


__all__ = [
    "OfficePreviewFile",
    "OfficePreviewUnavailable",
    "cleanup_office_preview",
    "convert_office_to_pdf",
    "is_office_document",
]
