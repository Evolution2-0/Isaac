from __future__ import annotations
import json
import os

os.environ["ISAAC_PRIVILEGE_MODE"] = "user"

import tempfile
import unittest
from pathlib import Path

from state_io import atomic_write_json, load_json_or_recover
from task_tool_state import TaskToolStateStore


class StateIoTests(unittest.TestCase):
    def test_atomic_write_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state.json"
            atomic_write_json(target, {"ok": True}, mode=0o600)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"ok": True})
            mode = os.stat(target).st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_load_json_or_recover_with_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "broken.json"
            target.write_text("{invalid-json", encoding="utf-8")
            value = load_json_or_recover(target, fallback_factory=list, context="test")
            self.assertEqual(value, [])

    def test_task_tool_state_store_skips_invalid_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "task_state.json"
            target.write_text(json.dumps([
                {"task_id": "ok-1", "status": "idle"},
                {"task_id": "", "status": "invalid"},
                {"task_id": "broken", "tool_history": ["bad"]},
                "not-a-dict"
            ]), encoding="utf-8")
            store = TaskToolStateStore(path=target)
            exported = store.export_for_tasks()
            self.assertIn("ok-1", exported)
            self.assertNotIn("", exported)


if __name__ == "__main__":
    unittest.main()
