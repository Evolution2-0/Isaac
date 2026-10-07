# Automation Pipeline (Isaac ↔ Render ↔ Cognee ↔ Letta ↔ GitHub)

Bounded multi-system automation. Isaac remains the kernel orchestrator;
companions stay opt-in tools.

## Status command

```text
status:pipeline
status:pipeline sync
pipeline status
automation status
status:smoke
status:smoke wake
status:smoke full
```

`sync` writes an ops snapshot into **Cognee** (requires write enabled).  
`status:smoke full` runs Health + Chat A/B/C/G against Free, samples Sentry, optional Cognee.

## Remote smoke + Render Free anti-sleep

Render Free sleeps after **~15 minutes** without inbound traffic. A sleeping
instance **cannot wake itself** — keep-alive must run from outside.

| Mode | What | Default interval |
|------|------|------------------|
| `wake` | `GET /healthz` only | **600s (10 min)** — hard-capped ≤840s |
| `full` | Health + chat A/B/C/G + Sentry (+ Cognee) | 7200s (2h) |

```bash
# one-shot
python3 scripts/remote_smoke_suite.py --mode wake
python3 scripts/remote_smoke_suite.py --mode full

# continuous anti-sleep on always-on host (laptop / S8)
ISAAC_REMOTE_SMOKE=1 python3 scripts/remote_smoke_suite.py --loop
```

**GitHub Actions:** `.github/workflows/remote-smoke.yml` runs every **10 minutes**
(`*/10 * * * *`) — under the sleep threshold. Even hours → full suite once;
otherwise wake only.

| Flag | Default | Meaning |
|------|---------|---------|
| `ISAAC_REMOTE_SMOKE` | `0` | Background keep-alive (local/S8 loop) |
| `ISAAC_REMOTE_SMOKE_WAKE_INTERVAL_S` | `600` | Keep-alive interval (**must be <900**) |
| `ISAAC_REMOTE_SMOKE_FULL_INTERVAL_S` | `7200` | Full chat suite interval |
| `ISAAC_REMOTE_SMOKE_WRITE_COGNEE` | follows memory write | Write report to Cognee |
| `ISAAC_REMOTE_SMOKE_PUSH_ON_FAIL` | `1` | Owner push on red suite |
| `RENDER_URL` / `ISAAC_REMOTE_FREE_URL` | isaac-free.onrender.com | Target |

Reports: `data/remote_smoke_last.json`, state `data/remote_smoke_state.json`.

### R2 — Deploy-Gate (beides: keep-alive **und** post-deploy)

Nach Push auf `main` wartet CI bis Free `healthz.git_commit` den neuen SHA hat,
dann Full-Smoke (A/B/C/G + Sentry + optional Cognee). Parallel bleibt der
**10-Minuten-Wake** aktiv (Anti-Sleep).

```bash
# wait until Free serves this SHA, then full suite
python3 scripts/remote_smoke_suite.py --mode post-deploy --expect-sha "$(git rev-parse HEAD)"

# deploy sync + smoke if already live
python3 scripts/check_deploy_sync.py --smoke

# deploy sync + wait for HEAD on Free + smoke
python3 scripts/check_deploy_sync.py --smoke-wait
```

GH Actions jobs:

| Job | Trigger | Mode |
|-----|---------|------|
| `post-deploy-smoke` | `push` main / dispatch full\|post-deploy | wait + full |
| `keep-alive` | schedule `*/10` / dispatch wake\|auto | wake or 2h full |

## Env flags

| Flag | Default | Meaning |
|------|---------|---------|
| `ISAAC_AUTO_PIPELINE` | `0` | Background ops→Cognee automation |
| `ISAAC_AUTO_REDEPLOY` | `0` | Optional auto-redeploy on drift (Stage 2) |
| `ISAAC_GH_AUTO_PR` | `0` | Aggressive auto-PR (Stage 3) |
| `ISAAC_GH_AUTO_MERGE` | `0` | Never merge main by default |
| `ISAAC_GH_REPO_ALLOWLIST` | `hansheute88/isaac,sc0rp0815/Isaac,sco0rp/IsaacNew` | Comma-separated repos |
| `ISAAC_GH_MAX_PR_PER_DAY` | `3` | Rate limit (Stage 3) |
| `ISAAC_COGNEE_ENABLED` | `0` | Cognee adapter |
| `ISAAC_COGNEE_ALLOW_CLOUD` | `0` | Cloud REST |
| `COGNEE_BASE_URL` / `COGNEE_API_KEY` | | Tenant |
| `ISAAC_EXTERNAL_MEMORY_WRITE` | `0` | Allow remember |
| `ISAAC_LETTA_ENABLED` | `0` | Letta CLI companion |
| `ISAAC_COPILOT_AGENT_ENABLED` | `0` | Copilot / CCA |
| `ISAAC_COPILOT_CLOUD_REPO` | | `owner/repo` for cloud tasks |
| `ISAAC_AGENT_AUTO_SELECT` | `0` | Marker-based companion pick |
| `SENTRY_DSN` / `SENTRY_AUTH_TOKEN` | | Errors + triage API |

## Owner autonomy tasks

`daily_stack_health` (action `automation_ops` / `stack_health`):

- Window ~07–22h, every 12h
- Runs `run_stack_health_cycle(force_write=True)` → status + Cognee snapshot
- Needs admin/owner mode like other autonomy tasks

`daily_remote_smoke` (action `automation_ops` / `remote_smoke`):

- Window ~08–22h, every 12h
- Full remote smoke against Free URL + optional Cognee/Sentry
- Keep-alive pings remain the 10-minute GH Action / `ISAAC_REMOTE_SMOKE` loop

Enable pipeline write for background:

```bash
export ISAAC_AUTO_PIPELINE=1
```

## Stages

0. Status wiring — **done in code** (`automation_pipeline.py`)
1. Memory/ops sync — Cognee snapshot + autonomy task
2. Sentry/deploy actions (redeploy flag)
3. GitHub auto-PR (policy + CCA)
4. Letta local parity

## Policy (aggressive GH later)

- Owner-equivalent only
- goal_id recommended / required for auto PR
- Branch `auto/…` only, never direct main
- No force-push, no auto-merge unless explicit flag
- Kill-switches above

## Code

| File | Role |
|------|------|
| `automation_pipeline.py` | Status probes + ops→Cognee |
| `remote_smoke.py` | Wake/full smoke, Cognee/Sentry report, anti-sleep intervals |
| `scripts/remote_smoke_suite.py` | CLI + `--loop` keep-alive |
| `.github/workflows/remote-smoke.yml` | Cron every 10 min against Free |
| `owner_autonomy.py` | `daily_stack_health` + `daily_remote_smoke` |
| `isaac_core.py` | `status:pipeline` / `status:smoke` |
| `docs/AUTOMATION_PIPELINE.md` | This doc |
