# Isaac auf iPad (iPad Pro 11) – Dashboard-Zugriff

## Ziel
Das Isaac-Dashboard läuft auf deinem Rechner/Server; das iPad ruft es über das lokale WLAN ab.

## Setup auf dem Isaac-Rechner

1. **Bind-Host auf LAN öffnen** – in `.env` (oder Shell) setzen:

```bash
ISAAC_BIND_HOST=0.0.0.0
```

Ohne diesen Wert bindet der Monitor nur an `localhost` und ist aus dem Netzwerk nicht erreichbar. `config.py` (`MonitorConfig.host`) und `free_cloud.bind_host()` lesen beide denselben Wert, damit HTTP-Dashboard und WebSocket auf derselben Adresse lauschen.

2. **Ports** (Defaults, in `.env` überschreibbar):

| Port | Dienst | Env-Variable |
|------|--------|--------------|
| **8766** | Dashboard (HTTP, im Safari öffnen) | `DASHBOARD_PORT` / `MONITOR_HTTP_PORT` |
| **8765** | WebSocket (Telemetrie, automatisch) | `MONITOR_PORT` |

3. **IP des Rechners herausfinden:**

```bash
# macOS
ipconfig getifaddr en0
# Linux
hostname -I | awk '{print $1}'
```

4. **Isaac starten:**

```bash
python3 isaac_core.py   # oder: bash run_isaac.sh
```

## Auf dem iPad

Safari öffnen:

```
http://<RECHNER-IP>:8766
```

Beispiel: `http://192.168.1.42:8766`

- Das Dashboard erkennt automatisch den richtigen WebSocket-Host (aus der aufgerufenen URL, via `MONITOR_WS_HOST_PUBLIC` überschreibbar).
- Dashboard am Home-Screen ablegen: Safari → Teilen → „Zum Home-Bildschirm" → läuft dann fast wie App im Vollbild.

## Firewall

Falls das iPad nichts erreicht, eingehende Verbindungen für Port 8766 (+8765) erlauben:

```bash
# Linux (ufw)
sudo ufw allow from 192.168.1.0/24 to any port 8766
sudo ufw allow from 192.168.1.0/24 to any port 8765
# macOS: Systemeinstellungen → Netzwerk → Firewall → Optionen → python3 erlauben
```

## Sicherheitsgrenzen (bewusst erhalten)

Folgende Dashboard-Endpunkte akzeptieren nur Verbindungen von `localhost` und sind vom iPad aus **absichtlich gesperrt** (Phase 3.4.3, `monitor_server.is_localhost_request`):

- Task-Checkpoints laden/speichern
- Task-Resume
- Owner-Override

Das ist ein Schutzmechanismus, kein Bug. Wenn du diese Funktionen später auch vom iPad willst, muss dafür ein authentifizierter Token-Weg gebaut werden – nicht einfach die Prüfung entfernen.

## Troubleshooting

| Problem | Lösung |
|---------|---------|
| iPad lädt ewig / Timeout | `ISAAC_BIND_HOST=0.0.0.0` gesetzt? Rechner-IP korrekt? Firewall? |
| Dashboard lädt, aber keine Live-Daten | WebSocket-Port 8765 blockiert; `MONITOR_WS_HOST_PUBLIC=<RECHNER-IP>` setzen |
| „owner_override nur von localhost" | Erwartet – siehe Sicherheitsgrenzen oben |
