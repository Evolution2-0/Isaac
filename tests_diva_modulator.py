from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from modulator import DivaModulator, get_modulator
import memory
from decision_trace import DecisionTrace, TracePhase


class TestDivaModulator(unittest.TestCase):

    def setUp(self):
        self.mod = DivaModulator(decay_rate=0.1)

    def test_initial_state(self):
        state = self.mod.get_all()
        self.assertEqual(state["uncertainty"], 0.0)
        self.assertEqual(state["caution"], 0.0)
        self.assertEqual(state["curiosity"], 0.0)

    def test_raise_for_diva(self):
        # uncertainty=0.8, relevance=0.5 -> boost = 0.4
        state = self.mod.raise_for_diva(0.8, 0.5, reason="unclear provenance")
        self.assertAlmostEqual(state["uncertainty"], 0.4, places=2)
        self.assertAlmostEqual(state["caution"], 0.32, places=2)
        self.assertAlmostEqual(state["curiosity"], 0.24, places=2)

    def test_raise_for_diva_clamped(self):
        # Exceed max 1.0
        self.mod.raise_for_diva(1.0, 1.0, reason="max boost 1")
        state = self.mod.raise_for_diva(1.0, 1.0, reason="max boost 2")
        self.assertEqual(state["uncertainty"], 1.0)
        self.assertEqual(state["caution"], 1.0)
        self.assertEqual(state["curiosity"], 1.0)

    def test_decay(self):
        self.mod.raise_for_diva(1.0, 1.0, reason="test decay")
        # Explicit decay by 5 seconds at 0.1 rate = -0.5
        state = self.mod.decay(dt=5.0)
        self.assertAlmostEqual(state["uncertainty"], 0.5, places=2)
        self.assertAlmostEqual(state["caution"], 0.3, places=2)
        self.assertAlmostEqual(state["curiosity"], 0.1, places=2)

    def test_resolve_provenance(self):
        self.mod.raise_for_diva(1.0, 1.0, reason="test resolve")
        state = self.mod.resolve_provenance(reason="provenance verified")
        self.assertLess(state["uncertainty"], 0.2)
        self.assertLess(state["caution"], 0.2)
        self.assertLess(state["curiosity"], 0.2)

    def test_memory_retrieval_integration(self):
        global_mod = get_modulator()
        global_mod.raise_for_diva(0.9, 0.9, reason="retrieval test")

        mem = memory.get_memory()
        ctx = mem.build_retrieval_context("test query")

        self.assertIn("modulator_state", ctx.as_dict())
        self.assertGreater(ctx.modulator_state.get("uncertainty", 0.0), 0.5)

        # Cleanup global modulator
        global_mod.resolve_provenance("cleanup")

    def test_decision_trace_integration(self):
        trace = DecisionTrace()
        mod_state = {"uncertainty": 0.7, "caution": 0.5, "curiosity": 0.3}
        trace.add(
            TracePhase.STRATEGY,
            "diva_modulator_state",
            {"modulators": mod_state},
        )
        export = trace.to_list()
        self.assertEqual(len(export), 1)
        self.assertEqual(export[0]["event"], "diva_modulator_state")
        self.assertEqual(export[0]["data"]["modulators"]["uncertainty"], 0.7)


if __name__ == "__main__":
    unittest.main()
