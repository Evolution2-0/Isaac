from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from diva_protocol import (
    DIVAProtocol,
    DIVAEvent,
    ProvenanceChain,
    ProvenanceStatus,
    ModulatorSystem,
    ConnectionMap,
)
from memory import Memory, EpistemicClass
from decision_trace import DecisionTrace


class TestDIVAProtocol(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp_dir.name) / "test_diva_state.json"
        self.memory = Memory()
        self.diva = DIVAProtocol(state_path=self.state_path, memory=self.memory)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_step_1_2_provenance_and_trigger(self):
        # 1. Complete provenance -> should NOT trigger DIVA
        prov_complete = ProvenanceChain(status=ProvenanceStatus.COMPLETE, confidence=1.0)
        evt1 = self.diva.evaluate_and_trigger("Known fact", prov_complete, 0.8)
        self.assertIsNone(evt1)

        # 2. Low behavioral relevance -> should NOT trigger DIVA
        prov_unknown = ProvenanceChain(status=ProvenanceStatus.UNKNOWN, confidence=0.1)
        evt2 = self.diva.evaluate_and_trigger("Uncertain fact", prov_unknown, 0.1)
        self.assertIsNone(evt2)

        # 3. Incomplete provenance & high relevance -> SHOULD trigger DIVA
        evt3 = self.diva.evaluate_and_trigger("Critical unverified fact", prov_unknown, 0.9)
        self.assertIsNotNone(evt3)
        self.assertTrue(evt3.event_id.startswith("diva_"))
        self.assertIn(evt3.event_id, self.diva.active_events)

    def test_step_3_memory_coupling(self):
        prov = ProvenanceChain(status=ProvenanceStatus.PARTIAL, confidence=0.4)
        evt = self.diva.evaluate_and_trigger("Ambiguous API endpoint", prov, 0.85)
        self.assertIsNotNone(evt)

        # Check epistemic memory entry creation
        ep_entry = self.memory.get_epistemic_memory(f"diva:{evt.event_id}")
        self.assertIsNotNone(ep_entry)
        self.assertEqual(ep_entry.epistemic_class, EpistemicClass.UNCERTAINTY_EVENT)
        self.assertIn("Ambiguous API endpoint", ep_entry.value)

    def test_step_4_connection_map(self):
        cm = ConnectionMap()
        cm.add_node("n1", "event", {"info": "test1"})
        cm.add_node("n2", "memory", {"info": "test2"})
        cm.add_edge("n1", "n2", weight=0.5)

        self.assertIn("n1->n2", cm.edges)
        self.assertEqual(cm.edges["n1->n2"], 0.5)

        cm.boost_connections("n1", boost_factor=0.3)
        self.assertAlmostEqual(cm.edges["n1->n2"], 0.8)

        associated = cm.get_associated_nodes("n1")
        self.assertIn("n2", associated)

    def test_step_5_modulators_and_persistence(self):
        mod = ModulatorSystem()
        self.assertEqual(mod.uncertainty_arousal, 0.0)

        mod.raise_for_diva(uncertainty_score=0.9, behavioral_relevance=0.8)
        self.assertGreater(mod.uncertainty_arousal, 0.0)
        self.assertGreater(mod.urgency_modifier, 0.0)
        self.assertGreater(mod.memory_bias_strength, 0.0)

        prev_arousal = mod.uncertainty_arousal
        mod.decay(turns=1, accelerated=False)
        self.assertLess(mod.uncertainty_arousal, prev_arousal)

    def test_step_6_7_resolution_and_decay(self):
        prov = ProvenanceChain(status=ProvenanceStatus.UNKNOWN)
        evt = self.diva.evaluate_and_trigger("Uncertain DB port", prov, 0.9)
        self.assertIsNotNone(evt)

        arousal_before = self.diva.modulators.uncertainty_arousal
        resolved = self.diva.resolve_event(evt.event_id, reason="User confirmed port 5432")
        self.assertTrue(resolved)

        self.assertNotIn(evt.event_id, self.diva.active_events)
        self.assertIn(evt.event_id, self.diva.resolved_events)
        self.assertLess(self.diva.modulators.uncertainty_arousal, arousal_before)

    def test_step_8_observability_and_metrics(self):
        dt = DecisionTrace()
        prov = ProvenanceChain(status=ProvenanceStatus.PARTIAL, confidence=0.3)
        evt = self.diva.evaluate_and_trigger("Observability info", prov, 0.9, decision_trace=dt)

        self.assertEqual(len(dt.entries), 1)
        self.assertEqual(dt.entries[0].event, "diva_event_triggered")

        self.diva.resolve_event(evt.event_id, "Verified by tool", decision_trace=dt)
        self.assertEqual(len(dt.entries), 2)
        self.assertEqual(dt.entries[1].event, "diva_event_resolved")

        metrics = self.diva.get_metrics()
        self.assertIn("active_events_count", metrics)
        self.assertIn("resolved_events_count", metrics)
        self.assertIn("modulator_values", metrics)


if __name__ == "__main__":
    unittest.main()
