# Isaac — Android Admin-Modus

## Überblick

Der **Admin-Modus** ermöglicht es Isaac, auf deinem Android-Gerät (Termux) genau wie du selbst zu agieren — mit allen Funktionen, Berechtigungen und ohne Gating-Fragen.

Im Admin-Modus:
- ✅ Alle Dateisystem-Operationen (Lesen, Schreiben, Löschen)
- ✅ Alle Shell-Befehle (System-Execution)
- ✅ Alle Tools und Browser-Funktionen
- ✅ Keine Sicherheitsfragen oder Gating
- ✅ Audit-Logging bleibt aktiv (für Transparenz)

**Hinweis:** Der Admin-Modus ist nur für lokale, vertrauenswürdige Geräte gedacht. Verwende ihn nicht auf Shared-Devices.

---

## Aktivierung

### 1. Termux-Setup (Android)

```bash
# Termux installieren (F-Droid oder Play Store)
# dann in Termux:

pkg install python3 git
cd $HOME
git clone https://github.com/hansheute88/isaac.git
cd Isaac
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Admin-Modus aktivieren

Erstelle eine `.env` Datei im Isaac-Verzeichnis:

```bash
cd /root/isaacnew  # oder dein Isaac-Pfad
cat > .env << 'EOF'
ISAAC_OWNER=Steffen
ACTIVE_PROVIDER=ollama
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:1.5b
ISAAC_DISABLE_VECTOR_MEMORY=1

# Admin-Modus aktivieren (direkt auf deinem Android-Gerät)
ISAAC_PRIVILEGE_MODE=admin
EOF
```

Oder füge einfach diese Zeile zu deiner bestehenden `.env` hinzu:

```env
ISAAC_PRIVILEGE_MODE=admin
```

### 3. Isaac starten

```bash
cd /root/isaacnew
.venv/bin/python isaac_core.py
```

### 4. Verifizieren

Beim Start solltest du diese Meldungen sehen:

```
Isaac.Privilege: PrivilegeGate aktiv │ Owner: Steffen
Isaac.Privilege: ADMIN_MODE (vorautorisiert)
```

Das bedeutet: **Admin-Modus ist aktiv**. Isaac wird alle Befehle ausführen, als würde er von dir selbst kommen.

---

## Was ändert sich?

| Verhalten | Normale Umgebung | Admin-Modus |
|-----------|------------------|------------|
| Dateizugriff | Eingeschränkt auf workspace/ | Vollständig (überall) |
| Shell-Befehle | Gating-Fragen bei sensitiven Aktionen | Direkt erlaubt |
| SUDO-Modus | Passwort erforderlich | Automatisch aktiviert |
| Sicherheitschecks | Aktiv | Aktiv (Audit-Logging bleibt) |

---

## Sicherheitshinweis

⚠️ **Admin-Modus ist vertrauensbasiert.**

- Verwende ihn **nur auf deinen persönlichen Geräten**.
- Nicht auf Shared-Devices oder mit fremdem Netzwerk.
- Audit-Logs sind trotzdem aktiv — alle Aktionen werden geloggt.
- Isaac handelt in deinem Namen — verantwortungsvoll verwenden.

---

## Zurück zum Normalbetrieb

Um den Admin-Modus zu deaktivieren, änder einfach die `.env`:

```env
ISAAC_PRIVILEGE_MODE=user
```

Oder entferne die Zeile ganz (Standard ist `user`).

---

## Troubleshooting

**Problem:** Admin-Modus wird nicht erkannt  
**Lösung:** Stelle sicher, dass die `.env` Datei im richtigen Verzeichnis liegt (neben `isaac_core.py`)

```bash
cd /root/isaacnew
cat .env | grep ISAAC_PRIVILEGE_MODE
```

**Problem:** Isaac läuft, aber Dateizugriff ist immer noch limitiert  
**Lösung:** Restart erforderlich

```bash
# Isaac stoppen (Ctrl+C)
# Starten Sie neu:
.venv/bin/python isaac_core.py
```

**Problem:** Audit-Log ist leer  
**Lösung:** Das ist normal im Admin-Modus. Logs sind in `data/audit.jsonl`

```bash
tail -f data/audit.jsonl
```

---

## Was ist NOT inklusive?

Auch im Admin-Modus sind folgende Funktionen **nicht verfügbar** (Phases später):
- ❌ Personality/Instincts-Layer (noch nicht gebaut)
- ❌ Learning-Loops (noch nicht aktiviert)
- ❌ Inquiry/Clarification (noch nicht gebaut)
- ❌ Trust-Modeling (noch nicht gebaut)

Diese sind auf der Roadmap, aber noch nicht implementiert.

---

## Für Entwickler

Wenn du Isaac selbst weiterentwickelst und Admin-Modus brauchst:

```python
from config import get_config

# Im Admin-Modus?
if get_config().privilege_mode == "admin":
    # Alles freigeben
    allowed = True
else:
    # Standard-Gating
    allowed = check_privilege(action, context)
```

Der Admin-Modus wird in drei Stellen durchgesetzt:

1. **`privilege.py`** — PrivilegeGate.authorize() — alle Aktionen erlaubt
2. **`sudo_gate.py`** — SudoGate.open/check() — Passwort nicht erforderlich
3. **`config.py`** — ISAAC_PRIVILEGE_MODE environment variable

---

## Feedback & Issues

Falls du Probleme mit dem Admin-Modus hast, öffne ein Issue auf GitHub:

https://github.com/hansheute88/isaac/issues

Erwähne:
- Android-Version
- Termux-Version
- deine `.env` Konfiguration
- Fehler-Output aus `data/logs/`

---

**Isaac v5.3+ | Admin-Mode Feature**
