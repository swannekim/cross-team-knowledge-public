"""Simple extractive summariser (term-frequency sentence scoring) plus key-fact and excerpt selection.

Production note: the production pipeline would call Azure OpenAI (e.g. a GPT-4o-class deployment)
with the redacted, injection-neutralised text wrapped as untrusted data (see
common.injection.wrap_untrusted), using map-reduce for long documents, and would evaluate output
with groundedness/safety checks. The extractive version keeps the prototype deterministic and
offline: every summary sentence is verbatim, already-redacted source text.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Optional

from .text import split_sentences, tokenize, truncate

_HAS_DIGIT = re.compile(r"\d")
_ALPHA_WORD = re.compile(r"^[A-Za-z\uac00-\ud7a3][A-Za-z\uac00-\ud7a3'-]*[.,;:!?)]?$")
_REDACTION = re.compile(r"\[(?:REDACTED|REMOVED):[A-Z_]+\]")


def _prose_ratio(sentence: str) -> float:
    words = sentence.split()
    if not words:
        return 0.0
    return sum(1 for w in words if _ALPHA_WORD.match(w)) / len(words)


def _sample(text: str, max_input_chars: int) -> str:
    """Evenly samples paragraphs from very long inputs so the whole document is represented."""
    if len(text) <= max_input_chars:
        return text
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    total = sum(len(p) for p in paragraphs) or 1
    keep_every = max(1, math.ceil(total / max_input_chars))
    sampled, size = [], 0
    for i, paragraph in enumerate(paragraphs):
        if i % keep_every == 0 and size + len(paragraph) <= max_input_chars:
            sampled.append(paragraph)
            size += len(paragraph)
    return "\n\n".join(sampled)


_LIST_MARKER = re.compile(r"^\s*(?:[-*\u2022]|\d{1,2}[.)])\s+")


def _norm(sentence: str) -> str:
    return " ".join(_REDACTION.sub(" ", sentence).split()).casefold()


def _candidates(text: str, min_prose: float, exclude=()) -> list:
    out, seen = [], {_norm(e) for e in exclude}
    for position, sentence in enumerate(split_sentences(text)):
        sentence = _LIST_MARKER.sub("", sentence).strip()
        clean = _REDACTION.sub(" ", sentence)
        words = clean.split()
        key = " ".join(words).casefold()
        if key in seen:
            continue
        seen.add(key)
        if 4 <= len(words) <= 80 and _prose_ratio(clean) >= min_prose:
            out.append((position, sentence))
    return out


def _scores(candidates: list, query_terms: Optional[set] = None) -> list:
    tf = Counter()
    tokenised = []
    for _, sentence in candidates:
        tokens = [t for t in tokenize(sentence, stem_words=True) if len(t) > 1]
        tokenised.append(tokens)
        tf.update(set(tokens))
    peak = max(tf.values()) if tf else 1
    scored = []
    for (position, sentence), tokens in zip(candidates, tokenised):
        unique = set(tokens)
        if not unique:
            continue
        score = sum(tf[t] / peak for t in unique) / math.sqrt(len(unique))
        if query_terms:
            score += 3.0 * len(unique & query_terms)
        score += 0.15 / (1 + position)
        scored.append((score, position, sentence))
    return scored


def summarize(text: str, max_sentences: int = 3, max_chars: int = 600, max_input_chars: int = 200_000,
              exclude=()) -> str:
    """Top-scoring sentences in document order; `exclude` drops e.g. the title line."""
    sample = _sample(text, max_input_chars)
    candidates = _candidates(sample, min_prose=0.5, exclude=exclude)
    if not candidates:
        return truncate(" ".join(sample.split()), max_chars)
    best = sorted(_scores(candidates), key=lambda s: (-s[0], s[1]))[:max_sentences]
    chosen, size = [], 0
    for _, position, sentence in sorted(best, key=lambda s: s[1]):
        if size + len(sentence) + 1 > max_chars:
            if not chosen:
                chosen.append(truncate(sentence, max_chars))
            break
        chosen.append(sentence)
        size += len(sentence) + 1
    return " ".join(chosen)


def key_facts(text: str, max_facts: int = 5, max_chars_each: int = 180, max_input_chars: int = 200_000,
              exclude=()) -> list:
    """Sentences that carry numbers (dimensions, yields, dates, counts) ranked by term salience."""
    sample = _sample(text, max_input_chars)
    candidates = [(p, s) for p, s in _candidates(sample, min_prose=0.4, exclude=exclude)
                  if _HAS_DIGIT.search(_REDACTION.sub("", s))]
    best = sorted(_scores(candidates), key=lambda s: (-s[0], s[1]))[:max_facts]
    seen, facts = set(), []
    for _, _, sentence in sorted(best, key=lambda s: s[1]):
        fact = truncate(sentence, max_chars_each)
        if fact not in seen:
            seen.add(fact)
            facts.append(fact)
    return facts


def select_excerpt(text: str, max_chars: int, query: Optional[str] = None, exclude=()) -> str:
    """Verbatim excerpt of at most max_chars.

    Without a query: the most salient contiguous run of sentences. With a query: the sentences that best
    match the query (stemmed term overlap), kept in document order and joined with ' … '.
    """
    sentences = split_sentences(text)
    if not sentences:
        return ""
    query_terms = set(tokenize(query, stem_words=True)) if query else set()
    excluded = {_norm(e) for e in exclude}
    scored = _scores([(i, s) for i, s in enumerate(sentences) if _norm(s) not in excluded], query_terms or None)
    if not scored:
        return truncate(" ".join(text.split()), max_chars)
    if query_terms:
        # A matching short heading ("Daily calibration") hands its relevance to the sentences that follow it.
        by_position = {position: [score, position, sentence] for score, position, sentence in scored}
        for position, sentence in enumerate(sentences):
            if position in by_position and len(sentence.split()) <= 4 and \
                    set(tokenize(sentence, stem_words=True)) & query_terms:
                bonus = by_position.pop(position)[0]
                for follower in (position + 1, position + 2):
                    if follower in by_position:
                        by_position[follower][0] += bonus
        scored = [tuple(entry) for entry in by_position.values()]
        headed = {p + d for p, s in enumerate(sentences) for d in (1, 2)
                  if len(s.split()) <= 4 and set(tokenize(s, stem_words=True)) & query_terms}
        matching = [entry for entry in scored
                    if set(tokenize(entry[2], stem_words=True)) & query_terms or entry[1] in headed]
        ranked = sorted(matching or scored, key=lambda s: (-s[0], s[1]))
        chosen, size = [], 0
        for _, position, sentence in ranked:
            extra = len(sentence) + (3 if chosen else 0)
            if not chosen and len(sentence) > max_chars:
                return truncate(sentence, max_chars)
            if size + extra <= max_chars:
                chosen.append((position, sentence))
                size += extra
            if len(chosen) == 3:
                break
        return " … ".join(sentence for _, sentence in sorted(chosen))
    _, start, _ = max(scored, key=lambda s: (s[0], -s[1]))
    excerpt = sentences[start]
    if len(excerpt) > max_chars:
        return truncate(excerpt, max_chars)
    nxt = start + 1
    while (nxt < len(sentences) and len(sentences[nxt].split()) >= 4
           and len(excerpt) + 1 + len(sentences[nxt]) <= max_chars):
        excerpt = excerpt + " " + sentences[nxt]
        nxt += 1
    return excerpt
