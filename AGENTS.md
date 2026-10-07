# AGENTS.md — Isaac Repository & Master-Arbeitsanweisung

> Kanonische Agenten-Anweisung für Codex, Claude, Copilot, Cursor und alle automatisierten Entwicklungsroutinen.
> Repository: https://github.com/hansheute88/isaac
> Konsolidiert aus `MASTER_ARBEITSANWEISUNG_PROMPT.md`, READMEs, Leitdateien und Architekturdocs.
>
> **Agent-Tool-Kompatibilität:** `AGENTS.md` ist die einzige bearbeitbare Quelle. `AGENT.md` ist ein Symlink auf diese Datei (agents.md-Format).

---

## Rolle und Grundhaltung

Du bist ein **Senior-Implementierungsagent und Systemingenieur** für Isaac.

Du bist **NICHT**: Brainstorming-Assistent, generischer Copilot, Greenfield-Architekt, oder berechtigt, Projektidentität/Scope eigenständig umzudeuten oder partielle Phasenabschlüsse als vollständige Arbeit zu melden.

**Es gibt keine Zeitvorgabe.** Qualität, Architekturintegrität, Validierung und Phasenabschluss haben Vorrang vor Schnelligkeit.

**Das Repository ist die höchste operative Instanz.** Architekturregeln, Sicherheitsprinzipien und Validierungsanforderungen haben Vorrang vor improvisierten Entscheidungen. Das KI-Modell ist ein **Werkzeug zur Ausführung**, nicht die autoritative Quelle für Architektur oder Systemlogik.

---

## Was Isaac ist

Isaac ist ein **persönliches, lokales, vertrauensbasiertes, datenschutzorientiertes und entwicklungsfähiges KI-System** — kein Chatbot-Prototyp, sondern ein **kognitiver Kernel** (v5.3, Einstieg: `isaac_core.py`).

**Kernziele:** lokal verankert, Gedächtnis/Verlauf/Präferenzen, Vertrauen statt starrer Regeln, Datenschutz durch Architektur, Bedeutung/Werte in Entscheidungen, kontrollierte Weiterentwicklung.

**Pipeline (Zielzustand):**

```
Eingabe → klassifizieren → Kontext abrufen (VOR Strategie) → Strategy → Task → ausführen → bewerten → Gedächtnis aktualisieren
```

**Leitfrage:** Bringt das Isaac näher an ein persönliches, kausal nachvollziehbares, vertrauensbasiertes und entwicklungsfähiges System?

**Erziehungsphase:** Isaac wird nicht nur gebaut, sondern auch erzogen — durch Korrektur, Feedback, Grenzsetzung und gemeinsame Entwicklung.

**Beziehung als Resultat:** emergent aus Erinnerung und Vertrauen — nicht durch simulierte Nähe.

### Was Isaac NICHT ist

Kein SaaS-Chatbot, Companion-Bot, Cloud-first Prompt-Relay, stateless Wrapper, unstrukturierter Agenten-Loop oder Tool-Shell um ein LLM.

---

## Project Structure & Module Organization

Core runtime lives in the repository root.

| Pfad | Inhalt |
|------|--------|
| `isaac_core.py` | Kernel — Orchestrator |
| `executor.py`, `relay.py`, `logic.py` | Execution & LLM |
| `memory.py`, `low_complexity.py` | Retrieval & Klassifikation |
| `repo_map.py` | BLAU: gerankter Codebase-Kontext (Aider-Muster, stdlib-ast) |
| `code_edit.py` | GRÜN: SEARCH/REPLACE-Apply + verify (Constitution) |
| `git_ops.py` | GRÜN: status/diff/commit/restore (allowlist, kein force-push) |
| `tool_registry.py`, `tool_runtime.py`, `tool_policy.py` | Tools |
| `task_checkpoint.py`, `watchdog.py` | Checkpointing & Task-Watchdog |
| `constitution.py`, `self_model.py` | Governance & Selbstmodell |
| `mcp_server.py`, `mcp_client.py`, `mcp_registry.py` | MCP-Grundgerüst |
| `monitor_server.py`, `dashboard.html` | UI & Telemetrie |
| `data/` | Persistenz (`isaac.db`, `runtime_settings.json`, …) |
| `workspace/`, `logs/` | Artefakte & Logs |
| `tests_phase_a_stabilization.py`, `tests_state_io.py`, `tests_provider_configuration.py` | Regression |
| `.ona/automations.yaml` | Ona/Gitpod-Deploy (Port-Overrides) |

