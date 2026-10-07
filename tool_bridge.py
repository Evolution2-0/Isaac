"""
Bounded tool bridge: GitHub API, web fetch, Grok/Copilot agents, remote Isaac.

Opt-in via ISAAC_TOOL_BRIDGE_ENABLED=1 (default on when secrets present for tool).
Remote fleet: ISAAC_REMOTE_BRIDGE_ENABLED=1.
Registered as registry tools with kind=bridge.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

from secrets_bootstrap import resolve_secret

log = logging.getLogger("Isaac.ToolBridge")

BRIDGE_TOOLS: list[dict[str, Any]] = [
    {
        "tool_id": "bridge_github",
        "name": "github_api",
        "kind": "bridge",
        "category": "code",
        "description": "GitHub REST API (issues/PRs/repos) using GITHUB_TOKEN/GH_TOKEN",
        "priority": 80,
        "trust": 70.0,
        "metadata": {"bridge": "github"},
    },
    {
        "tool_id": "bridge_web_fetch",
        "name": "web_fetch",
        "kind": "bridge",
        "category": "suche",
        "description": "HTTP GET URL and return text excerpt (no auth)",
        "priority": 75,
        "trust": 65.0,
        "metadata": {"bridge": "web_fetch"},
    },
    {
        "tool_id": "bridge_grok_agent",
        "name": "grok_agent",
        "kind": "bridge",
        "category": "code",
        "description": "Run Grok Build CLI headless companion (ISAAC_GROK_AGENT_ENABLED)",
        "priority": 85,
        "trust": 75.0,
        "metadata": {"bridge": "grok_agent"},
    },
    {
        "tool_id": "bridge_copilot_agent",
        "name": "copilot_agent",
        "kind": "bridge",
        "category": "code",
        "description": "Run GitHub Copilot CLI/SDK/cloud companion (ISAAC_COPILOT_AGENT_ENABLED)",
        "priority": 86,
        "trust": 75.0,
        "metadata": {"bridge": "copilot_agent"},
    },
    {
        "tool_id": "bridge_browserbase",
        "name": "browserbase",
        "kind": "bridge",
        "category": "browser",
        "description": "Provision an optional managed Browserbase cloud browser session",
        "priority": 88,
        "trust": 72.0,
        "metadata": {"bridge": "browserbase"},
    },
    {
        "tool_id": "bridge_jules",
        "name": "jules",
        "kind": "bridge",
        "category": "code",
        "description": "Delegate an asynchronous coding task to Google Jules",
        "priority": 87,
        "trust": 74.0,
        "metadata": {"bridge": "jules"},
    },
    {
        "tool_id": "bridge_isaac_cloud",
        "name": "isaac_cloud",
        "kind": "bridge",
        "category": "code",
        "description": "Chat with remote Isaac free (ISAAC_REMOTE_BRIDGE_ENABLED)",
        "priority": 84,
        "trust": 72.0,
        "metadata": {"bridge": "isaac_cloud"},
    },
    {
        "tool_id": "bridge_isaac_fleet",
        "name": "isaac_fleet",
        "kind": "bridge",
        "category": "code",
        "description": "Health of remote Isaac fleet (ISAAC_REMOTE_BRIDGE_ENABLED)",
        "priority": 83,
        "trust": 70.0,
        "metadata": {"bridge": "isaac_fleet"},
    },
]


def bridge_enabled() -> bool:
    raw = (os.getenv("ISAAC_TOOL_BRIDGE_ENABLED") or "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def ensure_bridge_tools_registered() -> list[str]:
    """Idempotent register of bridge tools in the tool registry."""
    if not bridge_enabled():
        return []
    from tool_registry import get_tool_registry

    reg = get_tool_registry()
    added: list[str] = []
    for spec in BRIDGE_TOOLS:
        tid = spec["tool_id"]
        existing = reg.get(tid)
        if existing:
            # keep user overrides; ensure kind/metadata
            if existing.kind != "bridge":
                reg.update(tid, {"kind": "bridge", "metadata": dict(spec.get("metadata") or {})})
            continue
        reg.add(dict(spec))
        added.append(tid)
    if added:
        log.info("Tool bridge registered: %s", ", ".join(added))
    return added


def _extract_url(prompt: str) -> str:
    m = re.search(r"https?://[^\s<>\"']+", prompt or "")
    return m.group(0).rstrip(").,]") if m else ""


def _github_token() -> str:
    return (
        resolve_secret("GITHUB_TOKEN")
        or resolve_secret("GH_TOKEN")
        or resolve_secret("github.token")
        or ""
    ).strip()


async def run_bridge(bridge_id: str, prompt: str) -> dict[str, Any]:
    """Execute a named bridge tool. Returns result_contract-like dict."""
    bid = (bridge_id or "").strip().lower()
    prompt = (prompt or "").strip()
    if not bid:
        return {"ok": False, "error": "bridge id missing", "via": "bridge"}
    if bid == "github":
        return await _bridge_github(prompt)
    if bid in {"web_fetch", "webfetch", "fetch"}:
        return await _bridge_web_fetch(prompt)
    if bid in {"grok_agent", "grok"}:
        return await _bridge_grok_agent(prompt)
    if bid in {"copilot_agent", "copilot", "github_copilot"}:
        return await _bridge_copilot_agent(prompt)
    if bid in {"browserbase", "browser_base", "bb"}:
        return await _bridge_browserbase(prompt)
    if bid in {"jules", "jules_agent"}:
        return await _bridge_jules(prompt)
    if bid in {"isaac_cloud", "isaac_remote", "cloud", "isaac_free"}:
        return await _bridge_isaac_cloud(prompt)
    if bid in {"isaac_fleet", "fleet", "isaac_status"}:
        return await _bridge_isaac_fleet(prompt)
    return {"ok": False, "error": f"unknown bridge: {bid}", "via": "bridge"}


async def _bridge_github(prompt: str) -> dict[str, Any]:
    token = _github_token()
    if not token:
        return {
            "ok": False,
            "error": "GITHUB_TOKEN/GH_TOKEN missing (set env or secrets store)",
            "via": "bridge.github",
        }

    # Parse simple commands:
    #   github: repo owner/name
    #   github: issues owner/name
    #   github: prs owner/name
    #   github: me
    #   or free text with owner/repo
    text = prompt
    low = text.lower()
    for prefix in ("github:", "github_api:", "gh:"):
        if low.startswith(prefix):
            text = text[len(prefix) :].strip()
            low = text.lower()
            break

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Isaac-ToolBridge/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    timeout = aiohttp.ClientTimeout(total=25)

    async def get_json(url: str) -> tuple[int, Any]:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url, headers=headers) as res:
                try:
                    data = await res.json(content_type=None)
                except Exception:
                    data = {"raw": (await res.text())[:2000]}
                return res.status, data

    try:
        if low in {"me", "user", "whoami"} or "whoami" in low:
            status, data = await get_json("https://api.github.com/user")
            if status >= 400:
                return {"ok": False, "error": f"GitHub {status}: {data}", "via": "bridge.github"}
            summary = {
                "login": data.get("login"),
                "name": data.get("name"),
                "public_repos": data.get("public_repos"),
                "html_url": data.get("html_url"),
            }
            return {
                "ok": True,
                "output": json.dumps(summary, ensure_ascii=False, indent=2),
                "via": "bridge.github",
                "status_code": status,
            }

        # issues / prs / repo
        m = re.search(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", text)
        owner = m.group(1) if m else "hansheute88"
        repo = m.group(2) if m else "Isaac"

        if low.startswith("issues") or " issues" in low or low.startswith("issue"):
            url = f"https://api.github.com/repos/{owner}/{repo}/issues?state=open&per_page=10"
            status, data = await get_json(url)
            if status >= 400:
                return {"ok": False, "error": f"GitHub {status}", "via": "bridge.github", "status_code": status}
            lines = []
            for it in (data or [])[:10]:
                if "pull_request" in it:
                    continue
                lines.append(f"#{it.get('number')} {it.get('title')} ({it.get('html_url')})")
            body = "\n".join(lines) or "(keine offenen Issues)"
            return {"ok": True, "output": f"Issues {owner}/{repo}:\n{body}", "via": "bridge.github", "status_code": status}

        if low.startswith("pr") or "pull" in low or "prs" in low:
            url = f"https://api.github.com/repos/{owner}/{repo}/pulls?state=open&per_page=10"
            status, data = await get_json(url)
            if status >= 400:
                return {"ok": False, "error": f"GitHub {status}", "via": "bridge.github", "status_code": status}
            lines = [
                f"#{it.get('number')} {it.get('title')} ({it.get('html_url')})"
                for it in (data or [])[:10]
            ]
            body = "\n".join(lines) or "(keine offenen PRs)"
            return {"ok": True, "output": f"PRs {owner}/{repo}:\n{body}", "via": "bridge.github", "status_code": status}

        # default: repo metadata
        status, data = await get_json(f"https://api.github.com/repos/{owner}/{repo}")
        if status >= 400:
            return {
                "ok": False,
                "error": f"GitHub {status}: {data if isinstance(data, str) else data.get('message', data)}",
                "via": "bridge.github",
                "status_code": status,
            }
        summary = {
            "full_name": data.get("full_name"),
            "private": data.get("private"),
            "description": data.get("description"),
            "default_branch": data.get("default_branch"),
            "html_url": data.get("html_url"),
            "language": data.get("language"),
            "stargazers_count": data.get("stargazers_count"),
            "open_issues_count": data.get("open_issues_count"),
            "pushed_at": data.get("pushed_at"),
        }
        return {
            "ok": True,
            "output": json.dumps(summary, ensure_ascii=False, indent=2),
            "via": "bridge.github",
            "status_code": status,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "via": "bridge.github"}


async def _bridge_web_fetch(prompt: str) -> dict[str, Any]:
    url = _extract_url(prompt)
    if not url:
        # treat prompt as search query → duckduckgo html lite
        q = quote((prompt or "")[:200])
        url = f"https://duckduckgo.com/html/?q={q}"
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "invalid url", "via": "bridge.web_fetch"}

    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"User-Agent": "Isaac-ToolBridge/1.0 (+local)"}
    try:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url, headers=headers, allow_redirects=True) as res:
                text = await res.text(errors="replace")
                # strip crude tags for readability
                plain = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
                plain = re.sub(r"(?is)<style.*?>.*?</style>", " ", plain)
                plain = re.sub(r"(?s)<[^>]+>", " ", plain)
                plain = re.sub(r"\s+", " ", plain).strip()
                ok = res.status < 400
                return {
                    "ok": ok,
                    "output": plain[:4000],
                    "via": "bridge.web_fetch",
                    "status_code": res.status,
                    "url": str(res.url),
                }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "via": "bridge.web_fetch"}


async def _bridge_grok_agent(prompt: str) -> dict[str, Any]:
    try:
        from external_memory import get_external_memory_bridge
        from config import BASE_DIR

        bridge = get_external_memory_bridge()
        if not bridge.cfg.grok_agent_enabled:
            return {
                "ok": False,
                "error": "ISAAC_GROK_AGENT_ENABLED=0 — set flag to use grok bridge",
                "via": "bridge.grok_agent",
            }
        # strip optional prefix
        text = prompt
        low = text.lower()
        for p in ("grok:", "grok_agent:", "grok-agent:"):
            if low.startswith(p):
                text = text[len(p) :].strip()
                break
        result = bridge.grok_agent.run(text or prompt, cwd=str(BASE_DIR))
        return {
            "ok": bool(result.get("ok")),
            "output": (result.get("text") or result.get("error") or "")[:8000],
            "error": result.get("error") or "",
            "via": "bridge.grok_agent",
            "session_id": result.get("session_id") or "",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "via": "bridge.grok_agent"}


async def _bridge_copilot_agent(prompt: str) -> dict[str, Any]:
    try:
        from external_memory import get_external_memory_bridge
        from config import BASE_DIR

        bridge = get_external_memory_bridge()
        if not bridge.cfg.copilot_agent_enabled:
            return {
                "ok": False,
                "error": "ISAAC_COPILOT_AGENT_ENABLED=0 — set flag to use copilot bridge",
                "via": "bridge.copilot_agent",
            }
        text = prompt
        low = text.lower()
        mode = "cli"
        for p in (
            "copilot:",
            "copilot_agent:",
            "copilot-agent:",
            "gh-copilot:",
            "github-copilot:",
        ):
            if low.startswith(p):
                text = text[len(p) :].strip()
                low = text.lower()
                break
        for marker, mname in (
            ("cloud:", "cloud"),
            ("task:", "cloud"),
            ("sdk:", "sdk"),
            ("cli:", "cli"),
        ):
            if low.startswith(marker):
                mode = mname
                text = text[len(marker) :].strip()
                break
        result = bridge.copilot_agent.run(
            text or prompt, cwd=str(BASE_DIR), mode=mode
        )
        return {
            "ok": bool(result.get("ok")),
            "output": (result.get("text") or result.get("error") or "")[:8000],
            "error": result.get("error") or "",
            "via": "bridge.copilot_agent",
            "session_id": result.get("session_id") or "",
            "mode": result.get("via") or mode,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "via": "bridge.copilot_agent"}


async def _bridge_browserbase(prompt: str) -> dict[str, Any]:
    try:
        from integrations.browserbase import BrowserbaseAdapter
        adapter = BrowserbaseAdapter()
        result = await adapter.create_session(metadata={"source": "isaac", "request": prompt[:180]})
        return {
            **result,
            "output": (
                f"Browserbase session {result.get('session_id') or '(none)'}"
                if result.get("ok") else result.get("error", "")
            ),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "via": "bridge.browserbase"}


async def _bridge_jules(prompt: str) -> dict[str, Any]:
    try:
        from integrations.jules import JulesAdapter
        adapter = JulesAdapter()
        text = prompt
        low = text.lower()
        for prefix in ("jules:", "jules-agent:", "jules agent:"):
            if low.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        result = await adapter.create_session(text or prompt)
        return {
            **result,
            "output": (
                f"Jules session {result.get('session_id') or '(none)'}"
                if result.get("ok") else result.get("error", "")
            ),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "via": "bridge.jules"}


async def _bridge_isaac_cloud(prompt: str) -> dict[str, Any]:
    try:
        from isaac_remote import (
            chat_remote,
            format_remote_reply,
            remote_bridge_enabled,
            remote_base_url,
        )

        if not remote_bridge_enabled():
            return {
                "ok": False,
                "error": "ISAAC_REMOTE_BRIDGE_ENABLED=0 — set flag to use remote Isaac",
                "via": "bridge.isaac_cloud",
            }
        text = prompt
        low = text.lower()
        for p in ("cloud:", "free:", "render:", "isaac-cloud:", "isaac_cloud:"):
            if low.startswith(p):
                text = text[len(p) :].strip()
                break
        if (text or "").strip().lower() in {"status", "health", "fleet"}:
            return await _bridge_isaac_fleet(text)
        result = await chat_remote(text or prompt)
        return {
            "ok": bool(result.get("ok")),
            "output": format_remote_reply(result)[:8000],
            "error": result.get("error") or "",
            "via": "bridge.isaac_cloud",
            "url": result.get("url") or remote_base_url(),
            "ms": result.get("ms"),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "via": "bridge.isaac_cloud"}


async def _bridge_isaac_fleet(prompt: str) -> dict[str, Any]:
    try:
        from isaac_remote import (
            fleet_status,
            format_fleet_status,
            remote_bridge_enabled,
        )

        if not remote_bridge_enabled():
            return {
                "ok": False,
                "error": "ISAAC_REMOTE_BRIDGE_ENABLED=0",
                "via": "bridge.isaac_fleet",
            }
        st = fleet_status()
        return {
            "ok": bool((st.get("remote") or {}).get("ok")),
            "output": format_fleet_status(st),
            "via": "bridge.isaac_fleet",
            "status": st,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "via": "bridge.isaac_fleet"}


def status() -> dict[str, Any]:
    return {
        "enabled": bridge_enabled(),
        "tools": [t["tool_id"] for t in BRIDGE_TOOLS],
        "github_token": bool(_github_token()),
        "grok_flag": (os.getenv("ISAAC_GROK_AGENT_ENABLED") or "0").strip() in {"1", "true", "yes", "on"},
        "copilot_flag": (os.getenv("ISAAC_COPILOT_AGENT_ENABLED") or "0").strip()
        in {"1", "true", "yes", "on"},
        "remote_flag": (os.getenv("ISAAC_REMOTE_BRIDGE_ENABLED") or "0").strip()
        in {"1", "true", "yes", "on"},
    }
