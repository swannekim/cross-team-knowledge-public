"""Contract-driven policy gate shared by all architectures (scope, label ceiling, excluded paths, type)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional

from .contract import SharingContract
from .labels import is_known_label

SUPPORTED_EXTENSIONS = (".txt", ".md", ".html", ".htm")


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str


class PolicyGate:
    def __init__(self, contract: SharingContract):
        self.contract = contract

    def _label_and_type(self, label: Optional[str], name: str) -> Optional[GateDecision]:
        if label is None:
            return GateDecision(False, "label_missing")
        if not is_known_label(label):
            return GateDecision(False, "label_unknown")
        if not self.contract.label_allowed(label):
            return GateDecision(False, "label_above_ceiling")
        if PurePosixPath(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            return GateDecision(False, "unsupported_type")
        return None

    def check_file(self, *, path: str, label: Optional[str], name: str) -> GateDecision:
        in_scope, reason = self.contract.path_in_scope(path)
        if not in_scope:
            return GateDecision(False, reason)
        return self._label_and_type(label, name) or GateDecision(True, "in_scope")

    def check_chat(self, chat_id: str) -> GateDecision:
        source = self.contract.chat_source(chat_id)
        if source is None:
            return GateDecision(False, "chat_not_in_contract")
        if not self.contract.label_allowed(source.get("label", "Confidential")):
            return GateDecision(False, "label_above_ceiling")
        return GateDecision(True, "in_scope")

    def check_chat_file(self, chat_id: str, *, label: Optional[str], name: str) -> GateDecision:
        source = self.contract.chat_source(chat_id)
        if source is None:
            return GateDecision(False, "chat_not_in_contract")
        if not source.get("includeChatFiles", False):
            return GateDecision(False, "chat_files_not_in_contract")
        return self._label_and_type(label, name) or GateDecision(True, "in_scope")