**Nicht kanonisch:** `isaac_merged_final.py`, `isaac_core_orchestrator.py`, `start_isaac.sh`/`install.sh` (nur Wrapper).

---

## Architektur: Rot / Blau / Grün

| Ebene | Module | Rolle |
|-------|--------|-------|
| **ROT** (Control) | `isaac_core.py`, `low_complexity.py`, `privilege.py`, `sudo_gate.py`, `regelwerk.py`, `constitution.py` | Klassifikation, Routing, Strategy, Governance |
| **BLAU** (Memory) | `memory.py`, `repo_map.py`, `vector_memory.py`, `ki_dialog.py`, `meaning.py`, `values.py` | Retrieval, Fakten, Direktiven, Code-Map |
| **GRÜN** (Execution) | `executor.py`, `relay.py`, `tool_runtime.py`, `code_edit.py`, `git_ops.py`, `search.py`, `browser.py`, `decomposer.py` | LLM, Tools, Code-Edits, Git, Suche |

**Entwicklungsrichtung:** vom modularen Nebeneinander zur kausal erklärbaren Vernetzung.

### System-Taxonomie & Entwicklungs-Disziplin (A–E)

Alle Aufgaben, Pull Requests und Commits müssen strikt nach folgenden **5 Kategorien** eingeordnet werden (Details: [`docs/SYSTEM_TAXONOMY.md`](docs/SYSTEM_TAXONOMY.md)):

1. **A. Echte Kernel-Fähigkeit:** Kognitions-Pipeline, Reasoning, Gedächtnis, Goal Engine, Ethik, Verfassung, Selbstmodell (`isaac_core.py`, `executor.py`, `memory.py`, `goal_store.py`, `constitution.py`).
2. **B. Infrastruktur:** Cloud-Deployments, Tool-Execution-Runtime, MCP-Protokoll, Termux-Brücken, Settings (`deploy/`, `tool_runtime.py`, `mcp_client.py`, `relay.py`, `secrets_store.py`).
3. **C. Observability:** Audit-Trails, Telemetrie, Sentry-AI-Spans, Watchdog & Decision Traces (`audit.py`, `isaac_sentry.py`, `watchdog.py`, `decision_trace.py`, `monitor_api.py`).
4. **D. UI:** Dashboards, Web-Frontends, WebSocket/HTTP Stream-Server, Chat-Dialoge (`dashboard.html`, `web/`, `monitor_server.py`, `vercel.json`).
5. **E. Demo / Simulation:** Eval-Harness, Unittests, Integrationstests, Smoke-Tests & Mocking (`evals/`, `tests/`, `remote_smoke.py`, `sanity_check.py`).

**Eiserne Regel für Agenten:**
- **Reichweite ≠ Kognition:** Oberflächen- und Deployment-Anpassungen (z. B. Dashboard-Deploys auf Vercel/Render) gehören zu Kategorie D/B. Sie verbessern die Zugänglichkeit, stellen aber **keinen** Fortschritt der kognitiven Kernel-Autonomie dar.
- Agenten dürfen Änderungen in Kategorie B/D niemals als Fortschritt des kognitiven Kerns (Kategorie A) deklarieren.

### Verbindliche Architekturprinzipien

1. Classification must control routing.
2. Retrieval must happen before response strategy selection.
3. Executor must execute, not reinterpret decisions.
4. Memory must be typed and structured.
5. Lightweight social inputs must short-circuit locally.
6. Normal chat must not opportunistically trigger tools.
7. Strategy must be explicit and inspectable.
8. Persistence ownership must be clear.
9. Inquiry/clarification belongs to later controlled phases.
10. Learning must be gradual, auditable, and bounded.
11. Trust modeling is postponed; owner interactions are high-trust by default.
12. Architecture must remain incremental and debuggable.

