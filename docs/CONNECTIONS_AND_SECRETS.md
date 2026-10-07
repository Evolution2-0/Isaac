# Connections, Tools & Secrets (Isaac)

**Hard rule:** Never commit API keys, tokens, or credentials to Git.  
Secrets live in **environment**, **Render env**, or **local** `data/` (gitignored).

## A) Where secrets live

| Location | In Git? | Purpose |
|----------|---------|---------|
| `.env` | ❌ (gitignored) | Primary local secrets |
| Render / PaaS Env Vars | ❌ | Production (isaac-free) |
| `COPILOT_GITHUB_TOKEN` (OAuth/fine-grained, **not** classic `ghp_`) | ❌ | Copilot CLI/SDK companion — see `docs/COPILOT_AGENT.md` |
| `data/secrets_store.json` | ❌ (`data/` ignored) | Runtime secret refs for tools |
| `data/cli_auth_backup/` | ❌ | Export of CLI auths (`export_cli_auth.sh`) |
| `.env.example` | ✅ | **Placeholders only** |
| `docs/*` | ✅ | Documentation, no real keys |

### Bootstrap at startup

`secrets_bootstrap.bootstrap_secrets()` (from `isaac_core.main`):

1. Loads missing keys from `data/cli_auth_backup/all_cli_api_keys.env` and `.env` into `os.environ`
2. Mirrors known keys into `SecretsStore` (`provider.*`, `github.token`, `sentry.dsn`, …)
3. Never logs secret **values**

```bash
# Refresh local backup from installed CLIs
bash scripts/export_cli_auth.sh
```

### Resolve a secret in code

```python
from secrets_bootstrap import resolve_secret
token = resolve_secret("GITHUB_TOKEN") or resolve_secret("github.token")
```

---

## B) Bounded Tool Bridge (Isaac-native)

Flag: `ISAAC_TOOL_BRIDGE_ENABLED=1` (default **on**).

Registered tools (`kind=bridge`):

| Tool ID | Name | Needs | What it does |
|---------|------|-------|----------------|
| `bridge_github` | github_api | `GITHUB_TOKEN` / `GH_TOKEN` | repo / issues / PRs / `me` |
| `bridge_web_fetch` | web_fetch | — | HTTP GET + text excerpt (or DDG html query) |
| `bridge_grok_agent` | grok_agent | `ISAAC_GROK_AGENT_ENABLED=1`, `XAI_API_KEY` or `grok login` | Headless Grok CLI |

### Example prompts (after strategy allows tools)

```text
github: me
github: issues hansheute88/isaac
github: prs sc0rp0815/Isaac
fetch: https://example.com
code: prüfe offene Issues auf hansheute88/isaac
```

Tools still require:

- `strategy.allow_tools` (CODE/FILE/AGENT/SEARCH/RESEARCH, or `github:` / `fetch:` prefixes in chat)
- Constitution + privilege gates
- Executor eligibility (`tool_policy`)

### Catalog tools (professional_core)

Auto-registered at boot via `ensure_catalog_tools_registered()`:

- Search: DuckDuckGo, Wikipedia, Stack Overflow, Crossref  
- Code: GitHub repos/issues, PyPI, npm  
- Security (authorized/public only): crt.sh, Cloudflare DoH, RDAP, HackerOne directory  
- Research/Ops: Wayback CDX, Open-Meteo  

Bundles: `free_security_pack`, `free_ops_pack`, `free_dev_pack`, `professional_core`.

### Owner push (blockers only)

See `owner_notify.py` / `docs/OWNER_COMMANDS.md` — ntfy / webhook / Termux when
Isaac is hard-blocked (creds, keys, URL, browser off, 2FA).

**Not** included (by design — use Grok CLI / companions instead):

- Full MCP marketplace of Grok Build  
- Linear, Google Drive, Chrome DevTools as first-class kernel tools  
- Arbitrary shell without agent sandbox  

---

## C) Isaac vs Grok CLI — who uses what

| Capability | Isaac Kernel | Grok Build CLI (this agent) |
|------------|--------------|-----------------------------|
| Multi-provider LLM | ✅ `relay.py` | ✅ own model |
| Local tools registry | ✅ | ✅ different tool set |
| GitHub API | ✅ bridge (token) | ✅ github MCP / `gh` |
| Web fetch/search | ✅ bridge + search tools | ✅ web_search / web_fetch |
| Browser automation | ✅ optional Playwright | ✅ chrome/playwright MCP |
| Grok agent coding | ✅ companion / bridge | ✅ native |
| Sentry | ✅ `isaac_sentry` | — |
| Subagents / workflows | ❌ (not kernel) | ✅ |
| Image/video gen | ❌ | ✅ Imagine tools |
| Linear / Drive MCP | ❌ | ✅ if MCP connected |

### Keys used by Isaac

| Key | Used for |
|-----|----------|
| `GROQ_API_KEY` | Primary free LLM |
| `OPENROUTER_API_KEY` | Multi-model / ensemble |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | Gemini |
| `XAI_API_KEY` | Grok companion CLI |
| `GITHUB_TOKEN` / `GH_TOKEN` | GitHub bridge |
| `SENTRY_DSN` | Observability |
| `OPENAI_API_KEY` | Optional OpenAI-compat |
| `ANTHROPIC_API_KEY` | Optional Anthropic |
| `COGNEE_API_KEY` | Optional external memory |

### Keys used mainly by Grok CLI / host (not Isaac kernel)

| Key / file | Used for |
|------------|----------|
| `~/.grok/auth.json` | Grok Build OAuth |
| Vercel / Cloudflare MCP tokens | Host MCP only |
| Linear / Google Drive MCP | Host MCP only |

---

## Security checklist

- [ ] No real keys in commits (`git status` before push)
- [ ] `data/` and `.env*` ignored
- [ ] Rotate any key ever pasted into chat
- [ ] Render secrets only in dashboard / API env, not repo
- [ ] Prefer least-privilege GitHub tokens (repo scope, not `admin:org` unless needed)
