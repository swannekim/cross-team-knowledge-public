"""Small unittest helpers (scratch directories inside the prototype, offline network guard)."""
from __future__ import annotations

import unittest

from . import netguard
from .paths import new_scratch_dir, remove_scratch_dir


class OfflineTestCase(unittest.TestCase):
    """Blocks non-loopback network access for the duration of each test class."""

    @classmethod
    def setUpClass(cls):
        netguard.install()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        netguard.uninstall()

    def scratch(self, prefix: str = "test"):
        path = new_scratch_dir(prefix)
        self.addCleanup(remove_scratch_dir, path)
        return path
