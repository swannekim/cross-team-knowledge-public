"""Private retrieval index for the broker: pure-Python BM25 over chunks with metadata.

Production equivalent: an Azure AI Search index (hybrid BM25 + vector, semantic ranker) that only
the broker's managed identity can query, populated by the same app identity (Sites.Selected).
Security filters are applied *during* ranking (like an AI Search $filter), so filtered chunks never
consume top-k slots.

Ingestion minimisation: prompt-injection sentences are stripped and secrets, connection strings and
resident registration numbers are removed before anything is stored; e-mail addresses and phone
numbers are redacted at answer time by the broker (audience-dependent policy).
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

from common.chunker import chunk_text
from common.injection import neutralise
from common.redaction import redact
from common.refs import TEST_REF_KEY, opaque_ref
from common.text import stem, tokenize

from .constants import INDEX_CHUNK_MAX_CHARS

INGEST_REDACTION = ("SECRET", "CONNECTION_STRING", "KR_RRN")
# Domain synonym map applied to queries (production: an Azure AI Search synonym map on the index).
SYNONYMS = {
    "pm": ("preventive", "maintenance"), "preventive": ("pm",), "maintenance": ("pm",),
    "chamber": ("ch",), "ch": ("chamber",),
    "capa": ("corrective", "preventive", "action"), "corrective": ("capa",),
    "rca": ("root", "cause"), "litho": ("lithography",), "lithography": ("litho",),
}


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str          # internal source id (never returned to callers)
    ref: str             # opaque reference returned to callers
    title: str
    label: str
    path: str            # internal location (used only by the PDP scope check)
    kind: str
    chunk_no: int
    total_chunks: int
    text: str
    injection_flags: int
    chat_id: Optional[str] = None
    label_id: Optional[str] = None   # Purview sensitivity label GUID (returned as sensitivityLabelId)


def expand_query(query: str) -> set:
    terms = set(tokenize(query, stem_words=True))
    for term in list(terms):
        terms.update(stem(t) for t in SYNONYMS.get(term, ()))
    return terms


class BM25Index:
    """BM25 over chunk text; the document title acts as a document-level prior (weighted title match added to
    chunks that already match on content), so ranking inside a document is driven by the chunk content."""

    def __init__(self, k1: float = 1.5, b: float = 0.75, title_weight: float = 0.5):
        self.k1, self.b, self.title_weight = k1, b, title_weight
        self.chunks: list = []
        self._lengths: list = []
        self._postings: dict = defaultdict(list)
        self._by_doc: dict = defaultdict(list)
        self._titles: dict = {}
        self._idf: dict = {}
        self._title_idf: dict = {}
        self._avg_len = 1.0

    def __len__(self) -> int:
        return len(self.chunks)

    def add(self, chunk: Chunk) -> None:
        index = len(self.chunks)
        counts = Counter(tokenize(chunk.text, stem_words=True))
        self._titles.setdefault(chunk.doc_id, set(tokenize(chunk.title, stem_words=True)))
        self.chunks.append(chunk)
        self._lengths.append(sum(counts.values()))
        for term, tf in counts.items():
            self._postings[term].append((index, tf))
        self._by_doc[chunk.doc_id].append(chunk)

    def finalize(self) -> "BM25Index":
        n = max(1, len(self.chunks))
        self._avg_len = (sum(self._lengths) / n) or 1.0
        self._idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self._postings.items()}
        title_df = Counter(t for tokens in self._titles.values() for t in tokens)
        n_docs = max(1, len(self._titles))
        self._title_idf = {t: math.log(1 + (n_docs - df + 0.5) / (df + 0.5)) for t, df in title_df.items()}
        return self

    def search(self, query: str, top_k: int = 10, accept: Optional[Callable] = None) -> list:
        scores: dict = defaultdict(float)
        k1, b, avg = self.k1, self.b, self._avg_len
        for term in expand_query(query):
            idf = self._idf.get(term)
            if idf is None:
                continue
            for index, tf in self._postings[term]:
                norm = k1 * (1 - b + b * self._lengths[index] / avg)
                scores[index] += idf * tf * (k1 + 1) / (tf + norm)
        query_terms = expand_query(query)
        title_bonus: dict = {}
        for index in scores:
            doc_id = self.chunks[index].doc_id
            if doc_id not in title_bonus:
                title_bonus[doc_id] = self.title_weight * sum(self._title_idf.get(t, 0.0)
                                                              for t in query_terms & self._titles.get(doc_id, set()))
            scores[index] += title_bonus[doc_id]
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        out = []
        for index, score in ranked:
            chunk = self.chunks[index]
            if accept is not None and not accept(chunk):
                continue
            out.append((score, chunk))
            if len(out) >= top_k:
                break
        return out

    def doc_chunks(self, doc_id: str) -> list:
        return list(self._by_doc.get(doc_id, []))

    def doc_ids(self) -> list:
        return list(self._by_doc)


_INDEX_CACHE: dict = {}


def build_index(documents, *, chunk_max_chars: int = INDEX_CHUNK_MAX_CHARS, ref_key: bytes = TEST_REF_KEY) -> BM25Index:
    """Builds (or returns the per-process memoised) read-only index for exactly these document versions."""
    documents = list(documents)
    key = (tuple((d.source_id, d.fingerprint, d.label, d.label_id, d.path, d.kind, d.title, d.chat_id) for d in documents),
           chunk_max_chars, ref_key)
    if key not in _INDEX_CACHE:
        if len(_INDEX_CACHE) >= 8:
            _INDEX_CACHE.clear()
        _INDEX_CACHE[key] = _build(documents, chunk_max_chars, ref_key)
    return _INDEX_CACHE[key]


def _build(documents, chunk_max_chars: int, ref_key: bytes) -> BM25Index:
    index = BM25Index()
    for doc in documents:
        neutral = neutralise(doc.text)
        cleaned = redact(neutral.text, INGEST_REDACTION).text
        title = redact(neutralise(doc.title).text, INGEST_REDACTION).text
        lines = cleaned.lstrip().split("\n", 1)
        if lines and " ".join(lines[0].split()) == " ".join(title.split()):
            cleaned = lines[1] if len(lines) > 1 else ""
        pieces = chunk_text(cleaned, chunk_max_chars) if cleaned.strip() else [title]
        ref = opaque_ref(doc.source_id, ref_key)
        for n, piece in enumerate(pieces):
            index.add(Chunk(chunk_id=f"{ref}-c{n:05d}", doc_id=doc.source_id, ref=ref, title=title, label=doc.label,
                            path=doc.path, kind=doc.kind, chunk_no=n, total_chunks=len(pieces), text=piece,
                            injection_flags=len(neutral.flags), chat_id=doc.chat_id, label_id=doc.label_id))
    return index.finalize()
