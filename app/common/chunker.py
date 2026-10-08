"""Paragraph-aware chunker with a hard maximum chunk size (in characters).

Paragraphs are packed greedily; oversize paragraphs fall back to lines, then sentences, then
words, and finally hard slices, so every chunk is guaranteed to be <= max_chars.
"""
from __future__ import annotations

import re

from .text import split_sentences

_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")


def _split_oversize(unit: str, max_chars: int) -> list:
    if len(unit) <= max_chars:
        return [unit]
    lines = [ln.strip() for ln in unit.split("\n") if ln.strip()]
    if len(lines) > 1:
        return _pack([piece for ln in lines for piece in _split_oversize(ln, max_chars)], max_chars, "\n")
    sentences = split_sentences(unit)
    if len(sentences) > 1:
        return _pack([piece for s in sentences for piece in _split_oversize(s, max_chars)], max_chars, " ")
    words = unit.split()
    if len(words) > 1:
        return _pack([piece for w in words for piece in _split_oversize(w, max_chars)], max_chars, " ")
    return [unit[i:i + max_chars] for i in range(0, len(unit), max_chars)]


def _pack(units: list, max_chars: int, sep: str) -> list:
    chunks, current = [], ""
    for unit in units:
        if not current:
            current = unit
        elif len(current) + len(sep) + len(unit) <= max_chars:
            current = current + sep + unit
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def chunk_text(text: str, max_chars: int) -> list:
    if max_chars < 20:
        raise ValueError("max_chars must be >= 20")
    paragraphs = [p.strip() for p in _PARAGRAPH_BREAK.split(text.strip()) if p.strip()]
    pieces = []
    for paragraph in paragraphs:
        pieces.extend(_split_oversize(paragraph, max_chars))
    chunks = _pack(pieces, max_chars, "\n\n")
    assert all(len(c) <= max_chars for c in chunks)
    return chunks
