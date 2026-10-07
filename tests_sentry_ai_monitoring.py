"""Regression: Sentry AI monitoring helpers stay no-op without DSN."""

from __future__ import annotations

import os
import unittest
from unittest import mock


class TestSentryAiMonitoringNoOp(unittest.TestCase):
    def test_before_send_drops_keyboardinterrupt_by_type(self):
        import isaac_sentry as mod

        event = {
            "exception": {"values": [{"type": "KeyboardInterrupt", "value": ""}]},
            "title": "KeyboardInterrupt",
        }
        self.assertIsNone(mod._before_send(event, {}))
        # hint.exc_info path
        self.assertIsNone(
            mod._before_send({"message": "x"}, {"exc_info": (KeyboardInterrupt, KeyboardInterrupt(), None)})
        )
        # real bug must pass through
        kept = mod._before_send(
            {"exception": {"values": [{"type": "ValueError", "value": "boom"}]}},
            {},
        )
        self.assertIsNotNone(kept)

    def test_before_send_drops_invalidupgrade_noise(self):
        import isaac_sentry as mod

        self.assertIsNone(
            mod._before_send(
                {"message": "InvalidUpgrade: missing Connection header"},
                {},
            )
        )

    def test_before_send_drops_ollama_noise_when_free_cloud(self):
        import isaac_sentry as mod

        with mock.patch.dict(os.environ, {"ISAAC_FREE_CLOUD": "1", "ACTIVE_PROVIDER": "openrouter"}, clear=False):
            self.assertIsNone(
                mod._before_send(
                    {"message": "Ollama nicht erreichbar. Starte mit: ollama serve"},
                    {},
                )
            )
            self.assertIsNone(
                mod._before_send(
                    {"message": "Ollama timeout during chat"},
                    {},
                )
            )

    def test_init_without_dsn_is_disabled(self):
        import isaac_sentry as mod

        with mock.patch.dict(os.environ, {"SENTRY_DSN": ""}, clear=False):
            # Force re-init path
            mod._initialized = False
            mod._session_conversation_id = None
            self.assertFalse(mod.init_sentry())
            self.assertFalse(mod.is_enabled())

    def test_spans_are_safe_when_disabled(self):
        import isaac_sentry as mod

        mod._initialized = False
        with mod.gen_ai_chat_span(model="gpt-test", provider="groq", prompt="hi") as span:
            mod.finish_chat_span(
                span,
                result_text="hello",
                input_tokens=3,
                output_tokens=2,
                total_tokens=5,
            )
        with mod.invoke_agent_span(user_input="Hallo") as span:
            mod.finish_agent_span(span, result_text="Hi", model="local")
        with mod.execute_tool_span("search", arguments={"q": "x"}) as span:
            mod.finish_tool_span(span, result={"ok": True})

    def test_message_json_shapes(self):
        import isaac_sentry as mod
        import json

        raw = mod._messages_json("sys", "user-text")
        data = json.loads(raw)
        self.assertEqual(data[0]["role"], "system")
        self.assertEqual(data[1]["role"], "user")
        self.assertIn("parts", data[0])
        out = json.loads(mod._output_json("antwort"))
        self.assertEqual(out[0]["role"], "assistant")


class TestToolRuntimeExecuteToolSpan(unittest.IsolatedAsyncioTestCase):
    async def test_run_selected_tool_wraps_execution_with_span(self):
        """run_selected_tool must complete and call finish_tool_span when Sentry helpers load."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from tool_runtime import run_selected_tool

        selection = {
            "source": "registry",
            "name": "search_web",
            "identifier": "registry:search_web",
            "kind": "http",
            "category": "suche",
            "tool": {"id": "search_web", "name": "search_web"},
        }
        fake_result = {"ok": True, "output": "Berlin 12°C", "via": "registry"}

        span = MagicMock()
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=span)
        cm.__exit__ = MagicMock(return_value=False)
        finish = MagicMock()

        with patch("isaac_sentry.execute_tool_span", return_value=cm) as exec_span, patch(
            "isaac_sentry.finish_tool_span", finish
        ), patch(
            "tool_runtime._run_selected_tool_body",
            new=AsyncMock(return_value=fake_result),
        ) as body:
            out = await run_selected_tool(selection, "Suche: Wetter Berlin", skip_constitution=True)

        self.assertEqual(out, fake_result)
        body.assert_awaited_once()
        exec_span.assert_called_once()
        self.assertEqual(exec_span.call_args[0][0], "search_web")
        finish.assert_called_once()
        self.assertIs(finish.call_args[0][0], span)
        payload = finish.call_args[0][1]
        self.assertTrue(payload["ok"])
        self.assertIn("Berlin", payload["output"] or "")

    async def test_run_selected_tool_works_when_sentry_import_fails(self):
        from unittest.mock import AsyncMock, patch

        from tool_runtime import run_selected_tool

        selection = {
            "source": "registry",
            "name": "x",
            "identifier": "registry:x",
            "tool": {},
        }
        fake_result = {"ok": False, "error": "boom", "via": "registry"}

        with patch.dict("sys.modules", {"isaac_sentry": None}), patch(
            "tool_runtime._run_selected_tool_body",
            new=AsyncMock(return_value=fake_result),
        ):
            # Import inside run_selected_tool may raise — still returns body result
            out = await run_selected_tool(selection, "x", skip_constitution=True)
        self.assertEqual(out, fake_result)


if __name__ == "__main__":
    unittest.main()
