# ISAAC Master Roadmap v5

**Stand:** 2026-07-24  
**Code-Basis:** `main` @ `ff5ab7b` (hansheute88/isaac + sco0rp/IsaacNew sync)  
**Aktive Disziplin (AGENTS.md):** *Consolidate core behavior* — nicht Feature-Expansion  
**Kernel:** v5.3 · Pipeline `classify → retrieve → strategy → task → execute → evaluate → memory`

> **Priorität bei Widersprüchen:**  
> `AGENTS.md` → Checklisten (`06_…` / `05_…`) → **dieses Dokument** → Drive/Elicit/Brain-PDFs  
> Drive-Patches und Brain-SNN-Pläne **ersetzen** diese Roadmap nicht.

---

## 1. Executive Status

Isaac ist ein **lokaler Cognitive Kernel** (ROT/BLAU/GRÜN), kein Chatbot-Wrapper und kein Agent-Framework-Import.

| Ebene | Rolle | Kernmodule |
|-------|--------|------------|
| **ROT** | Control / Governance | `isaac_core`, `low_complexity`, `constitution`, `privilege`, `sudo_gate`, `regelwerk` |
| **BLAU** | Memory / Kontext | `memory`, `vector_memory` (optional), `procedure_memory`, `forgetting_decay`, `meaning`, `values`, goal-Stores |
| **GRÜN** | Execution | `executor`, `relay`, `tool_runtime`, `search`, `browser`, `computer_use`, MCP-Bridge |

### Phasen-Wahrheit (nicht die veralteten Drive-PDFs)

| Phase | Status | Inhalt |
|-------|--------|--------|
| **1 — STABILIZE** | ✅ | Executor führt nur Task/Strategy aus; Tools nur mit `allow_tools` |
| **2 — ALIGN** | ✅ | Ein Retrieval-Pfad: `build_retrieval_context()` |
| **3 — REFINE** | ✅ | Constitution, Self-Model, Checkpoint, MCP-Grundgerüst, evals |
| **4 — CONNECT** | ✅ | DecisionTrace, Regelwerk→Retrieval, Procedure→Selection, MCP-Härtung |
| **Evolution 2.0** | ✅ lokal | Policy, Evaluation/Learning-Trace, Boundaries, Owner-Autonomie bounded |
| **Goal-Autonomie S0–S4** | ✅ | Goal Store, Motivation, Inquiry/Research, Digest, Temporal Facts |
| **Consolidate (aktiv)** | 🔄 | Härten, Evals, ehrliche Docs; keine neuen Framework-Layer |

**Messlatte lokal:** `unittest` grün · `evals.eval_runner` **96/96** · `sanity_check`  

**Offen ops:** GitHub Actions Billing-Lock (E2.0.5.4) — kein Code-Blocker.

---

## 2. Quellen (Drive-Uploads 2026-07-24 und Bestand)

### 2.1 Frische Uploads (~01:28 UTC)

| Quelle | Rolle | Für Kernel-main |
|--------|--------|-----------------|
| Elicit *Roadmap Phase 3+* | Sequenz Kern→MCP→Memory→Self-improve | **Nützlich**, Phasennummern aber **veraltet** (behauptet Phase 1–2 only) |
| Elicit *OSS punktuell* | Muster-Karte (Cognee, OTel, durable-agents, MCP SDK…) | **Nützlich** als Watchlist, kein Import-Zwang |
| Elicit *OTel→DecisionTrace* | Span-Mapping + Adapter-Plan | **Teilweise umgesetzt** (`to_portable_export`) |
| Master Roadmap *Gehirnähnlich* + Research Brief | SNN/WBE/Microcircuits | **Research-Track R** — nicht nächster Kernel-Schritt |
| `00_MASTER_PATCH_MANIFEST` | VectorMemory + Isaac Lab + R=1 Sim | **Abgelehnt** (siehe §8) |
| Projekt-Übersicht eclizit | Marketing „Phase 1–2“ | **Veraltet** |

### 2.2 Frühere Drive-Patches (IsaacNew-main)

