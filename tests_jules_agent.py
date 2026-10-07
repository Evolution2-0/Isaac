import unittest


class JulesIntentTests(unittest.TestCase):
    def test_jules_prefix_routes_to_jules_agent(self):
        from isaac_core import Intent, detect_intent
        self.assertEqual(detect_intent("jules: prüfe den Kernel"), Intent.JULES_AGENT)
        self.assertEqual(detect_intent("jules-agent: schreibe Tests"), Intent.JULES_AGENT)


if __name__ == "__main__":
    unittest.main()
