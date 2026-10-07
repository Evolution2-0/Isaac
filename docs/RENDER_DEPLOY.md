# Isaac auf Render free deployen

## Voraussetzungen

1. GitHub-Account mit Zugriff auf **hansheute88/isaac** (preferred; legacy: sc0rp0815/Isaac)
2. Kostenloser Account: https://dashboard.render.com/register  
3. Groq-Key: https://console.groq.com/keys  
   (neuen Key erzeugen, nicht den aus dem Chat wiederverwenden)

## Schritt für Schritt

### A) Blueprint (empfohlen)

1. https://dashboard.render.com → **New +** → **Blueprint**
2. GitHub verbinden, Repo wählen: **hansheute88/isaac**
3. Branch: **`main`** (oder `main` nach Merge)
4. Blueprint-Datei: **`render.yaml`** (Repo-Root)
5. **Apply** / Create
6. Service **isaac-free** öffnen → **Environment**
7. `GROQ_API_KEY` = dein Key (als Secret)
8. **Manual Deploy** → Deploy latest commit
9. Oben die URL öffnen, z. B. `https://isaac-free.onrender.com`
10. Prüfen: `https://…/healthz` sollte `{"ok": true, …}` liefern

### B) Manuell (ohne Blueprint)

1. **New +** → **Web Service**
2. Repo **hansheute88/isaac**, Branch wie oben
3. Runtime: **Docker**
4. Dockerfile path: **`Dockerfile.free`**
5. Instance type: **Free**
6. Health Check Path: **`/healthz`**
7. Env Vars (wie in `render.yaml`):
   - `ISAAC_FREE_CLOUD=1`
   - `ISAAC_UNIFIED_PORT=1`
   - `ISAAC_BIND_HOST=0.0.0.0`
   - `ISAAC_DISABLE_VECTOR_MEMORY=1`
   - `ACTIVE_PROVIDER=groq`
   - `GROQ_API_KEY` = Secret
8. Create Web Service → warten bis Live

## Integration: Google Labs Jules & Render

Render bietet eine direkte Integration mit Google Labs Jules zur automatischen Behebung von Build-Fehlern in Pull Requests.

### Prerequisites (Voraussetzungen)
Bevor du einen API-Schlüssel generierst, stelle sicher, dass Render und GitHub bereit sind:
1. **Pull Request Previews aktivieren:** Gehe zu deinem Render-Dashboard und aktiviere *Pull Request Previews*.
2. **GitHub-Berechtigungen prüfen:** Prüfe deine GitHub Installations.
   - Suche nach **Google Labs Jules**.
   - Falls ein Link *Review Request* daneben angezeigt wird, klicke ihn an, um die aktualisierten Berechtigungen zu akzeptieren.
   - Falls kein Link zu sehen ist, sind die Berechtigungen bereits auf dem neuesten Stand.

### Setup (Einrichtung)
1. **API-Schlüssel bereitstellen:** Navigiere zum Render Dashboard. Klicke oben rechts auf das Menu **Help** und wähle **Coding Agents**, um den Provisionierungs-Bildschirm zu öffnen. Alternativ kannst du direkt [dashboard.render.com/jules](https://dashboard.render.com/jules) aufrufen.
2. **Schlüssel erstellen:** Klicke auf **Create API key**. Kopiere den angezeigten Schlüssel (er wird nur einmal angezeigt).
3. **In Jules verbinden:** Öffne in Jules **Settings > Integrations**, füge den Schlüssel in das Render-Feld ein und sende das Formular ab.

### Automated Fixes (Automatische Korrekturen)
Sobald die Verbindung hergestellt ist, überwacht Jules automatisch von ihm erstellte Pull Requests auf Build-Fehler:
* **Jules erstellt einen PR:** Wenn du einen Plan akzeptierst, öffnet Jules einen Pull Request mit den Änderungen.
* **Erkennung:** Wenn der Render-Build für diesen PR fehlschlägt, analysiert Jules automatisch die Build-Logs.
* **Behebung:** Jules schreibt einen Fix und pusht einen neuen Commit auf denselben Branch.
* **Merge:** Du überprüfst die Korrektur wie jede andere Änderung und mergest sie, wenn sie bereit ist.

*Hinweis:* Jules überwacht und behebt derzeit nur Build-Fehler auf Pull Requests, die von Jules selbst erstellt wurden.

## Nach dem Deploy

| URL | Erwartung |
|-----|-----------|
| `/` | Isaac Dashboard |
| `/healthz` | JSON `ok: true` (+ optional `git_commit` aus `RENDER_GIT_COMMIT`) |
| WebSocket | automatisch über `/ws` (same origin) |

### Deploy-Sync (lokal ↔ Repos ↔ Render)

Pflicht für Agenten (siehe `AGENTS.md` § Deploy-Sync):

```bash
python3 scripts/check_deploy_sync.py
python3 scripts/check_deploy_sync.py --strict   # exit 1 wenn out of sync
python3 scripts/check_deploy_sync.py --json
```

Braucht `RENDER_API_KEY` (Secret). Optional: `RENDER_SERVICE_ID`, `ISAAC_REMOTE_FREE_URL`.

**Hinweis Free-Plan:** Nach ~15 min Idle schläft der Service ein. Erster Request danach = Kaltstart (30–90 s).

## Fallback wenn Groq 403/Quota

In Environment umstellen:

```
ACTIVE_PROVIDER=gemini
GOOGLE_API_KEY=<dein AI-Studio-Key>
GEMINI_MODEL=gemini-flash-lite-latest
```

## Logs

Render → Service → **Logs**. Bei Start solltest du sehen:

```
Dashboard: http://0.0.0.0:<PORT>
Dashboard WS: ws://0.0.0.0:<PORT>/ws
Free-cloud mode: ...
```

## Sicherheit

- Keys nur als Render **Secret**, nie im Repo
- Öffentliche URL = jeder mit Link kann das Dashboard sehen
- Admin-APIs bleiben localhost-only (bewusst)

## Probleme

| Symptom | Fix |
|---------|-----|
| Health Check fail | Logs prüfen; `/healthz` manuell öffnen |
| Build fail | Branch mit `Dockerfile.free` gewählt? |
| Chat leer / kein WS | `ISAAC_UNIFIED_PORT=1` gesetzt? Hard-Reload |
| Groq 403 | Key neu; oder Gemini-Fallback |
| Immer schlafend | Free-Plan normal; oder lokal laufen lassen |
