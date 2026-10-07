"""Optional Google Jules coding-agent integration.

Jules is an asynchronous coding companion for repositories connected through
the Jules GitHub app. Isaac uses it as a bounded companion, not as an
autonomous replacement for Isaac's governance layer.

Required:
  ISAAC_JULES_ENABLED=1
  JULES_API_KEY=<secret>

Optional:
  JULES_REPO=owner/repo
  JULES_BRANCH=main
  ISAAC_JULES_TIMEOUT=30
"""
from __future__ import annotations

import os
from typing import Any

import aiohttp


class JulesAdapter:
    name = "jules"

    def __init__(self) -> None:
        self.enabled = (os.getenv("ISAAC_JULES_ENABLED", "0").lower()
                        in {"1", "true", "yes", "on"})
        self.api_key = (os.getenv("JULES_API_KEY") or "").strip()
        self.repo = (os.getenv("JULES_REPO") or "").strip()
        self.branch = (os.getenv("JULES_BRANCH") or "main").strip() or "main"
        try:
            self.timeout_s = max(5.0, min(120.0, float(
                os.getenv("ISAAC_JULES_TIMEOUT", "30")
            )))
        except ValueError:
            self.timeout_s = 30.0

    def available(self) -> bool:
        return bool(self.enabled and self.api_key)

    async def create_session(
        self,
        prompt: str,
        *,
        repo: str = "",
        branch: str = "",
        auto_create_pr: bool = False,
        require_plan_approval: bool = True,
    ) -> dict[str, Any]:
        if not self.available():
            return {
                "ok": False,
                "error": "Jules disabled or JULES_API_KEY missing",
                "via": "jules",
            }
        prompt = (prompt or "").strip()
        source_repo = (repo or self.repo).strip()
        if not prompt:
            return {"ok": False, "error": "empty prompt", "via": "jules"}
        if not source_repo:
            return {
                "ok": False,
                "error": "JULES_REPO missing; Jules must be given a connected GitHub repo",
                "via": "jules",
            }
        if "/" not in source_repo:
            return {"ok": False, "error": "JULES_REPO must be owner/repo", "via": "jules"}

        owner, repo_name = source_repo.split("/", 1)
        source = f"sources/github/{owner}/{repo_name}"
        payload = {
            "prompt": prompt[:12000],
            "sourceContext": {
                "source": source,
                "githubRepoContext": {
                    "startingBranch": (branch or self.branch).strip() or "main",
                },
            },
            "requirePlanApproval": bool(require_plan_approval),
            "title": "Isaac delegated coding task",
        }
        if auto_create_pr:
            payload["automationMode"] = "AUTO_CREATE_PR"
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        url = "https://jules.googleapis.com/v1alpha/sessions"

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as response:
                    data = await response.json(content_type=None)
                    if response.status >= 400:
                        return {
                            "ok": False,
                            "error": f"Jules HTTP {response.status}",
                            "detail": str(data)[:1200],
                            "via": "jules",
                        }
                    return {
                        "ok": True,
                        "session_id": data.get("id", ""),
                        "name": data.get("name", ""),
                        "url": data.get("url", ""),
                        "state": data.get("state", ""),
                        "via": "jules",
                    }
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:500], "via": "jules"}

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "available": self.available(),
            "repo_configured": bool(self.repo),
            "branch": self.branch,
        }
