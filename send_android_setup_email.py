#!/usr/bin/env python3
"""Isaac — Setup-E-Mail für Android (Termux) versenden.

Sendet eine E-Mail mit Einmal-Setup-Link und Termux-Befehl.
Kein versteckter Zugriff — der Empfänger muss Termux öffnen und den Befehl bestätigen.

Beispiel:
  python3 send_android_setup_email.py \\
    --to mein-android@gmail.com \\
    --from steffen@gmail.com \\
    --smtp-host smtp.gmail.com \\
    --smtp-user steffen@gmail.com \\
    --smtp-pass "$GMAIL_APP_PASSWORD"
"""

from __future__ import annotations

import argparse
import os
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

DEFAULT_SCRIPT_URL = (
    "https://raw.githubusercontent.com/hansheute88/isaac/"
    "feature/phase-3-refine/android_remote_setup.sh"
)
TERMUX_ONE_LINER = f"curl -sL {DEFAULT_SCRIPT_URL} | bash -s admin"


def build_html(one_liner: str, script_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"><title>Isaac Android Setup</title></head>
<body style="font-family:system-ui,sans-serif;max-width:640px;margin:2rem auto;line-height:1.5">
  <h1>Isaac auf Android einrichten</h1>
  <p>Dieser Link richtet <strong>Isaac im Admin-Modus</strong> auf deinem Android-Gerät ein
     (Termux). Es gibt keinen versteckten Fernzugriff — du führst den Befehl selbst aus.</p>

  <h2>Schritt 1: Termux installieren</h2>
  <p>F-Droid oder Play Store → <em>Termux</em> installieren (nicht Termux aus fremden Quellen).</p>

  <h2>Schritt 2: Befehl in Termux einfügen</h2>
  <pre style="background:#111;color:#eee;padding:1rem;border-radius:8px;overflow:auto">{one_liner}</pre>

  <h2>Schritt 3: Isaac starten</h2>
  <pre style="background:#111;color:#eee;padding:1rem;border-radius:8px">isaac-start</pre>

  <h2>Optional: Vom iPhone zugreifen</h2>
  <ol>
    <li>Tailscale auf Android und iPhone installieren</li>
    <li>In Termux: <code>sshd</code></li>
    <li>iPhone: SSH-App (Termius/Blink) → Android-Tailscale-IP, Port <strong>8022</strong></li>
  </ol>

  <p style="color:#666;font-size:0.9rem">
    Script-URL: <a href="{script_url}">{script_url}</a><br>
    Mehr Infos: ANDROID_ADMIN_MODE.md im Isaac-Repository
  </p>
</body>
</html>"""


def build_text(one_liner: str) -> str:
    return f"""Isaac Android Setup
===================

1) Termux installieren (F-Droid / Play Store)

2) In Termux diesen Befehl ausführen:

{one_liner}

3) Isaac starten:

isaac-start

Optional vom iPhone (SSH):
- Tailscale auf beiden Geräten
- Termux: sshd
- SSH-App → Android-IP, Port 8022

Hinweis: Ein E-Mail-Klick allein reicht nicht — Termux muss den Befehl einmal ausführen.
"""


def send_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    mail_from: str,
    mail_to: str,
    subject: str,
    one_liner: str,
    script_url: str,
    use_tls: bool,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to

    text = build_text(one_liner)
    html = build_html(one_liner, script_url)
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        if use_tls:
            server.starttls(context=context)
        if smtp_user:
            server.login(smtp_user, smtp_pass)
        server.sendmail(mail_from, [mail_to], msg.as_string())


def main() -> int:
    parser = argparse.ArgumentParser(description="Isaac Android Setup per E-Mail versenden")
    parser.add_argument("--to", required=True, help="Empfänger (dein Android-Gerät / Gmail)")
    parser.add_argument("--from", dest="mail_from", required=True, help="Absender-Adresse")
    parser.add_argument("--subject", default="Isaac Android Setup — Admin-Modus")
    parser.add_argument("--smtp-host", default=os.getenv("SMTP_HOST", "smtp.gmail.com"))
    parser.add_argument("--smtp-port", type=int, default=int(os.getenv("SMTP_PORT", "587")))
    parser.add_argument("--smtp-user", default=os.getenv("SMTP_USER", ""))
    parser.add_argument("--smtp-pass", default=os.getenv("SMTP_PASS", ""))
    parser.add_argument("--script-url", default=DEFAULT_SCRIPT_URL)
    parser.add_argument("--no-tls", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nicht senden")
    args = parser.parse_args()

    one_liner = f"curl -sL {args.script_url} | bash -s admin"

    if args.dry_run:
        print(build_text(one_liner))
        return 0

    if not args.smtp_user or not args.smtp_pass:
        print("Fehler: SMTP_USER und SMTP_PASS (oder --smtp-user / --smtp-pass) setzen.", file=sys.stderr)
        print("Gmail: App-Passwort unter https://myaccount.google.com/apppasswords", file=sys.stderr)
        return 1

    send_email(
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        smtp_user=args.smtp_user,
        smtp_pass=args.smtp_pass,
        mail_from=args.mail_from,
        mail_to=args.to,
        subject=args.subject,
        one_liner=one_liner,
        script_url=args.script_url,
        use_tls=not args.no_tls,
    )
    print(f"✅ E-Mail gesendet an {args.to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())