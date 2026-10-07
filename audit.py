"""
Isaac – Audit Log (Append-Only)
================================
Jede Aktion die Isaac ausführt wird hier unveränderlich gespeichert.
Isaac kann seinen eigenen Audit-Log nicht löschen oder modifizieren.

Format: JSONL (eine JSON-Zeile pro Eintrag)
"""
from __future__ import annotations

import json
import time
import logging
import asyncio
import threading
from pathlib import Path
from typing import Any, Optional

from config import AUDIT_PATH

log = logging.getLogger("Isaac.Audit")

# Thread-Lock für synchronen Zugriff (wird von sync und async Code genutzt)
_write_lock = threading.Lock()


# ── Audit-Eintrag ──────────────────────────────────────────────────────────────
def _make_entry(typ: str, data: dict) -> dict:
    return {
        "ts":   time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ms":   int(time.time() * 1000),
        "typ":  typ,
        **data
    }


def _write(entry: dict):
    """Schreibt einen Eintrag thread-safe in die JSONL-Datei."""
    line = json.dumps(entry, ensure_ascii=False, default=str)
    with _write_lock:
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ── Öffentliche API ────────────────────────────────────────────────────────────
class AuditLog:
    """
    Append-Only Audit Log für Isaac.
    Alle Methoden sind sync und async verfügbar.
    """

    # Puffer für Dashboard (letzten N Einträge im RAM)
    _buffer:     list[dict] = []
    _max_buffer: int        = 1000
    _callbacks:  list       = []   # WebSocket-Callbacks

    @classmethod
    def _record(cls, typ: str, data: dict):
        entry = _make_entry(typ, data)
        _write(entry)
        cls._buffer.append(entry)
        if len(cls._buffer) > cls._max_buffer:
            cls._buffer = cls._buffer[-cls._max_buffer:]
        # Callbacks notifizieren (für WebSocket-Push)
        for cb in cls._callbacks:
            try:
                cb(entry)
            except Exception:
                pass

    @classmethod
    def register_callback(cls, fn):
        """WebSocket-Server registriert sich hier für Live-Push."""
        cls._callbacks.append(fn)

    @classmethod
    def unregister_callback(cls, fn):
        cls._callbacks = [c for c in cls._callbacks if c != fn]

    # ── Log-Typen ──────────────────────────────────────────────────────────────
    @classmethod
    def action(cls, caller: str, aktion: str, detail: str = "",
               level: int = 0, erfolg: bool = True):
        cls._record("action", {
            "caller": caller, "aktion": aktion,
            "detail": detail[:200], "level": level, "erfolg": erfolg
        })

    @classmethod
    def task(cls, task_id: str, status: str, detail: str = "",
             score: float = 0.0, iteration: int = 0):
        cls._record("task", {
            "task_id": task_id, "status": status,
            "detail": detail[:300], "score": score, "iteration": iteration
        })

    @classmethod
    def instance(cls, instance_id: str, event: str, provider: str = "",
                 detail: str = ""):
        cls._record("instance", {
            "instance_id": instance_id, "event": event,
            "provider": provider, "detail": detail[:200]
        })

    @classmethod
    def quality(cls, task_id: str, score: float, scores: dict,
                iteration: int, action_taken: str):
        cls._record("quality", {
            "task_id": task_id, "score": score,
            "scores": scores, "iteration": iteration,
            "action_taken": action_taken
        })

    @classmethod
    def privilege(cls, data: dict):
        cls._record("privilege", data)

    @classmethod
    def error(cls, caller: str, fehler: str, detail: str = ""):
        cls._record("error", {
            "caller": caller, "fehler": fehler[:200], "detail": detail[:300]
        })
        log.error(f"[{caller}] {fehler}")

    @classmethod
    def steffen_input(cls, text: str):
        cls._record("steffen_input", {"text": text[:500]})

    @classmethod
    def isaac_output(cls, text: str, task_id: str = ""):
        raw = text or ""
        # Keep a short preview for dense dashboards AND enough full text to
        # recover the last answer for owner follow-ups (was 500 — too short).
        cls._record(
            "isaac_output",
            {
                "text": raw[:4000],
                "text_preview": raw[:500],
                "text_len": len(raw),
                "task_id": task_id,
            },
        )

    @classmethod
    def memory_write(cls, caller: str, typ: str, key: str):
        cls._record("memory", {
            "caller": caller, "op": "write", "typ": typ, "key": key[:100]
        })

    @classmethod
    def internet(cls, caller: str, url: str, status: int = 0):
        cls._record("internet", {
            "caller": caller, "url": url[:200], "status": status
        })

    @classmethod
    def system_cmd(cls, caller: str, cmd: str, returncode: int = 0):
        # Systembefehl immer voll loggen
        cls._record("system_cmd", {
            "caller": caller, "cmd": cmd[:300], "returncode": returncode
        })


    @classmethod
    def confirmation(cls, status: str, aktion: str, queue_id: str, reason: str = ""):
        cls._record("confirmation", {
            "status": status, "aktion": aktion, "queue_id": queue_id, "reason": reason[:300]
        })

    @classmethod
    def development(cls, event_type: str, target_kind: str, target_key: str,
                    delta: float = 0.0, reason: str = ""):
        cls._record("development", {
            "event_type": event_type,
            "target_kind": target_kind,
            "target_key": target_key[:100],
            "delta": delta,
            "reason": reason[:300],
        })

    # ── Lesen ──────────────────────────────────────────────────────────────────
    @classmethod
    def recent(cls, n: int = 100, typ: Optional[str] = None) -> list[dict]:
        """Gibt die letzten n Einträge aus dem RAM-Puffer zurück."""
        if typ:
            filtered = [e for e in cls._buffer if e.get("typ") == typ]
            return filtered[-n:]
        return cls._buffer[-n:]

    @classmethod
    def read_from_disk(cls, n: int = 500) -> list[dict]:
        """Liest die letzten n Einträge von Disk."""
        if not AUDIT_PATH.exists():
            return []
        lines = []
        with open(AUDIT_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return lines[-n:]

    @classmethod
    def stats(cls) -> dict:
        buffer = cls._buffer
        typen: dict[str, int] = {}
        for e in buffer:
            typen[e.get("typ", "?")] = typen.get(e.get("typ", "?"), 0) + 1
        return {
            "total_buffer": len(buffer),
            "by_type":      typen,
            "audit_file_kb": round(AUDIT_PATH.stat().st_size / 1024, 1)
                             if AUDIT_PATH.exists() else 0,
        }


# ── Privilege-Hook registrieren ────────────────────────────────────────────────
def _privilege_hook(data: dict):
    AuditLog.privilege(data)

def setup_privilege_audit():
    """Verbindet Privilege-Gate mit Audit-Log."""
    from privilege import get_gate
    get_gate().set_audit_fn(_privilege_hook)