| Artefakt | Urteil |
|----------|--------|
| `task_state_machine.py` | Redundant zu `task_checkpoint` + Resume |
| `trace_otel.py` / `*_adapted` | Doppeltypen bzw. überholt durch portable Export |
| Lab / `run_baselines*` | Mock-Theater; echte Messung = Evals |
| Vector-Pipeline-Doppelpfad | Do-NOT vector redesign |
| UNIFIED Roadmaps v1–v4 / PHASE3 guides | Falsche Lücken („keine SM/Constitution“) |

Vollständige Ablehnung: `docs/DRIVE_PATCH_REVIEW_2026-07-24.md`.

### 2.3 Kanonische Repo-Leitdateien

1. `AGENTS.md`  
2. `06_goal_autonomy_checklist.txt`  
3. `05_evolution2_checklist.txt`  
4. `docs/OPEN_SOURCE_PATTERNS.md`  
5. `docs/GITHUB_WATCHLIST.md`  
6. **dieses Dokument**

---

## 3. Gap-Matrix (Drive/Elicit-Idee → Ist)

### 3.1 Elicit „Phase 3 – Kern härten“

| Idee | Status | Evidenz / Lücke |
|------|--------|-----------------|
| Verfassung an kritischen Calls | ✅ weitgehend | `constitution_gate_for_tool`, Kernel-Gate, Privilege, package-shell-Fragmente (PR #30), governance_eval |
| Self-Model an Interaktionen | 🟡 gut, ausbaubar | `self_model_hooks`, identity_eval; weitere Feedback→state-Kopplung möglich |
| Durable State-Machine / Resume | ✅ | `task_checkpoint` + Executor Resume + soft Transitions (PR #30); **keine** Parallel-SM aus Drive |
| OTel-Schema DecisionTrace | 🟡 minimal | `to_portable_export()`; fehlt: `gen_ai.*` Token-Felder, Relay-Anreicherung, File-Export-Hook |
| Eval-Harness | ✅ stark | 8 Suites, **96/96**, inkl. goal_eval (PR #31); optional DeepEval/YAML später |

### 3.2 Elicit „Phase 4 – Protokoll & Sichtbarkeit“

| Idee | Status | Evidenz / Lücke |
|------|--------|-----------------|
| MCP tools/resources/prompts | 🟡 solide Basis | 6 Tools, 7 Resources (`resource://constitution|self-model|memory/blocks|procedures|audit/tail` + isaac://…), JSON-RPC + REST, Privilege-Map, mcp_eval |
| MCP SDK-v1 Parität / stdio-Härte | 🟡 | stdio vorhanden; kein wholesale SDK-Import; mehr Contract-Tests möglich |
| MCP-Subagent-Expansion | ❌ Do-NOT | bleibt verboten |
| Dashboard Trace-Viewer | ❌ Do-NOT (aktuell) | nur blockierende Fixes |
| Deklarative Rails (NeMo-Muster) | ❌ | Constitution principles code-seitig |

### 3.3 Elicit „Phase 5 – Gedächtnis“

| Idee | Status | Evidenz / Lücke |
|------|--------|-----------------|
| Knowledge-Graph (Cognee-Muster) | 🟡 Adapter | external_memory Cognee optional OFF; **kein** Graph-Kern-Replace |
| Procedure-Memory | ✅ | procedure_memory + degrade in learning_eval |
| Forgetting/Decay | ✅ Modul | `forgetting_decay.py` |
| VectorMemory wholesale redesign | ❌ Do-NOT | Modul da, Pipeline-Doppelpfad abgelehnt |

### 3.4 Elicit „Phase 6 – Self-improve & Provider“

| Idee | Status | Evidenz / Lücke |
|------|--------|-----------------|
| Reflexion-Loop messbar | 🟡 | Module da; keine Eval-Pass-Rate-Kopplung |
| Failover/Routing | ✅ | relay, ensemble, watchdog |
| Lokales Serving | ✅ | Ollama / openai_compat / LOCAL_LLM docs |
| Multi-Agent Handoffs | ❌ Do-NOT | MCP-Subagent |

### 3.5 Autonomie

| Spur | Status | Evidenz |
|------|--------|---------|
| **Goal-directed** (Steffen-Ziele → Subgoals → Motivation → Inquiry) | ✅ S0–S4 | goal_store, motivation, goal_inquiry, goal_digest, retrieval, goal_eval |
| **Owner-scheduled** (nächtliche bounded Tasks) | ✅ | owner_autonomy + background_loop, Constitution-Gate |
| Ungebundene Personality / Companion-Autonomie | ❌ Do-NOT | — |

### 3.6 Brain / SNN-Roadmap (PDF)

| Idee | Status |
|------|--------|
| snnTorch / SpikingJelly / BrainCog Hybrid Memory | ❌ Research-only |
| Neuromorphic Event Loop / Lava | ❌ |
| Microcircuit-Orchestrator-Rewrite | ❌ (widerspricht „kein wholesale redesign“) |
| WBE-Sandbox | ❌ |

---

## 4. Erledigte Meilensteine (Code, Juli 2026)

| Meilenstein | PR / Hinweis |
|-------------|--------------|
| Goal-Autonomie Status-Docs | #27 |
| isaac-agent Roadmap-kompatibel | #28 |
| Drive-Patches abgelehnt + portable Trace | #29 |
| Soft-Checkpoints + package-shell Constitution + Evals 70 | #30 |
| Goal-Eval-Suite + Routing-Regression → **96/96** | #31 |
| Repo-Sync sc0rp0815 ↔ sco0rp ↔ glinka main | laufend (preferred: sc0rp0815/Isaac) |

---

## 5. Master-Tracks (Arbeitsprogramm)

Jeder Track: **klein, testbar, ROT/BLAU/GRÜN-Ownership, nach jedem Substep runnable.**

### Track C — Consolidate (jetzt · höchste Priorität)

#### C1 — Observability / DecisionTrace vertiefen (ROT/GRÜN)

- [x] EXECUTION-Spans: `provider`/`model`/`latenz`/Tokens unter stabilen Keys (+ `gen_ai.*` Aliase im Export)  
- [x] `export_portable_trace(path)` Hilfsfunktion **in** `decision_trace.py` (kein zweites Modul)  
- [x] Redaction vor Export (api_key/token/password…)  
- [x] Eval/unittest: portable export enthält model/latency (`portable_v1_1`)  

**DoD:** Ein Task-Lauf → portable JSON mit Phasen + EXECUTION-Metadaten, ohne Cloud-Collector. **(2026-07-24)**

#### C2 — MCP härten (ohne Subagent-Expansion) (GRÜN/ROT)

- [x] Tools + Resources + Prompts + Privilege-Map (Grundgerüst)  
- [x] JSON-RPC + REST Bridge  
- [x] Contract-Tests: jede Resource lesbar; unknown tool/resource/prompt rejected; Privilege-Maps exact (`evals/mcp_eval.py`)  
- [x] stdio-Transport Smoke in evals  
- [x] Docs: `docs/MCP_CAPABILITY.md` + Verweis in Admin-Capability-Matrix  
- [x] **Nicht:** Multi-Agent, remote Subagent-Orchestrierung (bleibt Do-NOT)  

**DoD:** `evals.mcp` grün + dokumentierte resource:// Liste = Registry-Output. **(2026-07-27)**

#### C3 — Checkpoint / Resume (GRÜN)

- [x] CheckpointState + Resume + soft Transitions  
- [x] Preferred-Path glätten (eval→done, planning→learning_commit); soft_ok nur debug  
- [x] Resume setzt normalized PLANNING; Resume-Unit + reliability/replay  

**DoD:** Keine parallele SM; Resume-Evals grün; Soft-Logs selten. **(2026-07-27)**

#### C4 — Self-Model & bounded Learning (BLAU/ROT)

- [x] Self-Model + Hooks + identity_eval  
- [x] Owner-Feedback → relationship_state (Korrektur/Bestätigung) + shared_themes  
- [x] LEARNING-Phase `procedure_record` / `turn_complete` im Executor-Finalize  
- [x] `learning_policy.bounded_update` in `apply_relationship_delta`  

**DoD:** identity_eval + learning_eval grün; keine ungebundene Self-Rewrite-Loop. **(2026-07-27)**

#### C5 — Release / CI (ops)

- [ ] GitHub Actions Billing entsperren  
- [x] unittest + eval_runner lokal  
- [ ] CI grün dokumentieren  

**DoD:** Ein Push → CI grün (sobald Billing ok).

---

### Track G — Goal & Owner-Autonomie (maintain)

#### G1 — Goal-directed free agency

- [x] Store, Commands, Status-Block  
- [x] Motivation Tick + Cooldown + Max-Attempts  
- [x] Inquiry/Research an goal_id  
- [x] Digest Fingerprint  
- [x] Retrieval active_goals  
- [x] goal_eval suite  
- [ ] Live-Ops: Intervalle/Env in OWNER_COMMANDS pflegen  

#### G2 — Owner-scheduled autonomy

- [x] owner_autonomy + Constitution vor Execute  
- [x] max_per_cycle, failure backoff, windows  
- [ ] Status-Inspectability in Docs/Status-Block prüfen  

#### G3 — Schutz

- [x] Kein zielloser Background-Spam (goal_id-Bindung)  
- [x] protect_user / privilege  

**DoD Track G:** goal_eval + autonomy unit tests grün; keine ungebundene Autonomie.

---

### Track M — Memory bounded (später · nach C)

#### M1 — Adapter härten

- [x] vector_memory optional / disable-flag  
- [x] external_memory Mem0/Cognee/Letta OFF default  
- [x] Fail-soft Tests (Bridge timeout/exception/score-gate) + Letta labels **(2026-07-27)**

#### M2 — Procedure / Decay

- [x] Procedure capture + degrade  
- [x] forgetting_decay Modul  
- [ ] Eval-Metriken für Decay optional  

**DoD:** Kein Graph-Framework-Import; ein Retrieval-Pfad bleibt autoritativ.

---

### Track R — Research Brain (geparkt)

Nur mit **expliziter Owner-Freigabe** und **Feature-Flag / isoliertem Branch**.

| Schritt | Inhalt |
|---------|--------|
| R0 | Entry: C1–C3 stabil, Evals ≥ aktuell, kein Scope-Mix mit main-Features |
| R1 | Isoliertes Env + snnTorch Hello-World (Notebook), **kein** isaac_core-Replace |
| R2 | Optional Hybrid-Memory-Adapter hinter Flag, Accuracy vs. bestehendem Retrieval messen |
| R3 | Event-Loop-Experimente nur als Side-Module |
| R∞ | WBE/Dendritic: Forschungsnotiz, kein Produktionspfad |

**Anti-DoD:** snnTorch als Default-Dependency auf main; Microcircuit-Rewrite des Kernels.

---

## 6. MCP Deep-Dive

### Ist

| Capability | Stand |
|------------|--------|
| Tools | `isaac.audit_recent`, `query_memory`, `run_browser_action`, `search_web`, `start_task`, `task_status` |
| Resources | `resource://constitution`, `self-model`, `memory/blocks`, `procedures`, `audit/tail`, `isaac://tasks/recent`, `isaac://tools/registry` |
| Prompts | `research.next_step`, `tool.refine_input` |
| Transport | REST `/api/mcp/*`, JSON-RPC, stdio-Pfad |
| Privilege | tool_privileges + resource_privileges gemappt |
| Evals | mcp_eval (u. a. unknown tool rejected) |

### Target (Track C2)

1. Contract-Stabilität und Doku > neue Tools  
2. Jede Resource lesbar unter non-owner mit erwartetem Deny wo nötig  
3. Keine Subagent-Architektur  

---

## 7. Autonomie Deep-Dive

```
Owner-Ziele (goal_store)
    → Motivation-Tick (background)
    → Subgoal / plan|research|inquiry|work
    → Task (default ohne opportunistische Tools)
    → Learning facts source=goal:{id}
    → Digest an Owner (Fingerprint)

Owner-Autonomie (owner_autonomy)
    → due tasks in window
    → Constitution gate
    → max_per_cycle + backoff
```

**Grenze:** Constitution `protect_user`, Privilege, Audit — **kein** silent privilege escalation.  
**Nicht:** ungebundene Companion-Autonomie, Trust-Modeling gegen Owner.

---

## 8. Anti-Liste (verbindlich)

| Nicht tun | Warum |
|-----------|--------|
| Drive `task_state_machine` mergen | Doppelte Wahrheit |
| Drive `trace_otel*` als Parallel-Typen | ersetzt durch portable Export |
| Isaac Lab / Fake-Baselines als „Messung“ | misst Kernel nicht |
| Vector-Doppelpfad neben `build_retrieval_context` | Architekturbruch |
| LangGraph/CrewAI/OpenClaw wholesale | AGENTS Do-NOT |
| MCP-Subagent-Orchestrierung | AGENTS Do-NOT |
| Dashboard-Redesign / Phoenix-Import jetzt | Do-NOT |
| SNN/WBE auf main ohne Freigabe | Research-Track R |
| Phase-Nummern aus Elicit wörtlich („jetzt Phase 3“) | 1–4 + E2 + Goals already done |

---

## 9. Validierung (jedes Substep)

```bash
python3 -m py_compile isaac_core.py executor.py low_complexity.py memory.py relay.py logic.py \
  watchdog.py task_checkpoint.py decision_trace.py mcp_server.py mcp_registry.py
ISAAC_DISABLE_VECTOR_MEMORY=1 .venv/bin/python sanity_check.py
ISAAC_DISABLE_VECTOR_MEMORY=1 .venv/bin/python -m unittest \
  tests_phase_a_stabilization tests_state_io tests_provider_configuration
ISAAC_DISABLE_VECTOR_MEMORY=1 .venv/bin/python -m evals.eval_runner
```

Validierungsfälle A–G: `evals/replay_eval` (erweitert H–J).

---

## 10. Empfohlene Reihenfolge (nächste 90 Tage · ohne Brain)

| Priorität | Track | Arbeit |
|-----------|--------|--------|
| 1 | C1 | OTel/portable Export + EXECUTION-Metadaten |
| 2 | C2 | MCP Contract-Tests + Docs |
| 3 | C3 | Checkpoint soft-path polish |
| 4 | C4 | Self-model/learning bounded gaps |
| 5 | G1–G2 | Autonomie Docs/Live-Ops |
| 6 | C5 | CI Billing |
| 7 | M1 | Memory-Adapter fail-soft |
| ∞ | R | nur mit Owner-Freigabe |

---

## 11. Definition of Done (Master)

- [x] Phase 1–4 + E2.0 + Goal S0–S4 im Code und in AGENTS ehrlich ✅  
- [x] Drive-Patches bewertet und abgelehnt wo redundant/gefährlich  
- [x] Eval-Harness deckt Governance, Routing, Goals, MCP ab (96+)  
- [x] Track C1–C2 abgeschlossen (portable+MCP Contracts)  
- [ ] CI grün (Billing)  
- [ ] Research-Brain nicht mit Kernel-main vermischt  

---

## 12. Appendix — Mapping Elicit-Phasen → Isaac-Tracks

| Elicit-Label | Isaac-Track | Kommentar |
|--------------|-------------|-----------|
| Phase 3 Kern härten | **C** (teilweise done) | Nicht „neu starten“ — restliche Härtung |
| Phase 4 MCP/Dashboard | **C2** + Dashboard Do-NOT | MCP ja, Dashboard-Redesign nein |
| Phase 5 Memory | **M** | bounded only |
| Phase 6 Self-improve | **C4** + optional später | bounded_update |
| Brain/SNN PDF | **R** | geparkt |

---

*Isaac Kernel v5.3 · Master Roadmap v5 · 2026-07-24*  
*Erstellt aus AGENTS.md, Checklisten, Live-Code und Drive-Uploads (Elicit + Brain + Patches).*
