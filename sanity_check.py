#!/usr/bin/env python3
from __future__ import annotations
import importlib
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

modules = [
    "config","audit","memory","privilege","logic","relay","executor","watchdog",
    "decomposer","dispatcher","ki_dialog","ki_skills","search","sudo_gate",
    "tool_registry","tool_runtime","browser","background_loop","monitor_server",
    "monitor_api","neural_core","learning_engine","isaac_core"
]

print("Isaac Sanity Check")
print("="*60)
ok = True

for mod in modules:
    try:
        importlib.import_module(mod)
        print(f"[OK] import {mod}")
    except Exception as e:
        ok = False
        print(f"[FAIL] import {mod}: {e}")

for env_name in ["ACTIVE_PROVIDER","OLLAMA_HOST","OLLAMA_MODEL","MONITOR_PORT","DASHBOARD_PORT"]:
    print(f"[ENV] {env_name} = {os.getenv(env_name, '<unset>')}")

for port_name in ["MONITOR_PORT","DASHBOARD_PORT"]:
    port = os.getenv(port_name)
    if not port:
        continue
    try:
        port_i = int(port)
        with socket.socket() as s:
            s.bind(("127.0.0.1", port_i))
        print(f"[OK] Port frei: {port_name}={port_i}")
    except Exception as e:
        print(f"[WARN] Portproblem {port_name}={port}: {e}")

if not (ROOT / ".env").exists():
    print("[WARN] .env fehlt – kopiere .env.example nach .env")
else:
    print("[OK] .env vorhanden")

if ok:
    print("\nErgebnis: OK")
    sys.exit(0)
print("\nErgebnis: FEHLER")
sys.exit(1)
