"""HTML to plain text (scripts, styles and comments are dropped; table cells are pipe-separated)."""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Optional

from .text import normalise_whitespace

_BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "thead", "tbody", "h1", "h2", "h3", "h4", "h5", "h6",
          "section", "article", "header", "footer", "blockquote", "pre", "hr", "title", "dl", "dt", "dd"}
_SKIP = {"script", "style", "noscript", "template", "head"}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title_parts = []
        self._skip = 0
        self._in_title = False
        self._cells_in_row = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag in _SKIP:
            self._skip += 1
            return
        if tag == "tr":
            self._cells_in_row = 0
        if tag in ("td", "th"):
            if self._cells_in_row:
                self.parts.append(" | ")
            self._cells_in_row += 1
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_startendtag(self, tag, attrs):
        if tag.lower() in ("br", "hr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Extractor()
    parser.feed(html)
    parser.close()
    return normalise_whitespace("".join(parser.parts))


def html_title(html: str) -> Optional[str]:
    parser = _Extractor()
    parser.feed(html)
    parser.close()
    title = " ".join("".join(parser.title_parts).split())
    return title or None
