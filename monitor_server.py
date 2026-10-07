"""
Isaac – Monitor Server v2.0
=============================
Fixes:
  - Callbacks vollständig thread-safe via asyncio.ensure_future
  - Empathie-Status im Dashboard
  - Blacklist-Status im Provider-Tab
  - Watchdog-Status in Metriken
  - _on_task_update und _on_audit_event laufen sicher im Loop
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import logging
from typing import Optional

import websockets
from websockets.server import WebSocketServerProtocol
from pathlib import Path
import socket

from config   import get_config, Level
from privilege import get_gate, steffen_ctx
from audit    import AuditLog
from task_tool_state import get_task_tool_state_store
from memory   import get_memory
from executor import get_executor, TaskType, TaskStatus
from relay    import get_relay
from logic    import get_logic
from free_cloud import (
    apply_free_cloud_defaults,
    bind_host,
    free_cloud_enabled,
    free_hosting_status,
    http_port as free_http_port,
    unified_port_enabled,
)

log = logging.getLogger("Isaac.Monitor")
_kernel_ref = None

def set_kernel(kernel):
    global _kernel_ref
    _kernel_ref = kernel


class _AioWsAdapter:
    """Adapter so aiohttp WebSocketResponse works with MonitorServer._send/broadcast."""

    def __init__(self, ws, remote=None):
        self._ws = ws
        self.remote_address = remote or ("aiohttp", 0)

    async def send(self, data: str):
        await self._ws.send_str(data)


def _is_port_in_use(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except Exception:
        return False


def is_localhost_request(remote: str) -> bool:
    """Dashboard-Resume/Checkpoints nur von localhost (Phase 3.4.3)."""
    return (remote or "").strip() in {"127.0.0.1", "::1", "localhost", ""}


def _request_json(request):
    async def _inner():
        try:
            return await request.json()
        except Exception:
            return {}
    return _inner()


def _public_ws_host(request, mon):
    env_host = os.getenv("MONITOR_WS_HOST_PUBLIC", "").strip()
    if env_host:
        return env_host
    try:
        req_host = request.host.split(":", 1)[0].strip()
    except Exception:
        req_host = ""
    if req_host and req_host not in {"localhost", "127.0.0.1", "::1"}:
        return req_host
    host = mon.bound_host or req_host or get_config().monitor.host
    if host in {"localhost", "::1", "0.0.0.0", ""}:
        return "127.0.0.1"
    return host


class MonitorServer:
    def __init__(self):
        self.cfg      = get_config()
        self.clients: set[WebSocketServerProtocol] = set()
        self.executor = get_executor()
        self.relay    = get_relay()
        self.memory   = get_memory()
        self.logic    = get_logic()
        self.gate     = get_gate()
        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._push:   Optional[asyncio.Task] = None
        self.bound_host = self.cfg.monitor.host
        self.bound_port = self.cfg.monitor.port

        # Callbacks: werden aufgerufen aus sync Executor-Code
        # → müssen thread-safe sein
        self.executor.register_callback(self._on_task_update_sync)
        AuditLog.register_callback(self._on_audit_event_sync)

        log.info(f"MonitorServer v2.0 │ ws://{self.cfg.monitor.host}:{self.cfg.monitor.port}")

    async def start(self):
        self._loop = asyncio.get_running_loop()
        if self._push is None or self._push.done():
            self._push = asyncio.create_task(self._metric_pusher())

        # Free-cloud / single-port: WebSocket läuft über aiohttp /ws (Dashboard)
        if unified_port_enabled():
            host = bind_host(self.cfg.monitor.host)
            port = free_http_port(self.cfg.monitor.http_port)
            self.bound_host = host
            self.bound_port = port
            log.info(
                "WS unified-mode: wss?/ws auf HTTP-Port %s (host=%s free_cloud=%s)",
                port,
                host,
                free_cloud_enabled(),
            )
            await asyncio.Future()
            return

        host = bind_host(self.cfg.monitor.host)
        start_port = int(os.getenv("MONITOR_PORT", str(self.cfg.monitor.port)))
        last_error = None

        for offset in range(0, 10):
            port = start_port + offset
            try:
                async with websockets.serve(
                    self._handle_client,
                    host,
                    port,
                    max_size=10 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=30,
                ):
                    self.bound_host = host
                    self.bound_port = port
                    if offset:
                        log.warning(f"WS-Port {start_port} belegt → Fallback auf {port}")
                    log.info(f"WS aktiv: ws://{host}:{port}")
                    await asyncio.Future()
            except OSError as e:
                last_error = e
                if e.errno == 98 or "address already in use" in str(e).lower():
                    continue
                raise

        raise last_error or RuntimeError("Kein freier WS-Port gefunden")

    async def handle_aiohttp_ws(self, request):
        """Same protocol as websockets server, on aiohttp (free single-port hosts)."""
        from aiohttp import web

        ws = web.WebSocketResponse(max_msg_size=10 * 1024 * 1024)
        await ws.prepare(request)
        adapter = _AioWsAdapter(ws, remote=request.remote)
        self.clients.add(adapter)
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        if self._push is None or self._push.done():
            self._push = asyncio.create_task(self._metric_pusher())
        log.info("Client (aiohttp/ws): %s", request.remote)

        await self._send(adapter, {"typ": "init", "state": self._build_state()})
        await self._send(adapter, {"typ": "tasks", "tasks": self.executor.all_tasks(100)})
        await self._send(adapter, {"typ": "audit", "entries": AuditLog.recent(60)})

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        await self._handle_msg(adapter, json.loads(msg.data))
                    except json.JSONDecodeError:
                        await self._send(adapter, {"typ": "fehler", "msg": "Ungültiges JSON"})
                    except Exception as e:
                        log.error(f"Message-Handler: {e}")
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                    break
        finally:
            self.clients.discard(adapter)
        return ws

    # ── Callbacks (sync, thread-safe) ─────────────────────────────────────────
    def _on_task_update_sync(self, task_dict: dict):
        """
        Wird vom Executor synchron aufgerufen.
        Leitet sicher in den Event Loop weiter.
        """
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"typ": "task_update", "task": task_dict}),
                self._loop
            )

    def _on_audit_event_sync(self, entry: dict):
        """Wie _on_task_update_sync — thread-safe."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"typ": "audit_event", "entry": entry}),
                self._loop
            )

    # ── Client-Handler ─────────────────────────────────────────────────────────
    async def _handle_client(self, ws: WebSocketServerProtocol):
        path = getattr(ws, "path", "/")
        self.clients.add(ws)
        log.info(f"Client: {ws.remote_address}")

        await self._send(ws, {"typ": "init", "state": self._build_state()})
        await self._send(ws, {"typ": "tasks", "tasks": self.executor.all_tasks(100)})
        await self._send(ws, {"typ": "audit", "entries": AuditLog.recent(60)})

        try:
            async for raw in ws:
                try:
                    await self._handle_msg(ws, json.loads(raw))
                except json.JSONDecodeError:
                    await self._send(ws, {"typ": "fehler", "msg": "Ungültiges JSON"})
                except Exception as e:
                    log.error(f"Message-Handler: {e}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)

    async def _handle_msg(self, ws: WebSocketServerProtocol, msg: dict):
        t = msg.get("typ", "")
        if   t == "chat":             await self._handle_chat(ws, msg)
        elif t == "task_cancel":      await self._handle_cancel(ws, msg)
        elif t == "directive_add":    await self._handle_directive_add(ws, msg)
        elif t == "directive_revoke": await self._handle_directive_revoke(ws, msg)
        elif t == "fact_set":
            self.memory.set_fact(msg.get("key",""), msg.get("value",""), source="Steffen")
            await self._send(ws, {"typ": "ok", "msg": "Fakt gespeichert"})
        elif t == "pause":
            self.gate.pause(steffen_ctx("Dashboard-Pause"))
            await self._broadcast({"typ": "system", "event": "paused"})
        elif t == "resume":
            self.gate.resume(steffen_ctx("Dashboard-Resume"))
            await self._broadcast({"typ": "system", "event": "resumed"})
        elif t == "state_request":
            await self._send(ws, {"typ": "state", "state": self._build_state()})
        elif t == "tasks_request":
            await self._send(ws, {"typ": "tasks", "tasks": self.executor.all_tasks(200)})
        elif t == "audit_request":
            await self._send(ws, {"typ": "audit", "entries": AuditLog.recent(msg.get("n", 100))})
        elif t == "ping":
            await self._send(ws, {"typ": "pong", "ts": time.strftime("%H:%M:%S")})

    async def _handle_chat(self, ws, msg: dict):
        text = msg.get("text", "").strip()
        if not text: return
        AuditLog.steffen_input(text)
        if _kernel_ref:
            asyncio.create_task(self._process_input(ws, text))
        else:
            await self._send(ws, {"typ": "chat_response",
                                  "text": "[Monitor] Kernel nicht bereit",
                                  "ts": time.strftime("%H:%M:%S")})

    async def _process_input(self, ws, text: str):
        t0 = time.perf_counter()
        try:
            t_kernel_0 = time.perf_counter()
            antwort = await _kernel_ref.process(text)
            kernel_ms = round((time.perf_counter() - t_kernel_0) * 1000, 2)
            AuditLog.isaac_output(antwort)
            t_send_0 = time.perf_counter()
            await self._send(ws, {"typ": "chat_response",
                                  "text": antwort,
                                  "ts":   time.strftime("%H:%M:%S")})
            send_ms = round((time.perf_counter() - t_send_0) * 1000, 2)
            total_ms = round((time.perf_counter() - t0) * 1000, 2)
            log.info("Latency(chat_ws) | total=%sms kernel=%sms send=%sms", total_ms, kernel_ms, send_ms)
        except Exception as e:
            await self._send(ws, {"typ": "chat_response",
                                  "text": f"[Fehler] {e}",
                                  "ts":   time.strftime("%H:%M:%S")})

    async def _handle_cancel(self, ws, msg: dict):
        tid  = msg.get("task_id", "")
        task = self.executor.get_task(tid)
        if task:
            task.status = TaskStatus.CANCELLED
            task.log("Dashboard: abgebrochen")
            await self._send(ws, {"typ": "ok", "msg": f"Task {tid} abgebrochen"})
            self.executor._notify(task)

    async def _handle_directive_add(self, ws, msg: dict):
        text = msg.get("text", "")
        pri  = int(msg.get("priority", 10))
        if not text: return
        d = self.gate.add_directive(text, pri)
        self.memory.save_directive(d.id, d.text, d.priority)
        await self._send(ws, {"typ": "ok", "msg": f"Direktive {d.id} gesetzt"})
        await self._broadcast({"typ": "directives",
                               "directives": self._get_directives()})

    async def _handle_directive_revoke(self, ws, msg: dict):
        did = msg.get("id", "")
        self.gate.revoke_directive(did)
        self.memory.revoke_directive(did)
        await self._send(ws, {"typ": "ok", "msg": f"Direktive {did} widerrufen"})
        await self._broadcast({"typ": "directives",
                               "directives": self._get_directives()})

    # ── State ──────────────────────────────────────────────────────────────────
    def _build_state(self) -> dict:
        # Empathie und Watchdog lazy laden
        empathie_status = {}
        watchdog_status = {}
        blacklist_status = []
        try:
            from empathie import get_empathie
            empathie_status = get_empathie().status_dict()
        except Exception: pass
        try:
            from watchdog import get_watchdog, get_blacklist
            watchdog_status  = get_watchdog().stats()
            blacklist_status = get_blacklist().all_stats()
        except Exception: pass

        provider_configs = get_config().list_provider_configs()

        tools_status = []
        browser_catalog = []
        try:
            from tool_registry import get_tool_registry
            tools_status = get_tool_registry().list_tools()
        except Exception:
            pass
        try:
            from browser import get_browser
            browser_catalog = get_browser().site_catalog()
        except Exception:
            pass

        neural_status = {}
        try:
            from neural_core import get_neural_cortex
            neural_status = get_neural_cortex().stats()
        except Exception:
            pass

        governance_status = {}
        try:
            from constitution import get_constitution
            from self_model import get_self_model
            governance_status = {
                "constitution_version": get_constitution().version(),
                "self_model_phase": get_self_model().summary().get("phase", "unknown"),
                "development_recent": len(self.memory.recent_development_events(8)),
                "procedures_total": int(self.memory.stats().get("procedures", 0)),
                "decay": self.memory.decay_stats(),
            }
        except Exception:
            pass

        settings = {
            "owner_name": getattr(self.cfg, "owner_name", "Steffen"),
            "active_provider": getattr(getattr(self.cfg, "relay", None), "primary_provider", ""),
            "monitor_host": self.bound_host,
            "monitor_port": self.bound_port,
            "monitor_push_interval": getattr(getattr(self.cfg, "monitor", None), "push_interval", None),
            "dashboard_port": getattr(get_dashboard(), "bound_port", None),
            "memory_max_working": getattr(getattr(self.cfg, "memory", None), "max_working_memory", None),
            "memory_max_facts": getattr(getattr(self.cfg, "memory", None), "max_facts", None),
            "browser_headless": getattr(getattr(self.cfg, "browser", None), "headless", None),
            "browser_max_instances": getattr(getattr(self.cfg, "browser", None), "max_instances", None),
            "filesystem_full_access": bool(getattr(self.cfg, "filesystem_full_access", False)),
            "browser_automation": bool(getattr(self.cfg, "browser_automation", False)),
            "browser_external_sites": bool(getattr(self.cfg, "browser_external_sites", False)),
            "auto_provision_providers": bool(getattr(self.cfg, "auto_provision_providers", True)),
            "auto_provision_all_providers": bool(getattr(self.cfg, "auto_provision_all_providers", True)),
            "free_only_providers": bool(getattr(self.cfg, "free_only_providers", False)),
            "multi_tool_mode": bool(getattr(self.cfg, "multi_tool_mode", False)),
        }

        now_snap: dict = {}
        goals_extra: dict = {}
        try:
            from monitor_now import build_now_snapshot, build_goals_missions_extras

            bg = {}
            try:
                if self.kernel and getattr(self.kernel, "_background", None):
                    bg = self.kernel._background.status() or {}
            except Exception:
                pass
            now_snap = build_now_snapshot(
                executor=self.executor,
                gate=self.gate,
                provider=str(settings.get("active_provider") or ""),
                background=bg,
            )
            goals_extra = build_goals_missions_extras()
        except Exception as exc:
            log.debug("NOW snapshot skipped: %s", exc)
            now_snap = {
                "headline": "NOW nicht verfügbar",
                "subline": str(exc)[:80],
                "pipeline_phase": "idle",
                "phases": {},
                "updated_ts": time.time(),
            }

        return {
            "ts":          time.strftime("%H:%M:%S"),
            "paused":      self.gate.is_paused,
            "memory":      self.memory.stats(),
            "executor":    self.executor.stats(),
            "logic":       self.logic.stats(),
            "audit":       AuditLog.stats(),
            "providers":   self.relay.provider_status(),
            "tools":       tools_status,
            "browser_catalog": browser_catalog,
            "provider_configs": provider_configs,
            "settings":    settings,
            "gate":        self.gate.status_dict(),
            "running":     self.executor.running_tasks(),
            "directives":  self._get_directives(),
            "open_questions": self._get_open_questions(),
            "regelwerk":   self._get_regelwerk_status(),
            "empathie":    empathie_status,
            "watchdog":    watchdog_status,
            "blacklist":   blacklist_status,
            "neural":      neural_status,
            "governance":  governance_status,
            "now":         now_snap,
            "goals":       goals_extra.get("goals") or {},
            "missions":    goals_extra.get("missions") or {},
            "remote_smoke": goals_extra.get("remote_smoke") or {},
            "background":  (
                (now_snap.get("background") if isinstance(now_snap, dict) else None)
                or {}
            ),
        }

    def _get_directives(self) -> list[dict]:
        return [{"id": d.id, "text": d.text, "priority": d.priority}
                for d in self.gate.active_directives()]

    def _get_open_questions(self) -> list[dict]:
        try:
            from regelwerk import get_regelwerk
            return get_regelwerk().open_questions_dict()
        except Exception:
            return []

    def _get_regelwerk_status(self) -> dict:
        try:
            from regelwerk import get_regelwerk
            return get_regelwerk().status()
        except Exception:
            return {}

    async def _metric_pusher(self):
        interval = self.cfg.monitor.push_interval
        while True:
            await asyncio.sleep(interval)
            if self.clients:
                await self._broadcast({"typ": "state", "state": self._build_state()})

    # ── Senden ────────────────────────────────────────────────────────────────
    async def _send(self, ws, data: dict):
        try:
            await ws.send(json.dumps(data, ensure_ascii=False, default=str))
        except Exception:
            pass

    async def _broadcast(self, data: dict):
        if not self.clients: return
        payload = json.dumps(data, ensure_ascii=False, default=str)
        dead    = set()
        for ws in list(self.clients):
            try:
                await ws.send(payload)
            except Exception:
                dead.add(ws)
        self.clients -= dead


# ── HTTP Dashboard ─────────────────────────────────────────────────────────────
class DashboardHTTPServer:
    def __init__(self, port: int = 8766):
        # PORT (PaaS) > MONITOR_HTTP_PORT > DASHBOARD_PORT > constructor default
        self.port      = free_http_port(int(os.getenv("MONITOR_HTTP_PORT", str(port)) or port))
        self.bound_port = None
        self.bound_host = None
        self.html_path = Path(__file__).parent / "dashboard.html"

    async def start(self):
        try:
            from aiohttp import web
            from tool_registry import get_tool_registry
            from secrets_store import get_secrets_store
            from tool_runtime import select_tool_for_prompt
            from tool_catalog import (
                list_local_tool_catalog,
                registry_payload_from_catalog,
                free_starter_pack_catalog_ids,
                list_tool_bundles,
                bundle_catalog_ids,
            )
            from mcp_registry import get_mcp_registry
            from updater import list_packages, inspect_package, apply_package, rollback_last_backup, status as updater_status, schedule_process_restart

            registry = get_tool_registry()
            secrets = get_secrets_store()
            mcp = get_mcp_registry()

            async def serve(request):
                if self.html_path.exists():
                    return web.FileResponse(self.html_path)
                return web.Response(text="Dashboard nicht gefunden", status=404)

            async def healthz(request):
                return web.json_response({"ok": True, "service": "isaac", **free_hosting_status()})

            async def monitor_config(request):
                mon = get_monitor()
                unified = unified_port_enabled()
                # Same-origin path for free single-port hosts
                if unified:
                    return web.json_response({
                        "ws_host": _public_ws_host(request, mon),
                        "ws_port": self.bound_port or mon.bound_port,
                        "ws_path": "/ws",
                        "ws_same_origin": True,
                        "http_port": self.bound_port,
                        "unified_port": True,
                        "free_cloud": free_cloud_enabled(),
                        "ws_port_candidates": [],
                    })
                return web.json_response({
                    "ws_host": _public_ws_host(request, mon),
                    "ws_port": mon.bound_port,
                    "ws_path": None,
                    "ws_same_origin": False,
                    "http_port": self.bound_port,
                    "unified_port": False,
                    "free_cloud": free_cloud_enabled(),
                    "ws_port_candidates": list(range(int(os.getenv("MONITOR_PORT", str(get_config().monitor.port))), int(os.getenv("MONITOR_PORT", str(get_config().monitor.port))) + 10)),
                })

            async def list_tools(request):
                return web.json_response({"ok": True, "tools": registry.list_tools()})

            async def list_local_catalog(request):
                items = list_local_tool_catalog()
                installed_catalog_ids = {((t.get("metadata") or {}).get("catalog_id")) for t in registry.list_tools()}
                for item in items:
                    item["installed"] = item.get("catalog_id") in installed_catalog_ids
                return web.json_response({"ok": True, "items": items})

            async def list_tool_bundles_api(request):
                installed_catalog_ids = {((t.get("metadata") or {}).get("catalog_id")) for t in registry.list_tools()}
                bundles = list_tool_bundles()
                for bundle in bundles:
                    ids = list(bundle.get("catalog_ids") or [])
                    bundle["installed_count"] = sum(1 for cid in ids if cid in installed_catalog_ids)
                    bundle["total_count"] = len(ids)
                return web.json_response({"ok": True, "bundles": bundles})

            async def live_tools(request):
                from tool_runtime import list_live_tool_interfaces
                live = await list_live_tool_interfaces()
                return web.json_response({"ok": True, "live": live})

            async def install_local_tool(request):
                data = await _request_json(request)
                catalog_id = (data.get("catalog_id") or "").strip()
                if not catalog_id:
                    return web.json_response({"ok": False, "error": "catalog_id fehlt"}, status=400)
                try:
                    payload = registry_payload_from_catalog(catalog_id)
                    tool = registry.add(payload)
                except KeyError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=404)
                row = next(x for x in registry.list_tools() if x["tool_id"] == tool.tool_id)
                return web.json_response({"ok": True, "tool": row, "installed": catalog_id})

            async def install_free_pack(request):
                try:
                    _ensure_local_owner_request(request, "install_free_pack")
                    installed_catalog_ids = {((t.get("metadata") or {}).get("catalog_id")) for t in registry.list_tools()}
                    installed = []
                    skipped = []
                    for catalog_id in free_starter_pack_catalog_ids():
                        if catalog_id in installed_catalog_ids:
                            skipped.append(catalog_id)
                            continue
                        tool = registry.add(registry_payload_from_catalog(catalog_id))
                        row = next(x for x in registry.list_tools() if x["tool_id"] == tool.tool_id)
                        installed.append(row)
                    return web.json_response({
                        "ok": True,
                        "installed": installed,
                        "installed_count": len(installed),
                        "skipped": skipped,
                    })
                except PermissionError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=403)

            async def install_tool_bundle(request):
                data = await _request_json(request)
                bundle_id = (data.get("bundle_id") or "").strip()
                if not bundle_id:
                    return web.json_response({"ok": False, "error": "bundle_id fehlt"}, status=400)
                try:
                    _ensure_local_owner_request(request, "install_tool_bundle")
                    installed_catalog_ids = {((t.get("metadata") or {}).get("catalog_id")) for t in registry.list_tools()}
                    installed = []
                    skipped = []
                    for catalog_id in bundle_catalog_ids(bundle_id):
                        if catalog_id in installed_catalog_ids:
                            skipped.append(catalog_id)
                            continue
                        tool = registry.add(registry_payload_from_catalog(catalog_id))
                        row = next(x for x in registry.list_tools() if x["tool_id"] == tool.tool_id)
                        installed.append(row)
                    return web.json_response({
                        "ok": True,
                        "bundle_id": bundle_id,
                        "installed": installed,
                        "installed_count": len(installed),
                        "skipped": skipped,
                    })
                except PermissionError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=403)
                except KeyError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=404)

            async def add_tool(request):
                data = await _request_json(request)
                api_key = (data.pop("api_key", "") or "").strip()
                if not data.get("name") or not data.get("kind"):
                    return web.json_response({"ok": False, "error": "name und kind sind Pflicht"}, status=400)
                tool = registry.add(data)
                if api_key:
                    ref = f"{tool.tool_id}_secret"
                    secrets.set_secret(ref, api_key)
                    registry.update(tool.tool_id, {"secret_ref": ref})
                row = next(x for x in registry.list_tools() if x["tool_id"] == tool.tool_id)
                return web.json_response({"ok": True, "tool": row})

            async def update_tool(request):
                data = await _request_json(request)
                tool_id = (data.get("tool_id") or "").strip()
                if not tool_id:
                    return web.json_response({"ok": False, "error": "tool_id fehlt"}, status=400)
                api_key = (data.pop("api_key", "") or "").strip()
                patch = {k: v for k, v in data.items() if k != "tool_id"}
                try:
                    tool = registry.update(tool_id, patch)
                except KeyError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=404)
                if api_key:
                    ref = tool.secret_ref or f"{tool.tool_id}_secret"
                    secrets.set_secret(ref, api_key)
                    registry.update(tool.tool_id, {"secret_ref": ref})
                row = next(x for x in registry.list_tools() if x["tool_id"] == tool.tool_id)
                return web.json_response({"ok": True, "tool": row})

            async def delete_tool(request):
                data = await _request_json(request)
                return web.json_response({"ok": registry.delete((data.get("tool_id") or "").strip())})

            async def toggle_tool(request):
                data = await _request_json(request)
                tool_id = (data.get("tool_id") or "").strip()
                try:
                    tool = registry.update(tool_id, {"active": bool(data.get("active", True))})
                except KeyError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=404)
                row = next(x for x in registry.list_tools() if x["tool_id"] == tool.tool_id)
                return web.json_response({"ok": True, "tool": row})

            async def suggest_tool(request):
                data = await _request_json(request)
                prompt = (data.get("prompt") or "").strip()
                tool = select_tool_for_prompt(prompt)
                if not tool:
                    return web.json_response({"ok": False, "error": "Kein passendes Tool"}, status=404)
                row = next(x for x in registry.list_tools() if x["tool_id"] == tool.tool_id)
                return web.json_response({"ok": True, "tool": row})

            async def runtime_settings(request):
                cfg = get_config()
                return web.json_response({"ok": True, "settings": cfg.runtime_settings()})

            async def update_runtime_settings(request):
                data = await _request_json(request)
                try:
                    _ensure_local_owner_request(request, "runtime_settings")
                    result = get_config().update_runtime_settings(data.get("settings") or {})
                    return web.json_response({"ok": True, **result})
                except PermissionError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=403)

            async def providers_list(request):
                cfg = get_config()
                return web.json_response({"ok": True, "providers": cfg.list_provider_configs(), "primary_provider": cfg.relay.primary_provider})

            async def providers_upsert(request):
                data = await _request_json(request)
                try:
                    _ensure_local_owner_request(request, "providers_upsert")
                    provider = get_config().upsert_provider(data)
                    get_relay().refresh_provider_config()
                    return web.json_response({"ok": True, "provider": provider, "primary_provider": get_config().relay.primary_provider})
                except PermissionError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=403)
                except ValueError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=400)

            async def providers_delete(request):
                data = await _request_json(request)
                provider_id = (data.get("provider_id") or "").strip()
                if not provider_id:
                    return web.json_response({"ok": False, "error": "provider_id fehlt"}, status=400)
                try:
                    _ensure_local_owner_request(request, "providers_delete")
                    ok = get_config().delete_provider(provider_id)
                    if ok:
                        get_relay().refresh_provider_config()
                    return web.json_response({"ok": ok, "primary_provider": get_config().relay.primary_provider})
                except PermissionError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=403)

            async def providers_set_default(request):
                data = await _request_json(request)
                provider_id = (data.get("provider_id") or "").strip()
                if not provider_id:
                    return web.json_response({"ok": False, "error": "provider_id fehlt"}, status=400)
                try:
                    _ensure_local_owner_request(request, "providers_set_default")
                    provider = get_config().set_default_provider(provider_id)
                    get_relay().refresh_provider_config()
                    return web.json_response({"ok": True, "provider": provider, "primary_provider": get_config().relay.primary_provider})
                except PermissionError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=403)
                except ValueError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=400)

            async def providers_test(request):
                data = await _request_json(request)
                provider_id = (data.get("provider_id") or "").strip()
                model_override = (data.get("model") or "").strip() or None
                if not provider_id:
                    return web.json_response({"ok": False, "error": "provider_id fehlt"}, status=400)
                cfg = get_config().get_provider(provider_id)
                if not cfg:
                    return web.json_response({"ok": False, "error": "Unbekannter Provider"}, status=404)
                if not cfg.enabled:
                    return web.json_response({"ok": False, "error": "Provider ist deaktiviert"}, status=400)
                relay = get_relay()
                try:
                    answer = await relay.ask(
                        prompt=(data.get("prompt") or "Antworte exakt mit: OK").strip() or "Antworte exakt mit: OK",
                        system="Kurzer Provider-Selbsttest. Antworte kurz.",
                        provider=provider_id,
                        use_cache=False,
                        model_override=model_override,
                    )
                    ok = not answer.startswith("[RELAY")
                    return web.json_response({
                        "ok": ok,
                        "provider_id": provider_id,
                        "model": model_override or cfg.model,
                        "response_preview": answer[:220],
                    }, status=200 if ok else 400)
                except Exception as e:
                    return web.json_response({"ok": False, "error": str(e), "provider_id": provider_id}, status=400)

            async def browser_state(request):
                from browser import get_browser
                browser = get_browser()
                return web.json_response({
                    "ok": True,
                    "instances": browser.list_instances(),
                    "catalog": browser.site_catalog(),
                    "stats": browser.stats(),
                    "settings": get_config().runtime_settings(),
                })

            async def browser_activate_site(request):
                data = await _request_json(request)
                try:
                    _ensure_local_owner_request(request, "browser_activate_site")
                    from browser import get_browser
                    result = await get_browser().activate_catalog_site(
                        (data.get("site_id") or "").strip()
                    )
                    return web.json_response(result, status=200 if result.get("ok") else 400)
                except PermissionError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=403)
                except KeyError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=404)

            async def browser_openrouter_token(request):
                data = await _request_json(request)
                try:
                    _ensure_local_owner_request(request, "browser_openrouter_token")
                    from browser import get_browser
                    result = await get_browser().provision_openrouter_token(
                        secret_ref=(data.get("secret_ref") or "OPENROUTER_API_KEY").strip() or "OPENROUTER_API_KEY",
                        key_name=(data.get("key_name") or "Isaac").strip() or "Isaac",
                    )
                    return web.json_response(result, status=200 if result.get("ok") else 400)
                except PermissionError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=403)

            async def browser_provider_token(request):
                data = await _request_json(request)
                try:
                    _ensure_local_owner_request(request, "browser_provider_token")
                    from browser import get_browser
                    provider_id = (data.get("provider_id") or "groq").strip().lower() or "groq"
                    auto = bool(data.get("auto"))
                    browser = get_browser()
                    if auto:
                        result = await browser.auto_provision_providers(
                            provider_ids=data.get("provider_ids") or None,
                            provision_all=bool(data.get("all", True)),
                        )
                    else:
                        result = await browser.provision_provider_token(
                            provider_id=provider_id,
                            secret_ref=(data.get("secret_ref") or "").strip(),
                            key_name=(data.get("key_name") or "Isaac").strip() or "Isaac",
                        )
                    return web.json_response(result, status=200 if result.get("ok") else 400)
                except PermissionError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=403)


            async def mcp_capabilities(request):
                return web.json_response({"ok": True, "capabilities": mcp.capabilities()})

            async def mcp_resources(request):
                return web.json_response({"ok": True, "resources": mcp.resources()})

            async def mcp_read_resource(request):
                data = await _request_json(request)
                uri = (data.get("uri") or "").strip()
                params = dict(data.get("params") or {})
                result = mcp.read_resource(uri, **params)
                return web.json_response(result, status=200 if result.get("ok") else 404)

            async def mcp_prompts(request):
                return web.json_response({"ok": True, "prompts": mcp.prompts()})

            async def mcp_get_prompt(request):
                data = await _request_json(request)
                name = (data.get("name") or "").strip()
                args = dict(data.get("arguments") or {})
                result = mcp.get_prompt(name, args)
                return web.json_response(result, status=200 if result.get("ok") else 404)

            async def mcp_tools(request):
                return web.json_response({"ok": True, "tools": mcp.tools()})

            async def mcp_invoke_tool(request):
                data = await _request_json(request)
                name = (data.get("name") or "").strip()
                args = dict(data.get("arguments") or {})
                if data.get("owner_override"):
                    remote = (request.remote or "").strip()
                    if remote not in {"127.0.0.1", "::1", ""}:
                        return web.json_response(
                            {"ok": False, "error": "owner_override nur von localhost"},
                            status=403,
                        )
                    reason = (data.get("override_reason") or args.get("override_reason") or "").strip()
                    if not reason:
                        return web.json_response(
                            {"ok": False, "error": "override_reason fehlt"},
                            status=400,
                        )
                    args["owner_override"] = True
                    args["override_reason"] = reason
                from tool_runtime import invoke_mcp_tool

                result = await invoke_mcp_tool(name, args)
                return web.json_response(result, status=200 if result.get("ok") else 400)

            async def mcp_jsonrpc(request):
                from mcp_jsonrpc import get_jsonrpc_handler

                try:
                    payload = await request.json()
                except Exception:
                    return web.json_response({
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    }, status=400)
                try:
                    result = get_jsonrpc_handler(mcp).handle_payload(payload)
                except Exception as e:
                    return web.json_response({
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32603, "message": str(e)},
                    }, status=500)
                status = 200
                return web.json_response(result, status=status)

            async def updater_packages(request):
                return web.json_response({"ok": True, "packages": list_packages()})

            async def updater_inspect(request):
                data = await _request_json(request)
                package_name = (data.get("package") or "").strip()
                if not package_name:
                    return web.json_response({"ok": False, "error": "package fehlt"}, status=400)
                try:
                    return web.json_response(inspect_package(package_name))
                except Exception as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=400)

            def _ensure_local_owner_request(request, action: str):
                remote = (request.remote or "").strip()
                if remote not in {"127.0.0.1", "::1", "localhost", ""}:
                    raise PermissionError("Updater nur lokal erlaubt")
                get_gate().require("modify_config", steffen_ctx(f"Dashboard {action} lokales Patch/Update"))

            async def updater_apply(request):
                data = await _request_json(request)
                package_name = (data.get("package") or "").strip()
                restart_after = bool(data.get("restart_after", True))
                if not package_name:
                    return web.json_response({"ok": False, "error": "package fehlt"}, status=400)
                try:
                    _ensure_local_owner_request(request, "apply")
                    result = apply_package(package_name)
                    if restart_after and result.get("ok"):
                        asyncio.create_task(schedule_process_restart())
                    return web.json_response({**result, "restart_scheduled": restart_after and result.get("ok")})
                except PermissionError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=403)
                except Exception as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=400)

            async def updater_rollback(request):
                data = await _request_json(request)
                restart_after = bool(data.get("restart_after", True))
                try:
                    _ensure_local_owner_request(request, "rollback")
                    result = rollback_last_backup()
                    if restart_after and result.get("ok"):
                        asyncio.create_task(schedule_process_restart())
                    return web.json_response({**result, "restart_scheduled": restart_after and result.get("ok")})
                except PermissionError as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=403)
                except Exception as e:
                    return web.json_response({"ok": False, "error": str(e)}, status=400)

            async def updater_status_api(request):
                return web.json_response(updater_status())

            async def regelwerk_open_questions(request):
                from regelwerk import get_regelwerk

                limit = max(1, min(int(request.rel_url.query.get("limit", "20")), 50))
                rw = get_regelwerk()
                return web.json_response({
                    "ok": True,
                    "count": len(rw.offene_fragen()),
                    "questions": rw.open_questions_dict(limit=limit),
                    "status": rw.status(),
                })

            async def governance_constitution(request):
                from constitution import get_constitution
                c = get_constitution()
                return web.json_response({
                    "ok": True,
                    "constitution": c.export(),
                    "summary": c.summary(),
                    "context": c.as_context(),
                })

            async def governance_self_model(request):
                from self_model import get_self_model
                sm = get_self_model()
                return web.json_response({
                    "ok": True,
                    "self_model": sm.snapshot(),
                    "summary": sm.summary(),
                })

            async def governance_development(request):
                from memory import get_memory
                limit = max(1, min(int(request.rel_url.query.get("limit", "40")), 200))
                return web.json_response({
                    "ok": True,
                    "events": get_memory().recent_development_events(limit),
                })

            async def governance_memory_blocks(request):
                limit = max(1, min(int(request.rel_url.query.get("limit", "40")), 100))
                result = mcp.read_resource("resource://memory/blocks", limit=limit)
                return web.json_response(result, status=200 if result.get("ok") else 400)

            async def governance_procedures(request):
                from memory import get_memory
                limit = max(1, min(int(request.rel_url.query.get("limit", "40")), 100))
                return web.json_response({
                    "ok": True,
                    "procedures": get_memory().list_procedures(limit),
                })

            async def governance_decay(request):
                from forgetting_decay import load_decay_state, run_decay_cycle
                from memory import get_memory

                if request.method == "POST":
                    summary = run_decay_cycle()
                    return web.json_response({
                        "ok": True,
                        "ran": True,
                        "summary": summary,
                        "stats": get_memory().decay_stats(),
                        "last_run": load_decay_state(),
                    })
                return web.json_response({
                    "ok": True,
                    "stats": get_memory().decay_stats(),
                    "last_run": load_decay_state(),
                    "archived_recent": get_memory().recent_archived_development_events(15),
                })

            async def task_checkpoints(request):
                if not is_localhost_request(request.remote):
                    return web.json_response(
                        {"ok": False, "error": "task checkpoints nur von localhost"},
                        status=403,
                    )
                task_id = (request.rel_url.query.get("task_id") or "").strip()
                if not task_id:
                    return web.json_response({"ok": False, "error": "task_id fehlt"}, status=400)
                from memory import get_memory
                return web.json_response({
                    "ok": True,
                    "task_id": task_id,
                    "checkpoints": get_memory().list_checkpoints(task_id, limit=25),
                })

            async def task_resume(request):
                if not is_localhost_request(request.remote):
                    return web.json_response(
                        {"ok": False, "error": "task resume nur von localhost"},
                        status=403,
                    )
                data = await _request_json(request)
                task_id = (data.get("task_id") or "").strip()
                if not task_id:
                    return web.json_response({"ok": False, "error": "task_id fehlt"}, status=400)
                ok = get_executor().resume_task(task_id)
                task = get_executor().get_task(task_id)
                return web.json_response({
                    "ok": ok,
                    "task_id": task_id,
                    "task": task.to_dict() if task else None,
                }, status=200 if ok else 400)

            async def monitor_state(request):
                mon = get_monitor()
                return web.json_response({
                    'ok': True,
                    'state': mon._build_state() if hasattr(mon, '_build_state') else {},
                    'tasks': get_executor().all_tasks(50),
                    'providers': get_relay().provider_status(),
                    'provider_configs': get_config().list_provider_configs(),
                    'tools': registry.list_tools(),
                    'tool_catalog': list_local_tool_catalog(),
                    'live_tools': await __import__('tool_runtime').list_live_tool_interfaces(),
                    'task_tool_states': get_task_tool_state_store().export_for_tasks([t.get('id') for t in get_executor().all_tasks(50)]),
                    'audit': AuditLog.recent(20),
                    'open_questions': mon._get_open_questions() if hasattr(mon, '_get_open_questions') else [],
                    'regelwerk': mon._get_regelwerk_status() if hasattr(mon, '_get_regelwerk_status') else {},
                })

            app = web.Application()
            app.router.add_get("/", serve)
            app.router.add_get("/monitor_config", monitor_config)
            app.router.add_get("/api/tools", list_tools)
            app.router.add_get("/api/tools/catalog", list_local_catalog)
            app.router.add_get("/api/tools/bundles", list_tool_bundles_api)
            app.router.add_get("/api/tools/live", live_tools)
            app.router.add_post("/api/tools/install_local", install_local_tool)
            app.router.add_post("/api/tools/install_free_pack", install_free_pack)
            app.router.add_post("/api/tools/install_bundle", install_tool_bundle)
            app.router.add_post("/api/tools/add", add_tool)
            app.router.add_post("/api/tools/update", update_tool)
            app.router.add_post("/api/tools/delete", delete_tool)
            app.router.add_post("/api/tools/toggle", toggle_tool)
            app.router.add_post("/api/tools/suggest", suggest_tool)
            app.router.add_get("/api/runtime/settings", runtime_settings)
            app.router.add_post("/api/runtime/settings", update_runtime_settings)
            app.router.add_get("/api/providers", providers_list)
            app.router.add_post("/api/providers/upsert", providers_upsert)
            app.router.add_post("/api/providers/delete", providers_delete)
            app.router.add_post("/api/providers/set_default", providers_set_default)
            app.router.add_post("/api/providers/test", providers_test)
            app.router.add_get("/api/browser/state", browser_state)
            app.router.add_post("/api/browser/activate_site", browser_activate_site)
            app.router.add_post("/api/browser/openrouter_token", browser_openrouter_token)
            app.router.add_post("/api/browser/provider_token", browser_provider_token)
            app.router.add_get("/api/mcp/capabilities", mcp_capabilities)
            app.router.add_get("/api/mcp/resources", mcp_resources)
            app.router.add_post("/api/mcp/resource/read", mcp_read_resource)
            app.router.add_get("/api/mcp/prompts", mcp_prompts)
            app.router.add_post("/api/mcp/prompts/get", mcp_get_prompt)
            app.router.add_get("/api/mcp/tools", mcp_tools)
            app.router.add_post("/api/mcp/tools/invoke", mcp_invoke_tool)
            app.router.add_post("/api/mcp/jsonrpc", mcp_jsonrpc)
            app.router.add_get("/api/regelwerk/open-questions", regelwerk_open_questions)
            app.router.add_get("/api/governance/constitution", governance_constitution)
            app.router.add_get("/api/governance/self-model", governance_self_model)
            app.router.add_get("/api/governance/development", governance_development)
            app.router.add_get("/api/governance/memory-blocks", governance_memory_blocks)
            app.router.add_get("/api/governance/procedures", governance_procedures)
            app.router.add_get("/api/governance/decay", governance_decay)
            app.router.add_post("/api/governance/decay", governance_decay)
            app.router.add_get("/api/tasks/checkpoints", task_checkpoints)
            app.router.add_post("/api/tasks/resume", task_resume)
            app.router.add_get("/api/update/packages", updater_packages)
            app.router.add_post("/api/update/inspect", updater_inspect)
            app.router.add_post("/api/update/apply", updater_apply)
            app.router.add_post("/api/update/rollback", updater_rollback)
            app.router.add_get("/api/update/status", updater_status_api)
            app.router.add_get("/api/monitor/state", monitor_state)
            app.router.add_get("/healthz", healthz)
            app.router.add_get("/health", healthz)

            # Unified free-cloud WS on same HTTP port
            mon = get_monitor()

            async def ws_endpoint(request):
                return await mon.handle_aiohttp_ws(request)

            if unified_port_enabled():
                app.router.add_get("/ws", ws_endpoint)

            runner = web.AppRunner(app)
            await runner.setup()

            host = bind_host("localhost")
            last_error = None
            # On PaaS PORT must be exact — no offset probing
            offsets = [0] if (os.getenv("PORT") or "").strip().isdigit() or free_cloud_enabled() else range(0, 10)
            for offset in offsets:
                port = self.port + int(offset)
                try:
                    await web.TCPSite(runner, host, port).start()
                    self.bound_port = port
                    self.bound_host = host
                    mon.bound_host = host
                    mon.bound_port = port if unified_port_enabled() else mon.bound_port
                    if offset:
                        log.warning(f"Dashboard-Port {self.port} belegt → Fallback auf {port}")
                    log.info(f"Dashboard: http://{host}:{port}")
                    if unified_port_enabled():
                        log.info(f"Dashboard WS: ws://{host}:{port}/ws")
                    return
                except OSError as e:
                    last_error = e
                    if e.errno == 98 or "address already in use" in str(e).lower():
                        continue
                    raise
            raise last_error or RuntimeError("Kein freier Dashboard-Port gefunden")
        except ImportError:
            log.warning("aiohttp nicht installiert — kein HTTP-Dashboard")
        except Exception as e:
            log.warning(f"Dashboard HTTP-Start: {e}")


_monitor: Optional[MonitorServer] = None

def get_monitor() -> MonitorServer:
    global _monitor
    if _monitor is None:
        _monitor = MonitorServer()
    return _monitor


_dashboard: Optional[DashboardHTTPServer] = None

def get_dashboard() -> DashboardHTTPServer:
    global _dashboard
    if _dashboard is None:
        _dashboard = DashboardHTTPServer()
    return _dashboard

async def _main():
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)-7s %(name)s – %(message)s',
        datefmt='%H:%M:%S'
    )

    mon = get_monitor()
    dash = get_dashboard()

    await dash.start()
    await mon.start()

if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("Monitor gestoppt.")