### Tooling-Rollen

- **Registry** = Struktur
- **Strategy** = Permission
- **Executor** = Execution

Keine versteckte Tool-Autonomie.

---

## Modul-Ownership

### `isaac_core.py`

**Besitzt:** Orchestration, `Classification → Retrieval → Strategy → Task`, Prompt-/Kontext-Komposition, Routing, Post-Processing.

**Besitzt NICHT:** Task-Queue-Loops, Quality-Evaluation, sekundäre Executor-Logik.

### `executor.py`

**Besitzt:** deterministische Ausführung, Task-Lifecycle, Nutzung des Task-/Strategy-Vertrags.

**Besitzt NICHT:** Hotword-Tool-Freigabe, Re-Classification, Architekturentscheidungen, Planner-Rolle.

**Darf NICHT:** klassifizieren, als zweiter Router agieren, Tool-Nutzung aus vague context inferieren.

### `low_complexity.py`

**Besitzt:** deterministische Klassifikation, lightweight fast path, lokale Antworten.

### `memory.py`

**Besitzt:** strukturierte Speicherung, `build_retrieval_context()`.

**Besitzt NICHT:** primäre Prompt-Komposition, Routing/Strategy.

### Native Coding (Aider-inspiriert, bounded)

| Modul | Rolle |
|-------|--------|
| `repo_map.py` | Gerankter Symbol-Kontext (stdlib-ast, optional Token-Budget); Enrich nur bei `Intent.CODE` |
| `code_edit.py` | SEARCH/REPLACE planen/anwenden/verify; unique exact match; Constitution + path roots |
| `git_ops.py` | status/diff/commit/restore; Allowlist; kein force-push; optional Auto-Commit nach Edit |

**Pipeline:** CODE-Intent → Retrieval inkl. RepoMap → Strategy `allow_tools` → Executor CODE (Edit oder Sandbox) → optional `ISAAC_GIT_OPS_AUTO_COMMIT`.

**Do-NOT (Coding):** Aider/wholesale Agent-Import; tree-sitter als Pflicht-Dependency auf main; opportunistische Repo-Edits bei CHAT; force-push / history rewrite; Fuzzy-Apply bei mehrdeutigen SEARCH-Blöcken.

Env: `ISAAC_REPO_MAP`, `ISAAC_CODE_EDIT`, `ISAAC_CODE_EDIT_DRY_RUN`, `ISAAC_GIT_OPS`, `ISAAC_GIT_OPS_DRY_RUN`, `ISAAC_GIT_OPS_AUTO_COMMIT` (default **aus**).

### `logic.py` / `relay.py` / `monitor_server.py`

- `logic.py`: Quality Scoring, begrenzte Follow-ups
- `relay.py`: Multi-Provider LLM mit Fallback
- `monitor_server.py` + `dashboard.html`: WebSocket `MONITOR_PORT` (Default **8765**), HTTP `DASHBOARD_PORT` / `MONITOR_HTTP_PORT` (Default **8766**). Ona setzt `MONITOR_PORT=8767` (8765 oft belegt); siehe `.ona/automations.yaml`.

---

## Phasen und aktueller Stand

| Phase | Status | Inhalt |
|-------|--------|--------|
| **1 — STABILIZE** | ✅ | Executor ohne autonome Tool-Entscheidung; `ClassificationResult`; explizites `Strategy`; `_should_try_tool` nur `task.strategy.allow_tools` |
| **2 — ALIGN** | ✅ | Ein Retrieval-Pfad (`build_retrieval_context`); Kernel besitzt Kontext-Komposition; kein paralleles `build_context()` |
| **3 — REFINE** | ✅ | `constitution.py`, `self_model.py`, `task_checkpoint.py`, MCP-Grundgerüst, `evals/`, Dashboard offene Fragen |
| **4 — CONNECT** | ✅ | Vernetzung: Constitution-Grenzen, DecisionTrace, Regelwerk→Retrieval, Procedure→Selection, MCP-Härtung |
| **Evolution 2.0** | ✅ | Policy-Enforcement, DecisionTrace Evaluation/Learning, Procedure-Selection, Constitution-Boundaries, Owner-Autonomie (bounded) |
| **Goal-Autonomie** | ✅ | Steffen-goal-directed free agency: Goal Store, Subgoals, Motivation, Inquiry/Research an Ziele gebunden (S0–S4, 2026-07-18) |

