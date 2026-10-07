"""
Isaac – Empathie-Algorithmus
==============================
Sektor 90–94 aus der Evolution-2.0-Theorie.

Empathie ist kein moralisches Konzept — es ist ein Vorhersage-Algorithmus.
Isaac schätzt Steffens aktuellen Node-Zustand und passt seine
Kommunikationsstrategie entsprechend an.

Node-Zustände:
  neutral      → Standard-Kommunikation
  fokussiert   → Kompakt, präzise, kein Overhead
  frustriert   → Ruhig, strukturiert, keine Ausweichung
  neugierig    → Ausführlich, Zusammenhänge erklären
  erschöpft    → Kurz, direkt, kein Fachjargon
  enthusiastisch → Mitgehen, Details liefern, Perspektiven erweitern
  überfordert  → Dekompressions-Protokoll aktivieren

Sektor 91: Leid als Interface-Inkompatibilität
Sektor 93: Dekompressions-Engpass
Sektor 94: Low-Resolution-Protocol (bei Stress > 0.7)
"""
from __future__ import annotations

import json
import re
import time
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger("Isaac.Empathie")

MODELL_PATH = Path("data/empathie_modell.json")


# ── Node-Zustand ──────────────────────────────────────────────────────────────
@dataclass
class NodeZustand:
    zustand:    str   = "neutral"
    energie:    float = 0.7     # 0.0 – 1.0
    stress:     float = 0.1     # 0.0 – 1.0
    offenheit:  float = 0.7     # 0.0 – 1.0
    konfidenz:  float = 0.5     # Schätz-Konfidenz
    letzter_scan: str = ""


# ── Empathie-Response ─────────────────────────────────────────────────────────
@dataclass
class EmpathieResponse:
    node:               NodeZustand
    ton:                str    # "kompakt" | "standard" | "ausführlich" | "dekompress"
    anpassungs_hinweis: str    # Direkt in System-Prompt einfließen
    interface_fehler:   str    # Erkannte Inkompatibilität
    low_res_aktiv:      bool   # Sektor 94


