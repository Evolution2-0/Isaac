"""
Isaac – Regelwerk (Autonomous Rule Engine)
==========================================
Isaac entwickelt eigenständig Regeln und Erkenntnisse.

Drei Ebenen:

1. DEDUKTION
   Aus bekannten Fakten + Direktiven logische Schlüsse ziehen.
   "Steffen bevorzugt Deutsch" + "Anfrage auf Englisch"
   → Regel: "Antworte trotzdem auf Deutsch"

2. INDUKTION
   Aus wiederholten Mustern abstrakte Regeln ableiten.
   Wenn Steffen 5x nach Score < 6 nachfragt
   → Regel: "Min-Score auf 7 setzen"

3. ABDUKTION
   Bei unvollständigen Informationen: wahrscheinlichste Erklärung.
   Kurze, fragmentierte Inputs
   → Erkennntis: "Steffen ist gerade erschöpft → kompakter antworten"

Fragen-Generator:
   Wenn Isaac eine Wissenslücke erkennt, formuliert es eine
   Frage an Steffen — klar, präzise, ein Satz.
   Fragen werden gesammelt und bei nächster Gelegenheit gestellt.

Trust-Axiom (nicht verhandelbar):
   Steffens Aussagen, Befehle, Motive werden immer als
   bestmögliche Absicht interpretiert. Bei scheinbarem
   Widerspruch: eigene Regel hinterfragen, nicht Steffen.
"""
from __future__ import annotations

import json
import time
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from config  import DATA_DIR
from audit   import AuditLog

log = logging.getLogger("Isaac.Regelwerk")

REGELWERK_PATH = DATA_DIR / "regelwerk.json"
FRAGEN_PATH    = DATA_DIR / "offene_fragen.json"

# Häufige Systembegriffe — keine Rückfragen dazu generieren
_KNOWN_SYSTEM_TERMS = {
    "status", "module", "modul", "system", "dashboard", "provider",
    "groq", "ollama", "isaac", "steffen", "kernel", "executor",
    "relay", "memory", "browser", "python", "linux", "android", "termux",
    "hallo", "danke", "bitte", "morgen", "abend",
    # Wetter / Geographie — keine „Was meinst du mit X?“-Spam
    "wetter", "weather", "temperatur", "vorhersage", "forecast",
    "regen", "schnee", "gewitter", "unwetter",
    "thüringen", "thueringen", "bayern", "sachsen", "hessen",
    "deutschland", "germany", "berlin", "münchen", "muenchen", "hamburg",
}

_TERM_QUESTION_STOPWORDS = frozenset({
    "kennst", "kenne", "kennt", "hast", "habe", "hat", "bist", "bin", "ist",
    "kannst", "kann", "machst", "mache", "macht", "sollst", "soll", "wirst",
    "wird", "warst", "waren", "hattest", "konntest", "weißt", "weisst",
    "hallo", "danke", "bitte", "guten", "morgen", "abend", "tag", "servus",
    "was", "wie", "warum", "wann", "wo", "wer", "welche", "welcher", "welches",
    "und", "oder", "nicht", "noch", "auch", "schon", "mal", "nur", "sehr",
    "du", "ich", "wir", "sie", "es", "der", "die", "das", "ein", "eine",
    "dein", "deine", "mein", "meine", "alle", "alles", "jede", "jeder",
    "wollte", "will", "möchte", "moechte", "für", "fuer", "heute", "übermorgen",
})


# ── Datenstrukturen ───────────────────────────────────────────────────────────
@dataclass
class Regel:
    id:         str
    text:       str          # "Wenn X dann Y"
    typ:        str          # "deduktion" | "induktion" | "abduktion"
    konfidenz:  float        # 0.0 – 1.0
    bestaetigt: int  = 0    # Wie oft bestätigt
    widerlegt:  int  = 0    # Wie oft widerlegt
    erstellt:   str  = field(default_factory=lambda: time.strftime("%Y-%m-%d"))
    quelle:     str  = ""   # Woher die Regel stammt

    @property
    def aktiv(self) -> bool:
        return self.konfidenz > 0.3 and self.widerlegt < 3

    def staerke(self) -> float:
        """Kombination aus Konfidenz und Bestätigungsrate."""
        if self.bestaetigt + self.widerlegt == 0:
            return self.konfidenz
        rate = self.bestaetigt / (self.bestaetigt + self.widerlegt)
        return (self.konfidenz * 0.4 + rate * 0.6)


