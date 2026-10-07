import os
import unittest
from unittest.mock import AsyncMock, patch


class IntegrationConfigTests(unittest.TestCase):
    def test_browserbase_is_off_by_default(self):
        from integrations.browserbase import BrowserbaseAdapter
        with patch.dict(os.environ, {}, clear=True):
            adapter = BrowserbaseAdapter()
            self.assertFalse(adapter.available())

    def test_jules_is_off_by_default(self):
        from integrations.jules import JulesAdapter
        with patch.dict(os.environ, {}, clear=True):
            adapter = JulesAdapter()
            self.assertFalse(adapter.available())

    def test_jules_requires_repo(self):
        from integrations.jules import JulesAdapter
        with patch.dict(
            os.environ,
            {"ISAAC_JULES_ENABLED": "1", "JULES_API_KEY": "test-key"},
            clear=True,
        ):
            adapter = JulesAdapter()
            self.assertTrue(adapter.available())


if __name__ == "__main__":
    unittest.main()