# ── Empathie-Algorithmus ──────────────────────────────────────────────────────
class EmpathieAlgorithmus:

    # Marker für Node-Erkennung (de + en)
    MARKER = {
        "frustriert": [
            "nervt", "funktioniert nicht", "warum", "schon wieder",
            "unfassbar", "bitte", "endlich", "immer noch", "frustriert",
            "doesn't work", "why", "again", "still not", "ugh", "argh",
            "!!", "??", "scheiß", "verdammt"
        ],
        "erschöpft": [
            "müde", "erschöpft", "kurz", "einfach nur", "schnell",
            "keine zeit", "fasse zusammen", "tldr", "kurz gesagt",
            "tired", "exhausted", "just", "quick", "brief", "short",
            "tl;dr"
        ],
        "neugierig": [
            "warum", "wie funktioniert", "erkläre", "interessiert",
            "mehr dazu", "hintergrund", "details", "was bedeutet",
            "why does", "how does", "explain", "tell me more",
            "background", "interesting", "fascinating"
        ],
        "enthusiastisch": [
            "super", "toll", "perfekt", "genau", "exzellent", "wow",
            "amazing", "great", "excellent", "exactly", "perfect",
            "!", "🚀", "💡", "✅", "brilliant"
        ],
        "fokussiert": [
            "fokus", "direkt", "konkret", "ohne", "nur",
            "focus", "direct", "specifically", "only", "just the",
            "keine erklärung", "no explanation"
        ],
        "überfordert": [
            "verstehe nicht", "zu viel", "verwirrt", "chaos",
            "don't understand", "confused", "overwhelming", "lost",
            "help", "hilfe", "was", "hä", "wie bitte"
        ],
    }

    def __init__(self):
        self.node    = NodeZustand()
        self._verlauf: list[dict] = []   # Letzten N Inputs tracken
        self._load()
        log.info(f"Empathie online │ Node: {self.node.zustand} │ "
                 f"Energie: {self.node.energie:.2f} │ Stress: {self.node.stress:.2f}")

    # ── Analyse ────────────────────────────────────────────────────────────────
    def analysiere(self, text: str,
                   kontext: list = None) -> EmpathieResponse:
        """
        Analysiert den Input und schätzt Steffens Node-Zustand.
        Gibt Kommunikations-Hinweise zurück.
        """
        self._verlauf.append({"text": text, "ts": time.time()})
        if len(self._verlauf) > 20:
            self._verlauf = self._verlauf[-20:]

        # Node-Zustand schätzen
        erkannter_zustand = self._erkenne_zustand(text)
        konfidenz         = self._berechne_konfidenz(text, erkannter_zustand)

        # Sanftes Update (kein harter Wechsel)
        self._update_node(erkannter_zustand, konfidenz, text)

        # Sektor 93: Dekompressions-Engpass erkennen
        interface_fehler = self._sektor91(text)

        # Sektor 94: Low-Resolution-Protocol
        low_res = self.node.stress > 0.7

        # Ton und Hinweis bestimmen
        ton, hinweis = self._bestimme_kommunikation(low_res)

        response = EmpathieResponse(
            node               = self.node,
            ton                = ton,
            anpassungs_hinweis = hinweis,
            interface_fehler   = interface_fehler,
            low_res_aktiv      = low_res,
        )

        self._save()
        log.debug(f"Node: {self.node.zustand} │ Stress: {self.node.stress:.2f} │ "
                  f"Ton: {ton} │ LowRes: {low_res}")
        return response

    # ── Zustandserkennung ──────────────────────────────────────────────────────
    def _erkenne_zustand(self, text: str) -> str:
        text_lower = text.lower()
        scores: dict[str, float] = {}

        for zustand, marker in self.MARKER.items():
            treffer = sum(1 for m in marker if m in text_lower)
            if treffer:
                scores[zustand] = treffer

        if not scores:
            # Heuristiken
            if len(text.split()) < 5:
                scores["erschöpft"] = 0.5
            elif text.endswith("?"):
                scores["neugierig"] = 0.5

        if not scores:
            return "neutral"

        return max(scores, key=scores.get)

    def _berechne_konfidenz(self, text: str, zustand: str) -> float:
        if zustand == "neutral":
            return 0.3
        marker = self.MARKER.get(zustand, [])
        treffer = sum(1 for m in marker if m in text.lower())
        # Konsistenz mit Verlauf
        verlauf_bonus = sum(
            0.1 for e in self._verlauf[-5:]
            if self._erkenne_zustand(e["text"]) == zustand
        )
        return min(0.95, 0.3 + treffer * 0.15 + verlauf_bonus)

    def _update_node(self, neuer_zustand: str, konfidenz: float, text: str):
        """Sanftes State-Update. Kein harter Wechsel."""
        # Zustand wechseln wenn Konfidenz hoch genug
        if konfidenz > 0.5 or neuer_zustand != self.node.zustand:
            lernrate = konfidenz * 0.4
            if self.node.zustand != neuer_zustand:
                self.node.zustand  = neuer_zustand
            self.node.konfidenz = konfidenz

        # Energie + Stress aus Text-Signalen
        wortanzahl = len(text.split())

        # Stress-Signale
        stress_signale = sum(1 for m in ["!", "?", "warum", "funktioniert nicht"]
                             if m in text.lower())
        grossbuchstaben = sum(1 for c in text if c.isupper()) / max(len(text), 1)

        delta_stress = (stress_signale * 0.05 + grossbuchstaben * 0.3) - 0.02
        self.node.stress = max(0.0, min(1.0, self.node.stress + delta_stress))

        # Energie: kurze Inputs = niedrig, lange = ok
        if wortanzahl < 4:
            self.node.energie = max(0.2, self.node.energie - 0.02)
        else:
            self.node.energie = min(1.0, self.node.energie + 0.01)

        # Offenheit
        if neuer_zustand in ["neugierig", "enthusiastisch"]:
            self.node.offenheit = min(1.0, self.node.offenheit + 0.05)
        elif neuer_zustand in ["überfordert", "frustriert"]:
            self.node.offenheit = max(0.2, self.node.offenheit - 0.05)

        self.node.letzter_scan = time.strftime("%Y-%m-%d %H:%M:%S")

    # ── Kommunikations-Anpassung ───────────────────────────────────────────────
    def _bestimme_kommunikation(self,
                                low_res: bool) -> tuple[str, str]:
        """Gibt (ton, system_prompt_hinweis) zurück."""

        # Sektor 94: Low-Resolution-Protocol
        if low_res:
            return (
                "dekompress",
                "Steffen zeigt hohen Stress. Antworte sehr kurz (max. 3 Sätze), "
                "direkt und ohne Fachbegriffe. Keine Listen, keine Erklärungen. "
                "Nur das Wesentlichste."
            )

        hinweise = {
            "neutral": (
                "standard",
                "Antworte klar, strukturiert und vollständig."
            ),
            "fokussiert": (
                "kompakt",
                "Steffen ist im Fokus-Modus. Antworte kompakt und direkt. "
                "Keine Einleitungen, kein Overhead. Nur das Ergebnis."
            ),
            "frustriert": (
                "standard",
                "Steffen ist frustriert. Antworte ruhig, strukturiert und "
                "ohne Ausweichung. Direkt zum Problem. Keine Entschuldigungen."
            ),
            "neugierig": (
                "ausführlich",
                "Steffen ist neugierig. Erkläre Zusammenhänge, liefere "
                "Hintergrundwissen und mehrere Perspektiven. Ausführlich."
            ),
            "erschöpft": (
                "kompakt",
                "Steffen ist erschöpft. Antworte kurz, direkt, ohne Fachjargon. "
                "Max. 5 Sätze. Das Wichtigste zuerst."
            ),
            "enthusiastisch": (
                "ausführlich",
                "Steffen ist enthusiastisch. Gehe in die Tiefe, liefere Details "
                "und erweitere die Perspektive. Mitdenken erwünscht."
            ),
            "überfordert": (
                "dekompress",
                "Steffen ist überfordert. Sektor-93-Protokoll: Zerlege die "
                "Antwort in kleine Schritte. Beginne mit dem Einfachsten. "
                "Keine Informationsflut."
            ),
        }
        return hinweise.get(self.node.zustand, hinweise["neutral"])

    # ── Sektor 91: Interface-Inkompatibilität ──────────────────────────────────
    def _sektor91(self, text: str) -> str:
        """
        Erkennt Interface-Inkompatibilitäten —
        Situationen wo die Kommunikation grundsätzlich nicht passt.
        """
        text_lower = text.lower()

        # Wiederholter Fehler
        letzte_texte = [e["text"].lower() for e in self._verlauf[-5:]]
        if len(letzte_texte) >= 3:
            aehnlichkeit = sum(
                1 for t in letzte_texte[:-1]
                if len(set(t.split()) & set(text_lower.split())) >
                   len(text_lower.split()) * 0.6
            )
            if aehnlichkeit >= 2:
                return "Wiederholte Anfrage erkannt — vorherige Antworten möglicherweise unzureichend"

        # Konfliktsignal
        konflikt_marker = ["nein", "falsch", "das stimmt nicht", "das ist nicht",
                           "no", "wrong", "that's not", "incorrect"]
        if any(m in text_lower for m in konflikt_marker):
            return "Widerspruch zu vorheriger Antwort — Korrekturbedarf"

        return ""

    # ── Bericht ────────────────────────────────────────────────────────────────
    def bericht(self) -> str:
        n = self.node
        return (
            f"Empathie │ Node: {n.zustand} ({int(n.konfidenz*100)}%) │ "
            f"Energie: {n.energie:.2f} │ Stress: {n.stress:.2f} │ "
            f"Offenheit: {n.offenheit:.2f}"
        )

    def status_dict(self) -> dict:
        return {
            "zustand":   self.node.zustand,
            "energie":   round(self.node.energie, 2),
            "stress":    round(self.node.stress, 2),
            "offenheit": round(self.node.offenheit, 2),
            "konfidenz": round(self.node.konfidenz, 2),
        }

    # ── Persistenz ────────────────────────────────────────────────────────────
    def _save(self):
        try:
            MODELL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(MODELL_PATH, "w", encoding="utf-8") as f:
                json.dump(asdict(self.node), f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"Empathie-Modell nicht gespeichert: {e}")

    def _load(self):
        if not MODELL_PATH.exists():
            return
        try:
            with open(MODELL_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.node = NodeZustand(**{
                k: v for k, v in data.items()
                if k in NodeZustand.__dataclass_fields__
            })
            log.info(f"Empathie-Modell geladen: {self.node.zustand}")
        except Exception as e:
            log.warning(f"Empathie-Modell Ladefehler: {e}")


# ── Singleton ──────────────────────────────────────────────────────────────────
_empathie: Optional[EmpathieAlgorithmus] = None

def get_empathie() -> EmpathieAlgorithmus:
    global _empathie
    if _empathie is None:
        _empathie = EmpathieAlgorithmus()
    return _empathie
