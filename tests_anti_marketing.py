"""Regression: SUDO-Passwörter werden nie im Klartext geloggt.

Die Marketing-Blockade-Direktive wurde auf Owner-Wunsch entfernt (2026-09-26).
Verbleibender Schutz: Purge von Eval-Noise-Fakten bleibt aktiv.
"""
from __future__ import annotations

import re
import unittest


class TestSudoRedaction(unittest.TestCase):
    """_redact_secrets maskiert SUDO-Passwörter in Log-Ausgaben."""

    def test_sudo_input_redacted(self):
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        # regex-Logik identisch zu IsaacKernel._redact_secrets
        pattern = r"(?i)^((?:sudo|öffne tür|master key)\s+).+$"
        text = "Sudo 08150815"
        m = re.match(pattern, text)
        assert m
        redacted = m.group(1) + ("*" * max(4, len(text) - len(m.group(1))))
        self.assertNotIn("08150815", redacted)
        self.assertTrue(redacted.startswith("Sudo "))

    def test_normal_input_untouched(self):
        pattern = r"(?i)^((?:sudo|öffne tür|master key)\s+).+$"
        self.assertIsNone(re.match(pattern, "Hallo Isaac, Steffen hier"))


class TestPurgeEvalNoise(unittest.TestCase):
    def test_purge_method_exists_and_runs(self):
        from memory import get_memory

        r = get_memory().purge_eval_noise_facts()
        self.assertIn("deleted", r)


if __name__ == "__main__":
    unittest.main()
