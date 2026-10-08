"""Sensitivity label ordering (Microsoft Purview-style). Unknown labels fail closed."""
from __future__ import annotations

from typing import Optional

LABEL_ORDER = ("Public", "General", "Confidential", "Highly Confidential")
_RANK = {name.casefold(): rank for rank, name in enumerate(LABEL_ORDER)}
UNKNOWN_RANK = len(LABEL_ORDER)


def is_known_label(label: Optional[str]) -> bool:
    return isinstance(label, str) and label.strip().casefold() in _RANK


def canonical_label(label: str) -> str:
    if not is_known_label(label):
        raise ValueError(f"unknown sensitivity label: {label!r}")
    return LABEL_ORDER[_RANK[label.strip().casefold()]]


def label_rank(label: Optional[str]) -> int:
    """Rank of a label; unknown or missing labels rank above everything (fail closed)."""
    if not is_known_label(label):
        return UNKNOWN_RANK
    return _RANK[label.strip().casefold()]


def label_within(label: Optional[str], ceiling: str) -> bool:
    """True when `label` is known and not more sensitive than `ceiling`."""
    if not is_known_label(ceiling):
        raise ValueError(f"unknown label ceiling: {ceiling!r}")
    return is_known_label(label) and label_rank(label) <= label_rank(ceiling)