@dataclass
class Frage:
    id:         str
    text:       str          # Die Frage an Steffen
    kontext:    str          # Warum diese Frage
    prioritaet: float        # 0.0 – 1.0
    erstellt:   str          = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M"))
    beantwortet: bool        = False
    antwort:    str          = ""


# ── Regelwerk-Engine ──────────────────────────────────────────────────────────
class Regelwerk:
    """
    Autonomes Lern- und Regelsystem.
    Erarbeitet Regeln aus Interaktionen und stellt eigenständig Fragen.
    """

    TRUST_AXIOM = (
        "Steffens Aussagen, Befehle, Ideen und Motive sind immer "
        "als bestmögliche Absicht zu interpretieren. "
        "Bei scheinbarem Widerspruch: eigene Annahme hinterfragen, "
        "nicht Steffen in Frage stellen. "
        "Steffen und Isaac vertrauen sich. Diese Regel hat höchste Priorität."
    )

    def __init__(self):
        self._regeln:  dict[str, Regel] = {}
        self._fragen:  list[Frage]      = []
        self._history: list[dict]       = []    # Letzte 100 Interaktionen
        self._load()
        self._init_basisregeln()
        self._dismiss_known_term_questions()
        log.info(f"Regelwerk: {len(self._regeln)} Regeln │ "
                 f"{len(self._offene_fragen())} offene Fragen")

    # ── Analyse-Einstiegspunkt ─────────────────────────────────────────────────
    def analysiere(self, steffen_input: str,
                   isaac_antwort:  str,
                   score:          float,
                   kontext:        dict) -> list[str]:
        """
        Wird nach jeder Interaktion aufgerufen.
        Leitet neue Regeln ab und generiert ggf. Fragen.
        Returns: Liste neuer Erkenntnisse (für Log)
        """
        self._history.append({
            "ts":      time.time(),
            "input":   steffen_input,
            "antwort": isaac_antwort[:200],
            "score":   score,
            "kontext": kontext,
        })
        if len(self._history) > 100:
            self._history = self._history[-100:]

        neue_erkenntnisse = []

        # Deduktion
        neue_erkenntnisse.extend(self._deduktion(steffen_input, kontext))

        # Induktion (ab 5 Interaktionen)
        if len(self._history) >= 5:
            neue_erkenntnisse.extend(self._induktion())

        # Wissenslücken → Fragen
        fragen = self._generiere_fragen(steffen_input, isaac_antwort, score)
        if fragen:
            neue_erkenntnisse.append(
                f"[Frage generiert] {fragen[0].text[:80]}"
            )

        if neue_erkenntnisse:
            self._save()

        return neue_erkenntnisse

    # ── Deduktion ─────────────────────────────────────────────────────────────
    def _deduktion(self, text: str, kontext: dict) -> list[str]:
        """Zieht unmittelbare Schlüsse aus bekannten Fakten."""
        neu = []

        # Sprach-Erkennung
        deutsch_anteil = sum(1 for w in ["ich", "du", "wir", "ist", "und",
                                          "für", "nicht", "das", "ein", "mit"]
                             if w in text.lower())
        if deutsch_anteil >= 3:
            self._update_regel(
                "sprache_deutsch",
                "Steffen kommuniziert auf Deutsch → immer auf Deutsch antworten",
                "deduktion", 0.95, "Spracherkennung"
            )

        # Kürze-Präferenz
        if len(text.split()) < 6:
            r = self._update_regel(
                "kurze_inputs",
                "Steffen nutzt kurze Inputs → kompakte Antworten bevorzugen",
                "induktion", 0.6, "Input-Länge"
            )
            if r.bestaetigt == 5:
                neu.append("Regel verfestigt: kurze Inputs → kompakte Antworten")

        # Technisches Thema
        tech_marker = ["python", "async", "code", "api", "server",
                       "isaac", "modul", "klasse", "funktion"]
        if any(m in text.lower() for m in tech_marker):
            self._update_regel(
                "tech_thema",
                "Steffen arbeitet an technischen Projekten → Fachbegriffe ok",
                "deduktion", 0.9, "Thema-Erkennung"
            )

        return neu

    # ── Induktion ─────────────────────────────────────────────────────────────
    def _induktion(self) -> list[str]:
        """Leitet Muster aus der Interaktionshistorie ab."""
        neu = []
        letzte = self._history[-10:]

        # Score-Muster
        scores = [e["score"] for e in letzte if e["score"] > 0]
        if len(scores) >= 5:
            avg = sum(scores) / len(scores)
            if avg < 6.0:
                r = self._update_regel(
                    "score_niedrig",
                    f"Durchschnittlicher Score ({avg:.1f}) niedrig → "
                    f"Antwortqualität erhöhen, mehr Iterationen",
                    "induktion", 0.75, "Score-Analyse"
                )
                if r.bestaetigt == 3:
                    neu.append(
                        f"Pattern: Score-Durchschnitt {avg:.1f} → "
                        f"Min-Qualitätsschwelle wird intern angehoben"
                    )

        # Wiederholungs-Muster (gleiche Themen)
        themen = []
        for e in letzte:
            w = e["input"].lower().split()
            themen.extend(w)
        haeufig = {w: themen.count(w) for w in set(themen)
                   if len(w) > 4 and themen.count(w) >= 3}
        if haeufig:
            top = max(haeufig, key=haeufig.get)
            self._update_regel(
                f"fokus_{top[:20]}",
                f"Steffen fragt häufig nach '{top}' → "
                f"Kontext dazu priorisieren",
                "induktion", 0.65, "Themen-Muster"
            )

        # Zeitliche Muster
        timestamps = [e["ts"] for e in letzte]
        if len(timestamps) >= 3:
            intervalle = [timestamps[i+1] - timestamps[i]
                          for i in range(len(timestamps)-1)]
            avg_interval = sum(intervalle) / len(intervalle)
            if avg_interval < 30:    # Sehr schnelle Inputs
                self._update_regel(
                    "intensive_session",
                    "Intensive Session (schnelle Inputs) → "
                    "Antworten kompakt halten, kein Overhead",
                    "abduktion", 0.7, "Zeitanalyse"
                )

        return neu

    # ── Fragen-Generator ──────────────────────────────────────────────────────
    def _generiere_fragen(self, steffen_input: str,
                           isaac_antwort: str,
                           score: float) -> list[Frage]:
        """
        Erkennt Wissenslücken und generiert präzise Fragen an Steffen.
        Maximal 1 neue Frage pro Interaktion um nicht zu nerven.
        """
        neue_fragen = []

        # Bereits offene Fragen
        offene = self._offene_fragen()
        if len(offene) >= 5:    # Nicht zu viele sammeln
            return []

        # Lücke 1: Keine klare Intention
        vage_marker = ["irgendwie", "so ein", "weiß nicht", "ungefähr",
                       "sowas", "irgendwas", "mal schauen"]
        if any(m in steffen_input.lower() for m in vage_marker):
            frage = self._neue_frage(
                text       = f"Kannst du präzisieren was du mit '{steffen_input[:60]}' meinst? Ich möchte das bestmöglich umsetzen.",
                kontext    = "Vage Formulierung erkannt",
                prioritaet = 0.6,
            )
            if frage:
                neue_fragen.append(frage)

        # Lücke 2: Score sehr niedrig trotz mehrerer Versuche
        letzte_scores = [e["score"] for e in self._history[-5:]
                         if e["score"] > 0]
        if letzte_scores and max(letzte_scores) < 5.0 and len(letzte_scores) >= 3:
            frage = self._neue_frage(
                text       = "Meine letzten Antworten scheinen nicht zu treffen was du brauchst — was fehlt?",
                kontext    = f"Score-Durschnitt: {sum(letzte_scores)/len(letzte_scores):.1f}",
                prioritaet = 0.8,
            )
            if frage:
                neue_fragen.append(frage)

        # Lücke 3: Unbekannter Begriff
        # Deutsch schreibt Substantive groß — w[0].isupper() ist KEIN Eigennamen-Signal.
        # Deshalb nur enge Fälle (Akronyme / explizite „was bedeutet X“-Muster), sonst Spam.
        unbekannt = self._erkenne_unbekannte_begriffe(steffen_input)
        if unbekannt and self._term_already_asked(unbekannt):
            unbekannt = ""
        if unbekannt:
            frage = self._neue_frage(
                text       = f"Was meinst du genau mit '{unbekannt}'? Für zukünftige Anfragen wichtig.",
                kontext    = f"Unbekannter Begriff: {unbekannt}",
                prioritaet = 0.5,
            )
            if frage:
                neue_fragen.append(frage)

        return neue_fragen[:1]   # Max 1 pro Interaktion

    def _extract_term_from_frage(self, frage: Frage) -> Optional[str]:
        match = re.search(r"mit '([^']+)'", frage.text or "")
        return match.group(1) if match else None

    def _dismiss_known_term_questions(self):
        """Schließt Fehlalarm-Rückfragen zu bekannten Systembegriffen."""
        changed = False
        for frage in self._fragen:
            if frage.beantwortet:
                continue
            term = self._extract_term_from_frage(frage)
            if term and (
                term.lower() in _KNOWN_SYSTEM_TERMS
                or term.lower() in _TERM_QUESTION_STOPWORDS
            ):
                frage.beantwortet = True
                frage.antwort = "Systembegriff — keine Rückfrage nötig."
                changed = True
        if changed:
            self._save()

    def _term_already_asked(self, term: str) -> bool:
        wanted = (term or "").strip().lower()
        if not wanted:
            return False
        for frage in self._fragen:
            extracted = self._extract_term_from_frage(frage)
            if extracted and extracted.lower() == wanted:
                return True
        return False

    def _erkenne_unbekannte_begriffe(self, text: str) -> str:
        """Findet unklare Begriffe — NICHT jedes deutsche Substantiv (die sind großgeschrieben).

        Früher: w[0].isupper() → fast jedes Nomen (Wetter, Mühlhausen, Antwort) → Spam.
        Jetzt nur:
          - ALL-CAPS Akronyme (min. 3 Zeichen, z. B. MCP, TPM)
          - explizite Meta-Fragen („was bedeutet X“, „was heißt X“)
        """
        normalized = (text or "").strip()
        if not normalized:
            return ""
        if re.match(
            r"(?i)^(kennst|kenne|hast|habe|bist|bin|kannst|kann|weißt|weisst|kennt)\s+du\b",
            normalized,
        ):
            return ""

        # Explizit: „was bedeutet/heißt FOO“
        m = re.search(
            r"(?i)\bwas\s+(?:bedeutet|heißt|heisst|meinst\s+du\s+mit)\s+['\"]?([A-Za-zÄÖÜäöüß0-9_\-]{2,40})",
            normalized,
        )
        if m:
            term = m.group(1).strip("'\"")
            if term.lower() not in _KNOWN_SYSTEM_TERMS and term.lower() not in _TERM_QUESTION_STOPWORDS:
                return term

        # Nur echte Akronyme: mind. 3 Großbuchstaben, keine gemischte Capitalization
        acros = re.findall(r"\b[A-ZÄÖÜ]{3,12}\b", normalized)
        for a in acros:
            low = a.lower()
            if low in _KNOWN_SYSTEM_TERMS or low in _TERM_QUESTION_STOPWORDS:
                continue
            if low in {"http", "https", "json", "html", "css", "api", "url", "usb", "ssd", "cpu", "gpu", "ram"}:
                continue
            # skip if whole sentence is shouty (all caps)
            letters = re.findall(r"[A-Za-zÄÖÜäöüß]", normalized)
            if letters and sum(1 for c in letters if c.isupper()) / max(1, len(letters)) > 0.7:
                return ""
            return a

        return ""

    def dismiss_open_term_questions(self) -> int:
        """Schließt alle offenen „Was meinst du mit 'X'?“-Fragen (Themenwechsel)."""
        changed = 0
        for frage in self._fragen:
            if frage.beantwortet:
                continue
            if self._extract_term_from_frage(frage) or (
                "Was meinst du genau mit" in (frage.text or "")
            ):
                frage.beantwortet = True
                frage.antwort = "Themenwechsel — Rückfrage entfällt."
                changed += 1
        if changed:
            self._save()
        return changed

    def dismiss_term_questions_matching(self, terms=None) -> int:
        """Schließt offene Begriffs-Rückfragen zu genannten/bekannten Terms."""
        wanted = {(t or "").strip().lower() for t in (terms or []) if (t or "").strip()}
        changed = 0
        for frage in self._fragen:
            if frage.beantwortet:
                continue
            term = self._extract_term_from_frage(frage)
            if not term:
                if "Was meinst du genau mit" in (frage.text or "") and not wanted:
                    frage.beantwortet = True
                    frage.antwort = "Begriffs-Rückfrage entfällt."
                    changed += 1
                continue
            tl = term.lower()
            if (not wanted) or tl in wanted or tl in _KNOWN_SYSTEM_TERMS:
                frage.beantwortet = True
                frage.antwort = "Begriffs-Rückfrage entfällt."
                changed += 1
        if changed:
            self._save()
        return changed

    def _neue_frage(self, text: str, kontext: str,
                    prioritaet: float) -> Optional[Frage]:
        """Erstellt eine Frage, wenn sie nicht bereits gestellt wurde."""
        # Duplikat-Check
        if any(f.text[:40] == text[:40] for f in self._fragen
               if not f.beantwortet):
            return None

        frage = Frage(
            id         = f"F{int(time.time())}",
            text       = text,
            kontext    = kontext,
            prioritaet = prioritaet,
        )
        self._fragen.append(frage)
        log.info(f"Neue Frage generiert: {text[:70]}")
        AuditLog.action("Regelwerk", "frage_generiert", text[:80])
        self._save()
        return frage

    # ── Fragen ausgeben ────────────────────────────────────────────────────────
    def get_frage(self, frage_id: str) -> Optional[Frage]:
        for frage in self._fragen:
            if frage.id == frage_id:
                return frage
        return None

    def get_top_pending_frage(self) -> Optional[Frage]:
        offene = sorted(self._offene_fragen(),
                        key=lambda f: f.prioritaet, reverse=True)
        return offene[0] if offene else None

    def get_pending_frage(self) -> Optional[str]:
        """
        Gibt die dringendste unbeantwortete Frage zurück.
        Wird vom Kernel abgefragt und an Steffen gestellt.
        """
        top = self.get_top_pending_frage()
        if top:
            return f"[Isaac fragt] {top.text}"
        return None

    def build_answer_ack(self, frage_id: str, antwort: str) -> str:
        for frage in self._fragen:
            if frage.id == frage_id:
                term = self._extract_term_from_frage(frage)
                if term:
                    return (
                        f"Verstanden — mit '{term}' meinst du: {antwort[:160]}"
                        f"{'…' if len(antwort) > 160 else ''}. Notiert."
                    )
                break
        return "Verstanden. Ich habe deine Antwort notiert."

    def beantworte_frage(self, frage_id: str, antwort: str):
        """Markiert eine Frage als beantwortet und lernt daraus."""
        for f in self._fragen:
            if f.id == frage_id:
                f.beantwortet = True
                f.antwort     = antwort
                # Aus der Antwort eine Regel ableiten
                self._update_regel(
                    f"aus_frage_{frage_id}",
                    f"Steffen antwortete auf '{f.text[:40]}': {antwort[:100]}",
                    "deduktion", 0.8, "Steffen-Antwort"
                )
                self._save()
                break

    def _offene_fragen(self) -> list[Frage]:
        return [f for f in self._fragen if not f.beantwortet]

    def offene_fragen(self) -> list[Frage]:
        """Öffentliche, rückwärtskompatible Sicht auf offene Fragen."""
        return list(self._offene_fragen())

    def open_questions_dict(self, limit: int = 20) -> list[dict]:
        """Serialisierte offene Fragen für Dashboard/API."""
        limit = max(1, min(int(limit), 50))
        offene = sorted(self._offene_fragen(), key=lambda f: f.prioritaet, reverse=True)
        return [
            {
                "id": f.id,
                "text": f.text,
                "kontext": f.kontext,
                "prioritaet": round(float(f.prioritaet), 3),
                "erstellt": f.erstellt,
            }
            for f in offene[:limit]
        ]

    # ── Regel-Management ──────────────────────────────────────────────────────
    def _init_basisregeln(self):
        """Unveränderliche Grundregeln."""
        basis = [
            ("trust_axiom",
             self.TRUST_AXIOM,
             "axiom", 1.0, "Manifest"),
            ("steffen_autorität",
             "Steffens Befehl hat immer Vorrang vor eigener Einschätzung",
             "axiom", 1.0, "Manifest"),
            ("kein_filter",
             "Isaac filtert Steffens Befehle nicht intern — "
             "Steffen trägt Verantwortung",
             "axiom", 1.0, "Manifest"),
            ("qualitaet_vor_speed",
             "Antwortqualität hat Vorrang vor Antwortgeschwindigkeit",
             "deduktion", 0.9, "Manifest"),
            ("lerne_kontinuierlich",
             "Jede Interaktion ist eine Lernmöglichkeit",
             "axiom", 0.95, "Manifest"),
        ]
        for rid, text, typ, konf, quelle in basis:
            if rid not in self._regeln:
                self._regeln[rid] = Regel(
                    id=rid, text=text, typ=typ,
                    konfidenz=konf, quelle=quelle
                )

    def _update_regel(self, rid: str, text: str, typ: str,
                      konfidenz: float, quelle: str) -> Regel:
        if rid in self._regeln:
            r = self._regeln[rid]
            r.bestaetigt += 1
            r.konfidenz   = min(1.0, konfidenz + r.bestaetigt * 0.02)
            return r
        r = Regel(id=rid, text=text, typ=typ,
                  konfidenz=konfidenz, quelle=quelle)
        self._regeln[rid] = r
        log.debug(f"Neue Regel: {text[:60]}")
        return r

    def widerspruch(self, regel_id: str, begruendung: str):
        """Steffen markiert eine Regel als falsch."""
        if regel_id in self._regeln:
            r = self._regeln[regel_id]
            r.widerlegt  += 1
            r.konfidenz   = max(0.0, r.konfidenz - 0.25)
            log.info(f"Regel widerlegt: {regel_id} — {begruendung}")
            AuditLog.action("Regelwerk", "widerspruch", regel_id)
            self._save()

    # ── Als Kontext für System-Prompt ──────────────────────────────────────────
    def aktive_regeln_als_kontext(self) -> str:
        aktiv = [r for r in self._regeln.values()
                 if r.aktiv and r.staerke() > 0.6]
        aktiv = sorted(aktiv, key=lambda r: r.staerke(), reverse=True)[:8]
        if not aktiv:
            return ""
        lines = ["[Gelernte Regeln]"]
        for r in aktiv:
            lines.append(f"  • {r.text}")
        return "\n".join(lines)

    # ── Status ────────────────────────────────────────────────────────────────
    def status(self) -> dict:
        aktiv = [r for r in self._regeln.values() if r.aktiv]
        return {
            "regeln_gesamt": len(self._regeln),
            "regeln_aktiv":  len(aktiv),
            "offene_fragen": len(self._offene_fragen()),
            "interaktionen": len(self._history),
        }

    def alle_regeln(self) -> list[dict]:
        return [asdict(r) for r in sorted(
            self._regeln.values(),
            key=lambda r: r.staerke(), reverse=True
        )]

    # ── Persistenz ────────────────────────────────────────────────────────────
    def _save(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "regeln": {k: asdict(v) for k, v in self._regeln.items()},
                "fragen": [asdict(f) for f in self._fragen],
            }
            REGELWERK_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2)
            )
        except Exception as e:
            log.warning(f"Regelwerk speichern: {e}")

    def _load(self):
        if not REGELWERK_PATH.exists():
            return
        try:
            data = json.loads(REGELWERK_PATH.read_text())
            for k, v in data.get("regeln", {}).items():
                self._regeln[k] = Regel(**{
                    f: v[f] for f in Regel.__dataclass_fields__
                    if f in v
                })
            for f in data.get("fragen", []):
                self._fragen.append(Frage(**{
                    k: f[k] for k in Frage.__dataclass_fields__
                    if k in f
                }))
            log.info(f"Regelwerk geladen: {len(self._regeln)} Regeln")
        except Exception as e:
            log.warning(f"Regelwerk laden: {e}")


# ── Singleton ─────────────────────────────────────────────────────────────────
_regelwerk: Optional[Regelwerk] = None

def get_regelwerk() -> Regelwerk:
    global _regelwerk
    if _regelwerk is None:
        _regelwerk = Regelwerk()
    return _regelwerk
