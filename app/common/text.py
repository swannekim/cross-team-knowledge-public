"""Tokenisation, sentence splitting and safe truncation shared by search, summarisation and chunking."""
from __future__ import annotations

import re

STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before being below between
both but by can could did do does doing down during each few for from further had has have having he her here
hers him his how i if in into is it its itself just me might more most must my no nor not now of off on once only
or other our ours out over own per same she should so some such than that the their theirs them then there these
they this those through to too under until up us very via was we were what when where which while who whom why
will with within would you your yours
""".split())

_HANGUL = "\uac00-\ud7a3"
_TOKEN = re.compile(rf"[0-9a-z{_HANGUL}]+(?:[._/-][0-9a-z{_HANGUL}]+)*")
_PART = re.compile(rf"[0-9a-z{_HANGUL}]+")
_COMPOUND = re.compile(rf"[0-9a-z{_HANGUL}]+(?:[._/-][0-9a-z{_HANGUL}]+)+")
# A boundary needs a lower-case letter, digit, Hangul or closing mark before the final punctuation, so
# initials ("J. H. Kim") and single-letter tokens ("chamber B.") do not end a sentence.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[a-z0-9%)\]\"'\uac00-\ud7a3][.!?])\s+(?=[\"'(\[]?[A-Z0-9\uac00-\ud7a3])")


def stem(token: str) -> str:
    """Light English suffix stripping (changed/changes/changing -> chang, wafers -> wafer) for search matching.
    Production search (Microsoft Search, Azure AI Search analyzers) applies full linguistic processing."""
    if len(token) <= 3 or not token.isalpha() or not token.isascii():
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    for suffix in ("ing", "ed", "es"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            token = token[: -len(suffix)]
            break
    else:
        if token.endswith("s") and not token.endswith(("ss", "us", "is")):
            token = token[:-1]
    return token[:-1] if token.endswith("e") and len(token) > 3 else token


def tokenize(text: str, *, keep_stopwords: bool = False, expand_compounds: bool = True, stem_words: bool = False) -> list:
    """Bag-of-words tokens (lower-cased). Compound tokens such as 'etch-07' or 'cf4/o2' are kept whole
    and, with expand_compounds, also contribute their parts. Token order is not significant."""
    lower = text.lower()
    tokens = (_PART.findall(lower) + _COMPOUND.findall(lower)) if expand_compounds else _TOKEN.findall(lower)
    if not keep_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return [stem(t) for t in tokens] if stem_words else tokens


def split_sentences(text: str) -> list:
    """Splits on line breaks and sentence-final punctuation followed by an upper-case/digit start."""
    sentences = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        sentences.extend(part.strip() for part in _SENTENCE_BOUNDARY.split(line) if part.strip())
    return sentences


def truncate(text: str, max_chars: int, ellipsis: str = "…") -> str:
    """Truncates at a word boundary; the result (including the ellipsis) never exceeds max_chars."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= len(ellipsis):
        return text[:max_chars]
    cut = text[: max_chars - len(ellipsis)]
    space = cut.rfind(" ")
    if space > max_chars // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + ellipsis


def normalise_whitespace(text: str) -> str:
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines()]
    out, blank = [], 0
    for line in lines:
        if line:
            out.append(line)
            blank = 0
        else:
            blank += 1
            if blank == 1 and out:
                out.append("")
    return "\n".join(out).strip()
