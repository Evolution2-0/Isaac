#!/usr/bin/env python3
"""Compare local HEAD, git remotes (main), and Render live deploy.

Usage:
  python3 scripts/check_deploy_sync.py
  python3 scripts/check_deploy_sync.py --json
  python3 scripts/check_deploy_sync.py --fix  # exit 1 if out of sync

Env (optional):
  RENDER_API_KEY          — required for deploy commit check
  RENDER_SERVICE_ID       — default from data/cli_auth_backup or srv-d9cflunavr4c73b43br0
  ISAAC_REMOTE_FREE_URL   — health URL (default https://isaac-free.onrender.com)
  GITHUB_TOKEN / GH_TOKEN — optional, for private remote tips via API fallback

Remotes checked when present: hansheute88, sc0rp0815, sco0rp, glinka, origin (branch main).
Canonical deploy remote (preferred): hansheute88 → hansheute88/isaac
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERVICE = "srv-d9cflunavr4c73b43br0"
DEFAULT_HEALTH = "https://isaac-free.onrender.com"


def _run(cmd: list[str], *, cwd: Path = ROOT) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return p.returncode, (p.stdout or p.stderr or "").strip()
    except Exception as exc:
        return 1, str(exc)


def _git_rev(ref: str) -> Optional[str]:
    rc, out = _run(["git", "rev-parse", ref])
    if rc != 0 or not out or "unknown" in out.lower():
        return None
    return out.split()[0][:40]


def _git_log1(ref: str) -> str:
    rc, out = _run(["git", "log", "-1", "--oneline", ref])
    return out if rc == 0 else ""


def _load_dotenv_key(name: str) -> str:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return (os.getenv(name) or "").strip()
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return (os.getenv(name) or "").strip()


def _render_key() -> str:
    k = _load_dotenv_key("RENDER_API_KEY") or (os.getenv("RENDER_API_KEY") or "").strip()
    if k:
        return k
    p = ROOT / "data" / "cli_auth_backup" / "render" / "api_key.txt"
    if p.is_file():
        return p.read_text(encoding="utf-8").strip()
    return ""


def _service_id() -> str:
    sid = (
        _load_dotenv_key("RENDER_SERVICE_ID")
        or (os.getenv("RENDER_SERVICE_ID") or "").strip()
    )
    if sid:
        return sid
    p = ROOT / "data" / "cli_auth_backup" / "render" / "service_id.txt"
    if p.is_file():
        return p.read_text(encoding="utf-8").strip()
    return DEFAULT_SERVICE


def _http_json(url: str, *, headers: Optional[dict] = None, timeout: float = 25) -> tuple[int, Any]:
    req = Request(url, headers=headers or {"User-Agent": "Isaac-DeploySync/1.0", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, {"raw": raw[:300]}
    except HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", errors="replace")[:300]}
    except Exception as e:
        return 0, {"error": str(e)[:300]}


def render_live_deploy(service_id: str, api_key: str) -> dict[str, Any]:
    if not api_key:
        return {"ok": False, "error": "RENDER_API_KEY missing"}
    code, data = _http_json(
        f"https://api.render.com/v1/services/{service_id}/deploys?limit=10",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    if code != 200 or not isinstance(data, list):
        return {"ok": False, "error": f"deploys HTTP {code}", "body": data}
    live = None
    for item in data:
        d = item.get("deploy") if isinstance(item, dict) and "deploy" in item else item
        if not isinstance(d, dict):
            continue
        if d.get("status") == "live":
            live = d
            break
    if not live and data:
        d0 = data[0].get("deploy") if isinstance(data[0], dict) and "deploy" in data[0] else data[0]
        live = d0 if isinstance(d0, dict) else None
    if not live:
        return {"ok": False, "error": "no deploys"}
    commit = live.get("commit") or {}
    cid = ""
    if isinstance(commit, dict):
        cid = str(commit.get("id") or "")
    elif isinstance(commit, str):
        cid = commit
    return {
        "ok": True,
        "deploy_id": live.get("id"),
        "status": live.get("status"),
        "commit": cid[:40],
        "message": (commit.get("message") if isinstance(commit, dict) else "") or "",
        "trigger": live.get("trigger"),
        "finishedAt": live.get("finishedAt"),
    }


def healthz(url: str) -> dict[str, Any]:
    base = url.rstrip("/")
    code, data = _http_json(base + "/healthz")
    if code != 200:
        return {"ok": False, "url": base, "status_code": code, "error": data}
    if not isinstance(data, dict):
        return {"ok": False, "url": base, "error": "non-json"}
    return {
        "ok": bool(data.get("ok")),
        "url": base,
        "status_code": code,
        "git_commit": (data.get("git_commit") or data.get("commit") or "")[:40] or None,
        "active_provider": data.get("active_provider"),
        "has_groq_key": data.get("has_groq_key"),
        "has_gemini_key": data.get("has_gemini_key"),
        "has_openrouter_key": data.get("has_openrouter_key"),
        "free_cloud": data.get("free_cloud"),
    }


def collect_report() -> dict[str, Any]:
    # refresh remotes best-effort
    for remote in ("hansheute88", "sc0rp0815", "sco0rp", "glinka", "origin"):
        _run(["git", "fetch", remote, "main"])

    local_head = _git_rev("HEAD")
    local_branch_rc, local_branch = _run(["git", "branch", "--show-current"])
    branch = local_branch if local_branch_rc == 0 else ""

    # Primary remotes for deploy sync. Preferred canonical: hansheute88/isaac
    # (legacy sc0rp0815/Isaac, sco0rp/IsaacNew still checked when present).
    primary_names = ("hansheute88", "sc0rp0815", "sco0rp", "glinka")
    remotes: dict[str, Any] = {}
    for name in ("hansheute88", "sc0rp0815", "sco0rp", "glinka", "origin"):
        ref = f"{name}/main"
        sha = _git_rev(ref)
        if sha:
            remotes[name] = {
                "ref": ref,
                "sha": sha,
                "oneline": _git_log1(ref),
                "primary": name in primary_names,
            }

    service_id = _service_id()
    render = render_live_deploy(service_id, _render_key())
    health_url = (
        os.getenv("ISAAC_REMOTE_FREE_URL")
        or os.getenv("RENDER_URL")
        or DEFAULT_HEALTH
    ).strip()
    health = healthz(health_url)

    # compare — primary remotes should align; origin may be another fork
    primary_shas = {
        v["sha"] for n, v in remotes.items() if v.get("primary") and v.get("sha")
    }
    remotes_aligned = len(primary_shas) <= 1
    canonical = None
    for preferred in ("hansheute88", "sc0rp0815", "sco0rp", "glinka"):
        if preferred in remotes:
            canonical = remotes[preferred]["sha"]
            break
    if not canonical and local_head:
        canonical = local_head

    render_sha = render.get("commit") if render.get("ok") else None

    issues: list[str] = []
    if not remotes_aligned and len(primary_shas) > 1:
        issues.append("primary remotes (sc0rp0815/sco0rp/glinka) main tips diverge")
    if local_head and canonical and local_head != canonical and branch == "main":
        issues.append(f"local main HEAD {local_head[:7]} != remote main {canonical[:7]}")
    if render.get("ok") and canonical and render_sha:
        if render_sha[:12] != canonical[:12]:
            issues.append(
                f"Render live commit {render_sha[:7]} != repo main {canonical[:7]}"
            )
    if health.get("ok") is False:
        issues.append(f"healthz fail: {health.get('error') or health.get('status_code')}")
    elif health.get("ok") and not any(
        health.get(k) for k in ("has_groq_key", "has_gemini_key", "has_openrouter_key")
    ):
        issues.append("Render health: no LLM keys (has_*_key all false)")

    # IN_SYNC for deploy path: primary remotes + render + health keys
    in_sync = (
        remotes_aligned
        and bool(render.get("ok"))
        and bool(health.get("ok"))
        and not any("Render live" in i or "healthz" in i or "no LLM" in i for i in issues)
        and (
            not (canonical and render_sha)
            or render_sha[:12] == (canonical or "")[:12]
        )
    )

    return {
        "in_sync": in_sync,
        "issues": issues,
        "local": {
            "branch": branch,
            "head": local_head,
            "oneline": _git_log1("HEAD"),
        },
        "remotes_main": remotes,
        "canonical_main": canonical,
        "render": {
            "service_id": service_id,
            **render,
        },
        "health": health,
    }


def print_human(rep: dict[str, Any]) -> None:
    print("=== Isaac deploy sync ===")
    loc = rep.get("local") or {}
    print(f"local:  {loc.get('branch') or '?'} @ {(loc.get('head') or '?')[:12]}  {loc.get('oneline')}")
    for name, info in (rep.get("remotes_main") or {}).items():
        print(f"{name}/main: {(info.get('sha') or '?')[:12]}  {info.get('oneline')}")
    print(f"canonical main: {(rep.get('canonical_main') or '?')[:12]}")
    ren = rep.get("render") or {}
    if ren.get("ok"):
        print(
            f"render: live {(ren.get('commit') or '?')[:12]}  "
            f"status={ren.get('status')} trigger={ren.get('trigger')}  "
            f"msg={(ren.get('message') or '')[:60]}"
        )
    else:
        print(f"render: FAIL {ren.get('error')}")
    h = rep.get("health") or {}
    if h.get("ok"):
        print(
            f"health: {h.get('url')} ok provider={h.get('active_provider')} "
            f"keys g/g/o={h.get('has_groq_key')}/{h.get('has_gemini_key')}/{h.get('has_openrouter_key')} "
            f"git_commit={h.get('git_commit') or 'n/a'}"
        )
    else:
        print(f"health: FAIL {h.get('error') or h.get('status_code')}")
    if rep.get("issues"):
        print("issues:")
        for i in rep["issues"]:
            print(f"  - {i}")
    print("RESULT:", "IN_SYNC" if rep.get("in_sync") else "OUT_OF_SYNC")


def main() -> int:
    ap = argparse.ArgumentParser(description="Check local/repos/Render deploy sync")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--fail",
        "--strict",
        dest="fail",
        action="store_true",
        help="exit 1 if out of sync",
    )
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="R2: after sync check, run full remote smoke against Free URL",
    )
    ap.add_argument(
        "--smoke-wait",
        action="store_true",
        help="R2: wait until health git_commit matches local HEAD, then full smoke",
    )
    args = ap.parse_args()
    rep = collect_report()
    if args.json and not (args.smoke or args.smoke_wait):
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print_human(rep)
    if args.fail and not rep.get("in_sync"):
        return 1

    if args.smoke or args.smoke_wait:
        # Import after report so smoke failures don't hide sync output
        sys.path.insert(0, str(ROOT))
        import asyncio

        from remote_smoke import format_report, run_full_smoke, run_post_deploy_smoke

        local = (rep.get("local") or {}).get("commit") or _git_rev("HEAD") or ""
        if args.smoke_wait and local:
            print(f"\n--- Post-deploy smoke (wait for {local[:12]}) ---")
            smoke = asyncio.run(run_post_deploy_smoke(local, timeout_s=600.0))
        else:
            print("\n--- Remote full smoke ---")
            smoke = asyncio.run(run_full_smoke(write_memory=True))
        print(format_report(smoke))
        if not smoke.get("ok"):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
