"""Optional Browserbase cloud-browser integration.

Browserbase complements Isaac's existing Playwright browser manager with a
managed Chromium session. It is intentionally opt-in and only provisions a
session; page actions remain behind Isaac's normal browser/constitution gates.

Required:
  ISAAC_BROWSERBASE_ENABLED=1
  BROWSERBASE_API_KEY=<secret>

Optional:
  BROWSERBASE_PROJECT_ID=<project>
  ISAAC_BROWSERBASE_TIMEOUT=30
"""
from __future__ import annotations

import os
from typing import Any

import aiohttp


class BrowserbaseAdapter:
    name = "browserbase"

    def __init__(self) -> None:
        self.enabled = (os.getenv("ISAAC_BROWSERBASE_ENABLED", "0").lower()
                        in {"1", "true", "yes", "on"})
        self.api_key = (os.getenv("BROWSERBASE_API_KEY") or "").strip()
        self.project_id = (os.getenv("BROWSERBASE_PROJECT_ID") or "").strip()
        try:
            self.timeout_s = max(5.0, min(120.0, float(
                os.getenv("ISAAC_BROWSERBASE_TIMEOUT", "30")
            )))
        except ValueError:
            self.timeout_s = 30.0

    def available(self) -> bool:
        return bool(self.enabled and self.api_key)

    async def create_session(self, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.available():
            return {
                "ok": False,
                "error": "Browserbase disabled or BROWSERBASE_API_KEY missing",
                "via": "browserbase",
            }

        # Browserbase's REST surface is kept behind this adapter so the rest of
        # Isaac does not depend on vendor-specific HTTP details.
        payload: dict[str, Any] = {}
        if self.project_id:
            payload["projectId"] = self.project_id
        if metadata:
            payload["userMetadata"] = {
                str(k): str(v)[:200] for k, v in metadata.items()
            }

        headers = {
            "x-bb-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        url = "https://api.browserbase.com/v1/sessions"

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as response:
                    data = await response.json(content_type=None)
                    if response.status >= 400:
                        return {
                            "ok": False,
                            "error": f"Browserbase HTTP {response.status}",
                            "detail": str(data)[:1000],
                            "via": "browserbase",
                        }
                    return {
                        "ok": True,
                        "session_id": data.get("id", ""),
                        "connect_url": data.get("connectUrl", ""),
                        "status": data.get("status", ""),
                        "via": "browserbase",
                    }
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:500], "via": "browserbase"}

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "available": self.available(),
            "project_configured": bool(self.project_id),
        }