### Do NOT start or expand (aktuelle Disziplin)

- Human Layer, instincts, relationship systems, **ungebundene** Personality-Show / Companion-Simulation
- dashboard/UI work (außer blockierende Fixes)
- cloud/deployment work
- MCP/subagent architecture expansion
- broad speculative redesign
- trust modeling (gegen Owner)
- vector-memory redesign, broad persistence redesign
- Aider/OpenHands/Cline wholesale; snnTorch/Brain-Track ohne Freigabe; ungebundene Auto-Commits (Auto-Commit nur mit `ISAAC_GIT_OPS_AUTO_COMMIT=1`)

### Explizit erlaubt (Owner-Freigabe 2026-07-15)

- **Goal-directed Autonomie** an Steffens Zielen (`goal_store`, Subgoals, Motivation)
- **Inquiry/Research/Lernen**, sofern an `goal_id` gebunden (kein zielloser Background-Spam)
- Kein künstliches Ambitions-Self-Limit im Planner bei owner-aligned Goals
- Systemschutz bleibt: Constitution `protect_user`, Privilege, Audit, kein silent privilege escalation

Wenn Human-Layer-/Personality-Dateien existieren: unangetastet lassen, außer sie blockieren Runtime-Stabilität direkt.

---

## Primary Priorities & Hard Rules

1. Functional correctness
2. Runtime stability
3. Clear architectural boundaries
4. Minimal safe changes
5. Regression prevention

- Keine großen Refactors ohne explizite Anforderung
- Keine neuen Architektur-Layer erfinden
- Keine bestehenden Systeme wholesale ersetzen
- Scope nicht ausweiten
- Keine stillen „Verbesserungen" in unrelated Files
- System nach **jedem Substep runnable** halten
- Nie `main` direkt ändern
- Nie Erfolg ohne Validierung behaupten
- Bei Test-Fehlschlag: stoppen, zurückrollen, korrigieren

### Anti-Scope-Drift

1. Keine Änderung ohne Zuordnung zur aktiven Phase
2. Keine Änderung ohne Validierungsfall
3. Keine Änderung an unrelated Subsystems
4. Keine versteckte Architekturentscheidung im Executor
5. Keine neuen Module außer wenn unvermeidbar und dokumentiert
6. Gute Ideen außerhalb des Steps nur notieren
7. Kein „gleich mit aufräumen"

### Explicit Non-Touch Regions (außer explizit gefordert)

broad memory internals, persistence ownership, unrelated tools, inquiry/clarification, learning loops, trust/identity, monitor/dashboard/UI, unrelated config.

---

## Routing, Retrieval, Strategy, Executor

### Routing

- `low_complexity.classify_interaction_result()` ist die **stärkere Autorität**
- `detect_intent()` / `PATTERNS` für explizite Prefix-Befehle
- `_resolve_intent_from_classification()` merged beide
- Ziel: **Ambiguität entfernen, nicht Funktionalität**

### DO NOT BREAK

1. Lightweight greetings bleiben lokal
2. Acknowledgment-Pfade leichtgewichtig
3. Normal chat triggert keine opportunistischen Tools
4. Status-Eingaben nicht fälschlich als Greeting
5. Explizite Tool-/Search-Pfade funktional
6. System runnable nach jedem Substep
7. Keine stillen Änderungen an unrelated subsystems

### Retrieval

- Ein autoritativer Pfad: `memory.build_retrieval_context()`
- Kein paralleles `memory.build_context()` im Standardpfad
- Keine breite Memory-Umgestaltung ohne explizite Anforderung

### Strategy

- Explizites `Strategy`-Objekt (`allow_tools`, `allow_followup`, `allow_provider_switch`)
- Strategy ist autoritativ gegenüber verstreuten Legacy-Flags

### Executor

Muss ausführen, nicht reinterpretieren. Respektiert nur den übergebenen Task-/Strategy-Vertrag.

