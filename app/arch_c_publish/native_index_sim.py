"""Simulated native Microsoft 365 (Copilot / Microsoft Search) index over the Knowledge Exchange site.

SharePoint content is security-trimmed by its own permissions: only members of the site's member
groups (grp-team-b) can retrieve cards, and guests are blocked because the site does not allow guest
access. The simulation reads the live site (no crawl latency; production indexing is near-real-time
but not instantaneous, so deletions can take a short while to disappear from results).
"""
from __future__ import annotations

import math
from collections import Counter

from common.directory import Directory
from common.text import tokenize

from .derivative import parse_front_matter
from .publisher import KnowledgeExchangeSite


class NativeIndexSim:
    def __init__(self, site: KnowledgeExchangeSite, directory: Directory):
        self.site, self.directory = site, directory

    def search(self, user_key: str, query: str, top: int = 5) -> list:
        user = self.directory.find_user(user_key)
        if not self.site.can_read(user):
            return []
        docs = []
        for item in self.site.items():
            content = self.site.read(item["id"])
            docs.append((item, content, Counter(tokenize(content, stem_words=True))))
        if not docs:
            return []
        df = Counter()
        for _, _, counts in docs:
            df.update(counts.keys())
        terms = set(tokenize(query, stem_words=True))
        hits = []
        for item, content, counts in docs:
            score = sum((1 + math.log(counts[t])) * math.log(1 + len(docs) / df[t]) for t in terms if counts.get(t))
            if score > 0:
                meta = parse_front_matter(content)
                hits.append({"title": item["fields"]["Title"], "webUrl": item["webUrl"], "score": round(score, 6),
                             "fields": dict(item["fields"]), "sourceRef": meta.get("sourceRef"), "content": content})
        hits.sort(key=lambda h: (-h["score"], h["title"]))
        return hits[:top]
