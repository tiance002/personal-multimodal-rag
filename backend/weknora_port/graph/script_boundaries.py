"""Literal graph surfaces across Chinese/word-delimited script boundaries.

Han text has no mandatory spaces between names and predicates. Recognizing a
literal substring does not establish a Chinese word boundary or entity identity;
the caller must still validate the quote, direction and identity ambiguity.
This module never normalizes text, so source offsets remain original offsets.
"""
from __future__ import annotations

import re
import unicodedata


def _han(char: str) -> bool:
    code = ord(char)
    return (0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF or 0x20000 <= code <= 0x323AF)


def _word_character(char: str) -> bool:
    # Marks remain joined to their base; connector/dash punctuation and
    # apostrophes remain part of identifiers and hyphenated entity surfaces.
    category = unicodedata.category(char)
    return (char.isalnum() or category[0] == 'M' or category in ('Pc', 'Pd')
            or char in "'’")


def _joined(left: str, right: str) -> bool:
    if _han(left) or _han(right):
        return False
    return _word_character(left) and _word_character(right)


def exact_surface_occurrences(text: str, surface: str) -> list[tuple[int, int]]:
    """Return exact case-sensitive spans without splitting word identifiers.

Adjacent Han characters are allowed, including Han/Latin mixed text. No fuzzy
matching, case folding, spacing insertion, segmentation or alias expansion is
performed here. Query callers may keep their separately defined normalization.
"""
    if not surface:
        return []
    result = []
    for match in re.finditer(re.escape(surface), text):
        start, end = match.span()
        if start and _joined(text[start - 1], surface[0]):
            continue
        if end < len(text) and _joined(surface[-1], text[end]):
            continue
        result.append((start, end))
    return result