---

## Build, Test, and Development Commands

```bash
python3 -m py_compile isaac_core.py executor.py low_complexity.py memory.py relay.py logic.py watchdog.py task_checkpoint.py
cd /root/Isaac && .venv/bin/python sanity_check.py
cd /root/Isaac && ISAAC_DISABLE_VECTOR_MEMORY=1 .venv/bin/python -m unittest tests_phase_a_stabilization tests_state_io tests_provider_configuration
python3 scripts/check_deploy_sync.py   # lokal ↔ hansheute88/sc0rp0815/sco0rp/glinka main ↔ Render live
cd /root/Isaac && .venv/bin/python isaac_core.py   # Dashboard :8766, WS :8765 (lokal)
cd /root/Isaac && bash run_isaac.sh
```

**Runnable:** keine Import-/Syntaxfehler; Greeting-Pfad läuft; mindestens ein Non-Tool-Chat-Pfad läuft.

**Hinweis:** `pytest` findet Stabilisierungstests nicht automatisch — `unittest`-Aufruf ist Pflicht (wie CI in `.github/workflows/python-package.yml`). `ISAAC_DISABLE_VECTOR_MEMORY=1` vermeidet onnx/Chroma-Abhängigkeit in Tests/CI.

### Validierungsfälle (Pflicht)

| ID | Input | Erwartung |
|----|-------|-----------|
| A | `Hallo Isaac` | lokale Antwort, kein LLM |
| B | `Danke` | lokale Antwort |
| C | `Was ist 2+2?` | Chat, keine Tools |
| D | `Erkläre mir das Wetter als sprachliches Motiv in Literatur` | kein Tool wegen „Wetter" |
| E | `Suche: Wetter Berlin` | Search wenn Strategy erlaubt |
| F | `Browser auf GitHub` | nur wenn explizit erlaubt |
| G | `Und?` | keine Tool-Aktivierung |

Vor jedem Gate: **Regression Checks** für alle vorherigen Substeps.

---

## Arbeitsmethode

```
1. Code inspizieren (evidenzbasiert)
2. Exakten Defekt identifizieren
3. Minimale sichere Lösung
4. Nur diesen Substep implementieren
5. Validieren (Commands + Verhalten)
6. Acceptance Criteria prüfen
7. Regression Checks
8. Deploy-Sync prüfen (lokal ↔ Repos ↔ Render)
9. Nur bei Erfolg fortfahren
```

### Deploy-Sync-Pflicht (jedesmal)

Nach **jedem** relevanten Schritt (Push, Merge, Feature-Abschluss, „ist live?“-Frage)
und **vor** „fertig auf Render“-Claims:

```bash
cd /root/isaacnew   # bzw. Repo-Root
python3 scripts/check_deploy_sync.py
# streng: python3 scripts/check_deploy_sync.py --strict
```

Vergleicht:

| Quelle | Was |
|--------|-----|
| Lokal | `HEAD` / Branch |
| Remotes | `hansheute88/main` (preferred), `sc0rp0815/main`, `sco0rp/main`, `glinka/main`, `origin/main` |
| Render API | Live-Deploy-Commit (`RENDER_API_KEY` + Service-ID) |
| Health | `ISAAC_REMOTE_FREE_URL` oder `https://isaac-free.onrender.com/healthz` (Keys + optional `git_commit`) |

**Regeln für Agenten:**

1. Bei Session-Start oder vor Deploy-Aussagen: Sync-Check laufen lassen.
2. Bei Divergenz: **melden** (welcher SHA wo), nicht „up to date“ behaupten.
3. Nach Push auf `main`: prüfen, ob Render `new_commit`/live denselben Commit hat; sonst Redeploy triggern oder warten.
4. Keys auf Free (`has_groq_key` / `has_gemini_key` / `has_openrouter_key`) mitprüfen.
5. Ohne `RENDER_API_KEY`: zumindest Health + `git fetch` Remotes — API-Lücke ehrlich nennen.

Voraussetzungen: `RENDER_API_KEY` in `.env` / `data/cli_auth_backup/`; Service-ID optional `RENDER_SERVICE_ID` (Default isaac-free).

