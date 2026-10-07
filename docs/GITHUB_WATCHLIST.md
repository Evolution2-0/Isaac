# GitHub Watchlist für Isaac (bounded)

Kuratierte Auswahl aus OpenLM/GitHub-Scan (2026). Isaac importiert **keine**
Agent-Frameworks wholesale — nur Muster in bestehende Module.

Siehe auch: `docs/OPEN_SOURCE_PATTERNS.md`, `docs/LOCAL_LLM.md`, `docs/OPEN_INTERPRETER.md`.

## Tier A — Nutzen (Infra / schon da / härten)

| Projekt | Rolle | Isaac-Status |
|---------|-------|--------------|
| [vLLM](https://github.com/vllm-project/vllm) | Lokales GPU-Serving | Ops: `ACTIVE_PROVIDER=local` |
| [SGLang](https://github.com/sgl-project/sglang) | Alternative zu vLLM | Ops: ein Backend reicht |
| [Mem0](https://github.com/mem0ai/mem0) | Memory-Layer | Adapter vorhanden (default OFF) |
| [Cognee](https://github.com/topoteretes/cognee) | Graph-Memory | Adapter vorhanden |
| [Letta](https://github.com/letta-ai/letta) | Companion + Memory-Ideen | `letta:` Companion |
| [Graphiti](https://github.com/getzep/graphiti) | Temporal facts | **Muster umgesetzt**: `goal_latest` > `goal_progress`, demote on update |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Tool-Schema / computer-use | `hermes_compat` + eigene Module |
| [browser-use](https://github.com/browser-use/browser-use) | Browser-Robustheit | **Muster in `browser.py`**: `_safe_goto`, page-alive, action-retry (kein Package) |
| [Agent_Memory_Techniques](https://github.com/NirDiamant/Agent_Memory_Techniques) | Lern-Notebooks | lesen, nicht installieren |
| [DSPy](https://github.com/stanfordnlp/dspy) | Declarative Prompt Optimization | **Muster nativ**: Eval-driven Prompt-Refinement in `learning_engine` & `runtime_settings.json` |

## Tier B — Goal-Autonomie (Muster — S0–S4 umgesetzt)

| Projekt | Übernommenes Muster |
|---------|---------------------|
| [goal-driven](https://github.com/lidangzzz/goal-driven) | Stop-Kriterien, Subgoal-Gates, Verification |
| [autogoals](https://github.com/ozankasikci/autogoals) | Goal-as-contract, kein freier Loop |

Umsetzung: `goal_store` / `motivation` / `goal_inquiry` / `goal_digest` — kein Import der Repos.

## Tier C — Companion only (expliziter Prefix)

| Projekt | Isaac |
|---------|--------|
| Open Interpreter (Codex harness) | `oi:` / `interpreter:` |
| Cline / OpenCode / Goose | **nicht** einbauen (redundant zu OI) |
| [Aider](https://github.com/Aider-AI/aider) | **Muster nativ** (`repo_map`, `code_edit`, `git_ops`) — kein Package |

## Tier D — Beobachten, nicht installieren

| Projekt | Warum nur lesen |
|---------|-----------------|
| [OpenClaw](https://github.com/openclaw/openclaw) | Zweiter Kernel — **nicht** installieren |
| NEOTH / Aivyx | Consent-tools / audit Ideen |

### OpenClaw — übernommene *Ideen* (kein Runtime)

1. **Session-Flush / Digest:** gebündelte Owner-Updates statt Event-Spam → `goal_digest.py`
2. **Channel-Gates:** riskante Aktionen brauchen Policy → Constitution + Privilege (bereits)
3. **Memory-Compaction:** „current“ vs Archiv → `goal_latest` vs demoted `goal_progress`

### Hermes — übernommene *Ideen* (kein Runtime)

1. **Tool-Schema mit Permissions** → `hermes_compat` + `tool_policy`
2. **Computer-use getrennt vom Chat** → `computer_use` + Strategy `allow_tools`
3. **Multi-layer memory labels** → typed facts/directives/procedures, keine Runtime-Übernahme

## Tier E — Anti-Liste

CrewAI, AutoGPT, LangGraph wholesale, Ray, DeepSpeed, Megatron, verl,
Mem0/Zep Cloud als Default, Companion-Theater, MCP-Subagent-Expansion,
OpenClaw/Hermes/Cline als Orchestrator.

**Drive-Patch-Pakete / Parallel-Roadmaps** (IsaacNew-main Folder, Juli 2026):  
`task_state_machine.py`, `trace_otel.py` (eigene Trace-Typen), Isaac-Lab-Mocks,
Vector-Pipeline-Doppelpfade, `MASTER_UNIFIED_ROADMAP_*` v1–v4 als aktive Phase —  
**nicht importieren.** Begründung: `docs/DRIVE_PATCH_REVIEW_2026-07-24.md`.

## Tier R — Research only (nicht auf main ohne Owner-Freigabe)

| Thema | Quellen | Isaac-Haltung |
|-------|---------|----------------|
| SNN / snnTorch / SpikingJelly / BrainCog | Drive *Gehirnähnliche Architektur* PDFs | Isolierter Branch + Flag; kein Default-Dependency |
| WBE / Dendritic planners | Research Brief | Sandbox-Notiz, kein Produktionspfad |
| Neuromorphic Event Loop (Lava/Rockpool) | Master Brain Roadmap | Nicht Executor-Replace |

Kanonische Einordnung: `docs/MASTER_ROADMAP_ISAAC_v5_2026-07-24.md` Track **R**.

## Status

Goal-Autonomie S0–S4 **DONE** (2026-07-18): Store, Motivation, Inquiry/Research, Digest, Temporal Facts — siehe `06_goal_autonomy_checklist.txt`.  
Master-Roadmap v5 + Eval-Harness **96/96** (2026-07-24).  
Browser-use wholesale: **deferred** bis konkreter Defekt.
