"""Adapted helpers from WeKnora docreader/parser/markdown_parser.py.

Upstream commit 3e8b0bfc80b845b2d4b2ed683994748741450a97; Copyright (C) 2025
Tencent. The complete MIT terms are in LICENSE-THIRD-PARTY-WeKnora-MIT.txt.
The table normalizer and Markdown image extraction preserve the upstream
MarkItDown -> MarkdownParser pipeline used by the built-in DOCX route.
"""
from __future__ import annotations

import base64
import re
import uuid

_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
_ALIGNMENT_CELL = re.compile(r"^[:-]+$")
_DATA_IMAGE = re.compile(r"!\[(.*?)\]\(data:image/([^;]+);base64,([^\)]+)\)")


def _split_row_cells(row_line: str) -> list[str]:
    inner = row_line.strip()
    if not inner.startswith("|"):
        return []
    parts = inner.split("|")
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [part.strip() for part in parts]


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and "|" in stripped[1:]


def _is_separator_row(line: str) -> bool:
    cells = _split_row_cells(line)
    return bool(cells) and all(_SEPARATOR_CELL.match(cell) for cell in cells)


def _is_alignment_row(line: str) -> bool:
    cells = _split_row_cells(line)
    return bool(cells) and all(_ALIGNMENT_CELL.match(cell) for cell in cells)


def _is_empty_row(line: str) -> bool:
    cells = _split_row_cells(line)
    return bool(cells) and all(cell == "" for cell in cells)


def _separator_row_for(header_line: str) -> str:
    cells = _split_row_cells(header_line)
    return "| " + " | ".join("---" for _ in cells) + " |"


def _normalize_table_block(block: list[str]) -> list[str]:
    while block and _is_empty_row(block[0]):
        block.pop(0)
    if block and _is_separator_row(block[0]):
        block.pop(0)
    if len(block) >= 2 and not _is_separator_row(block[1]):
        block = [block[0], _separator_row_for(block[0])] + block[1:]
    return block


def format_markdown_tables(content: str) -> str:
    """Port upstream MarkdownTableUtil.format_table, including prefix repair."""
    unified = content.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for line in unified.split("\n"):
        stripped = line.lstrip(" \t")
        if stripped.startswith("|") and line.rstrip(" \t").endswith("|"):
            prefix = line[:len(line) - len(stripped)]
            columns = _split_row_cells(line)
            if not columns:
                out.append(line)
            elif _is_alignment_row(line):
                formatted = []
                for column in columns:
                    left = ":" if column.startswith(":") else ""
                    right = ":" if column.endswith(":") else ""
                    formatted.append(left + "---" + right)
                out.append(prefix + "| " + " | ".join(formatted) + " |")
            else:
                out.append(prefix + "| " + " | ".join(columns) + " |")
        else:
            out.append(line)

    lines = "\n".join(out).split("\n")
    normalized: list[str] = []
    index = 0
    while index < len(lines):
        if not _is_table_row(lines[index]):
            normalized.append(lines[index])
            index += 1
            continue
        block: list[str] = []
        while index < len(lines) and _is_table_row(lines[index]):
            block.append(lines[index])
            index += 1
        normalized.extend(_normalize_table_block(block))
    return "\n".join(normalized)


def extract_markdown_data_images(content: str) -> tuple[str, list[tuple[str, bytes, str]]]:
    """Port upstream MarkdownImageUtil.extract_base64 with storage-neutral bytes."""
    images: list[tuple[str, bytes, str]] = []

    def replace(match: re.Match[str]) -> str:
        title, extension, encoded = match.groups()
        try:
            raw = base64.b64decode(encoded, validate=False)
        except (ValueError, base64.binascii.Error):
            raw = b""
        if not raw:
            return title
        path = f"images/{uuid.uuid4()}.{extension}"
        images.append((path, raw, f"image/{extension.lower()}"))
        return f"![{title}]({path})"

    return _DATA_IMAGE.sub(replace, content), images