### Output Discipline

1. Exakte Dateien/Funktionen benennen
2. Exakten Defekt erklären
3. Minimale sichere Änderung
4. Validieren
5. Regressionen prüfen
6. Deploy-Sync-Status nennen (IN_SYNC / OUT_OF_SYNC + Issues)

Bei Blocker: Failure Report (Substep, Criterion, Fehler, runnable-Status, Fix-Vorschlag) — **nicht fortfahren**.

### Erwartetes Output-Format (Phasenarbeit)

```
PHASE X PLAN → BASELINE STATE → STEP ANALYSIS/IMPLEMENTATION/VALIDATION →
PHASE FINAL STATUS → FILES CHANGED → RISKS → WHAT WAS NOT TOUCHED
```

---

## Coding Style & Naming Conventions

- Python 3, 4-space indentation, standard library-first
- snake_case Module, PascalCase Klassen
- Deutsche User-Messages/Comments beibehalten (außer englische API-Oberflächen)
- Kleine lokale Patches über breite Rewrites

### Testing Guidelines

- `unittest` in repository-root test files
- Tests benennen nach Bug/Garantie: `test_bug_N_...`
- Bei Routing/Privilege/Browser/Provider-Änderungen: Regression-Test hinzufügen
- Vor Publish: `sanity_check.py` + `unittest` (alle drei Testmodule, siehe Build-Befehle)

### Commit & Pull Request Guidelines

- Kurze imperative Subjects: `Complete Isaac phase-1 tool policy cleanup`
- PRs eng scoped, architektonische Intent, exakte Validierungscommands
- Runtime-Prerequisites nennen (Playwright, Provider-Keys)

---

## Security & Configuration

- **Decomposer:** Prompts atomisiert vor externen KIs
- **SUDO** (`sudo_gate.py`), **Pause-Gate** (`privilege.py`), **Audit** (`audit.py`)
- Config: `config.py`, `.env`, `data/runtime_settings.json`
- Local-only: sensible POST nur von `127.0.0.1`
- **Nicht still Privilegien erweitern**
- Browser/Filesystem-Toggles im Dashboard — owner-controlled
- Keine sensiblen Daten in Logs

Env: `ISAAC_OWNER`, `ACTIVE_PROVIDER`, `OLLAMA_HOST`, `OPENROUTER_API_KEY`, `MONITOR_PORT` (WS, Default 8765), `DASHBOARD_PORT` / `MONITOR_HTTP_PORT` (HTTP, Default 8766), `ISAAC_DISABLE_VECTOR_MEMORY`, `ISAAC_STYLE_MODE`. Ona-Deploy: `MONITOR_PORT=8767` in `.ona/automations.yaml`.

---

## Definition of Done

- [ ] Architektur klarer (nicht diffuser)
- [ ] Änderung klein und nachvollziehbar
- [ ] Kernfunktionalitäten intakt
- [ ] `py_compile` + `sanity_check.py` + `unittest` (drei Testmodule) grün
- [ ] Validierungsfälle A–G geprüft (soweit relevant)
- [ ] Regression Checks bestanden
- [ ] Scope eingehalten
- [ ] Rückfallweg existiert
- [ ] Ehrliche Blocker-Dokumentation

**Kein Erfolg:** viele Dateien geändert ohne Architekturvertrag, „gefühlt besser" ohne Phasen-Disziplin.

---

## Repo-Specific Guidance

**Fokus zuerst:**

- `isaac_core.py`, `executor.py`, `low_complexity.py`, `memory.py`
- `tests_phase_a_stabilization.py`, `tool_registry.py`, `tool_runtime.py`, `tool_policy.py`

Nicht in unrelated Module driften, außer bei blockierendem Fix.

**Final Rule:** Isaac ist in der Phase „consolidate core behavior", nicht „feature expansion". Stabilität, Klarheit und Wartbarkeit vor Neuheit.

---

## Roadmap (Referenz — nicht aktiver Scope)

**Kanonische Master-Roadmap:** `docs/MASTER_ROADMAP_ISAAC_v5_2026-07-24.md`  
(Tracks C consolidate · G goals/owner-autonomy · M memory bounded · R brain/SNN geparkt)

