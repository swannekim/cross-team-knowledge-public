"""Pattern-based redaction of personal data and secrets.

Replaces matches with tokens such as ``[REDACTED:EMAIL]``. Categories:
EMAIL, KR_MOBILE, KR_RRN (Korean resident registration number), SECRET (API keys, tokens,
passwords, private keys), CONNECTION_STRING and INTERNAL_URL (links into SharePoint/OneDrive,
which would otherwise point Team B back at the originals).

Each detector has a cheap prefilter (substring / digit-shape checks) so multi-megabyte inputs are
processed quickly; detection (find_sensitive) and redaction share the same detectors.

Production note: complement (not replace) this with Microsoft Purview sensitive information
types / DLP and Azure AI Language PII detection; regexes are a deterministic first line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ALL_CATEGORIES = ("SECRET", "CONNECTION_STRING", "INTERNAL_URL", "EMAIL", "KR_RRN", "KR_MOBILE")

_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}=*")
_KNOWN_KEY_FORMATS = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[abprs]-[A-Za-z0-9-]{10,}|(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{10,})\b")
_KV_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|apikey|x-api-key|client[_-]?secret|secret[_-]?key|secret|access[_-]?key|access[_-]?token|"
    r"auth[_-]?token|refresh[_-]?token|sas[_-]?token|token|password|passwd|pwd|private[_-]?key)"
    r"([ \t]*[:=][ \t]*)([\"']?)([^\s\"',;]{6,})\3")
_CONN_KEY = r"(?:Data Source|Initial Catalog|User ID|Integrated Security|Persist Security Info|[A-Za-z][A-Za-z0-9_.-]{0,40})"
_CONNECTION_STRING = re.compile(rf"(?i)(?:\b{_CONN_KEY}\s*=\s*[^;\r\n]*;[ \t]*){{2,}}(?:\b{_CONN_KEY}\s*=\s*[^;\r\n]*)?")
_CREDENTIAL_KEYS = re.compile(r"(?i)\b(?:password|pwd|accountkey|sharedaccesskey|sharedaccesssignature|secret|token)\s*=")
_INTERNAL_URL = re.compile(r"(?i)\bhttps?://[a-z0-9-]+\.sharepoint\.com(?:/[^\s<>\"')\]]*[^\s<>\"')\].,;:!?])?")
_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}\b")
_KR_RRN = re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[- ]?[1-8]\d{6}(?!\d)")
# Literal-prefixed mobile patterns let the regex engine use its fast prefix scan; the
# "not preceded by a digit or hyphen" condition is checked in Python (see _accept_mobile).
_KR_MOBILE_LOCAL = re.compile(r"01[016789][-. ]?\d{3,4}[-. ]?\d{4}(?![\d-])")
_KR_MOBILE_INTL = re.compile(r"\+82[-. ]?1[016789][-. ]?\d{3,4}[-. ]?\d{4}(?![\d-])")

_REDACTION_TOKEN = re.compile(r"\[REDACTED:[A-Z_]+\]")
_DIGIT_SHAPE = str.maketrans("0123456789", "9999999999")
_RRN_SHAPES = ("999999-9999999", "999999 9999999", "9999999999999")
_SECRET_HINTS = ("key", "secret", "token", "pass", "pwd")
_KEY_FORMAT_HINTS = ("AKIA", "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "xoxa-", "xoxb-", "xoxp-", "xoxr-", "xoxs-",
                     "_live_", "_test_")


def token(category: str) -> str:
    return f"[REDACTED:{category}]"


def _accept_any(_match) -> bool:
    return True


def _accept_mobile(match) -> bool:
    start = match.start()
    return not (start and (match.string[start - 1].isdigit() or match.string[start - 1] == "-"))


def _accept_connection_string(match) -> bool:
    return _CREDENTIAL_KEYS.search(match.group(0)) is not None


def _accept_kv(match) -> bool:
    return not _REDACTION_TOKEN.fullmatch(match.group(4))


@dataclass(frozen=True)
class _Detector:
    category: str
    pattern: re.Pattern
    applies: object                 # (text, lower) -> bool : whole-text prefilter
    line_filter: object = None      # optional (line) -> bool : only matching lines are scanned
    accept: object = _accept_any    # (match) -> bool
    keep_key: bool = False          # SECRET key=value: keep the key, replace only the value


_DETECTORS = (
    _Detector("SECRET", _PRIVATE_KEY, lambda t, lo: "-----BEGIN" in t),
    _Detector("SECRET", _JWT, lambda t, lo: "eyJ" in t),
    _Detector("SECRET", _BEARER, lambda t, lo: "bearer" in lo),
    _Detector("SECRET", _KNOWN_KEY_FORMATS, lambda t, lo: any(h in t for h in _KEY_FORMAT_HINTS)),
    _Detector("CONNECTION_STRING", _CONNECTION_STRING, lambda t, lo: ";" in t,
              line_filter=lambda ln: ";" in ln and "=" in ln, accept=_accept_connection_string),
    _Detector("SECRET", _KV_SECRET, lambda t, lo: any(h in lo for h in _SECRET_HINTS),
              line_filter=lambda ln: (":" in ln or "=" in ln) and any(h in ln.lower() for h in _SECRET_HINTS),
              accept=_accept_kv, keep_key=True),
    _Detector("INTERNAL_URL", _INTERNAL_URL, lambda t, lo: "sharepoint" in lo),
    _Detector("EMAIL", _EMAIL, lambda t, lo: "@" in t, line_filter=lambda ln: "@" in ln),
    _Detector("KR_RRN", _KR_RRN, lambda t, lo: any(s in t.translate(_DIGIT_SHAPE) for s in _RRN_SHAPES)),
    _Detector("KR_MOBILE", _KR_MOBILE_LOCAL, lambda t, lo: "01" in t, accept=_accept_mobile),
    _Detector("KR_MOBILE", _KR_MOBILE_INTL, lambda t, lo: "+82" in t, accept=_accept_mobile),
)


def _scan(detector: _Detector, text: str, on_match) -> str:
    """Applies `on_match(match) -> replacement` for accepted matches; returns the (possibly) new text."""
    def repl(match):
        return on_match(match) if detector.accept(match) else match.group(0)
    if detector.line_filter is None:
        return detector.pattern.sub(repl, text)
    lines = text.split("\n")
    return "\n".join(detector.pattern.sub(repl, ln) if detector.line_filter(ln) else ln for ln in lines)


@dataclass
class RedactionResult:
    text: str
    counts: dict = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


class Redactor:
    def __init__(self, categories=ALL_CATEGORIES):
        unknown = set(categories) - set(ALL_CATEGORIES)
        if unknown:
            raise ValueError(f"unknown redaction categories: {sorted(unknown)}")
        self.categories = tuple(c for c in ALL_CATEGORIES if c in categories)

    def redact(self, text: str) -> RedactionResult:
        counts: dict = {}
        lower = text.lower()  # prefilters are conservative hints; tokens never introduce new matches
        for detector in _DETECTORS:
            if detector.category not in self.categories or not detector.applies(text, lower):
                continue

            def replace(match, detector=detector):
                counts[detector.category] = counts.get(detector.category, 0) + 1
                if detector.keep_key:
                    return f"{match.group(1)}{match.group(2)}{token(detector.category)}"
                return token(detector.category)
            text = _scan(detector, text, replace)
        return RedactionResult(text=text, counts=counts)


_DEFAULT = Redactor()


def redact(text: str, categories=None) -> RedactionResult:
    return (_DEFAULT if categories is None else Redactor(categories)).redact(text)


def find_sensitive(text: str) -> list:
    """Detection only: (category, matched_text) for every sensitive value still present."""
    findings = []
    stripped = _REDACTION_TOKEN.sub(" ", text)
    lower = stripped.lower()
    for detector in _DETECTORS:
        if not detector.applies(stripped, lower):
            continue

        def record(match, detector=detector):
            findings.append((detector.category, match.group(0)))
            return match.group(0)
        _scan(detector, stripped, record)
    return findings