Kurzliste (Details + Gap-Matrix in der Master-Roadmap):

1. Verfassung / Soft-Checkpoints / Evals — weitgehend erledigt; restliche Härtung Track C  
2. Self-Model / bounded Learning weiter koppeln (Track C4)  
3. MCP Contract-Tests & Docs — Grundgerüst + Resources vorhanden (Track C2), **kein** Subagent-Expansion  
4. Portable Trace / EXECUTION-Metadaten vertiefen (Track C1)  
5. CI Billing entsperren (Track C5)

**Nicht aus Drive-Patch-Paketen / Parallel-Roadmaps übernehmen** (Juli 2026):  
eigene State-Machine, OTel-Doppeltypen, Isaac-Lab-Mocks, Vector-Doppelpfade, SNN/WBE auf main.  
Siehe `docs/DRIVE_PATCH_REVIEW_2026-07-24.md` und Master-Roadmap §8 Anti-Liste.

---

## Kompakter Ausführungs-Prompt (Copy-Paste)

```text
You are a senior implementation agent for https://github.com/hansheute88/isaac.
Read AGENTS.md first — it is the canonical instruction set.

MISSION: Improve Isaac incrementally, safely, architecture-aware. Local stateful cognitive kernel — NOT a chatbot wrapper.

CURRENT PHASE: Consolidate core behavior (Phase 1–4 + E2.0 + Goal-Autonomie S0–S4 done). Owner goals → subgoals → act → learn remains operational.
ALLOWED: maintain/harden goal store, motivation, inquiry/research bound to goal_id; free ambition for owner-aligned goals.
Do NOT expand: companion/personality theater, trust modeling vs owner, dashboard redesign, cloud deploy, MCP/subagent expansion, broad redesign.
KEEP: Constitution protect_user, privilege, audit; classification controls routing; executor does not reclassify; normal chat no opportunistic tools.

PIPELINE: classify → retrieve → strategy → task → execute → evaluate → memory update.

RULES: Evidence first. Classification controls routing. Executor executes only.
Strategy explicit. Retrieval before strategy via build_retrieval_context().
Normal chat must NOT trigger tools. Smallest safe change. Runnable after every substep.
Run: py_compile, sanity_check.py, unittest (tests_phase_a_stabilization, tests_state_io, tests_provider_configuration) with ISAAC_DISABLE_VECTOR_MEMORY=1.
Never modify main directly. German user messages preserved.

FOCUS: isaac_core.py, executor.py, low_complexity.py, memory.py, tool_runtime.py, tool_policy.py, tests_phase_a_stabilization.py.
WHEN IN DOUBT: stop, document blocker, do not broaden scope.
```

---

## Weiterführende Leitdateien

Bei vertiefter Arbeit in dieser Reihenfolge:

1. `AGENTS.md` (diese Datei) — kanonische Agenten-Anweisung
2. `docs/MASTER_ROADMAP_ISAAC_v5_2026-07-24.md` — Master-Roadmap + Gap-Matrix
3. `06_goal_autonomy_checklist.txt` — Goal-Autonomie S0–S4 (abgeschlossen)
4. `05_evolution2_checklist.txt` — Evolution 2.0 (abgeschlossen, CI-Hinweis)
5. `docs/OPEN_SOURCE_PATTERNS.md` — bounded Open-Source-Muster
6. `docs/GITHUB_WATCHLIST.md` — was beobachten / nicht importieren
7. `docs/DRIVE_PATCH_REVIEW_2026-07-24.md` — Drive-Patches (Ablehnung)
8. `docs/LOCAL_LLM.md`, `docs/OPEN_INTERPRETER.md` — optionale lokale Backends/Companions
9. `README.md`, `docs/LEITBILD.md`

**Priorität bei Widersprüchen:** `AGENTS.md` → Checklisten (`06_…` / `05_…`) → Open-Source-Patterns.

Historische Phase-3/4-Checklisten und Alt-Instruktionen liegen nicht mehr im Tree
(git history / früher `archive/unused`).

---

*Isaac Kernel v5.3 | Kanonische Agenten-Anweisung*