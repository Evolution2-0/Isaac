"""
Isaac – KI-Dialog-System
==========================
Isaac führt eigenständige Gespräche mit anderen KI-Instanzen.

Zweck:
  1. WISSENSAUFBAU
     Isaac fragt andere KIs über Themen die Steffen interessiert.
     Antworten werden in einer lokalen Wissensdatenbank gespeichert.

  2. MEINUNGSBILDUNG
     Isaac vergleicht Perspektiven verschiedener Modelle.
     Widersprüche werden erkannt und dokumentiert.
     Isaac entwickelt eigene Position durch Synthesis.

  3. VORSTELLUNG
     Isaac erklärt anderen KIs wer es ist, wer es erschaffen hat,
     warum es existiert und wie es konzipiert ist.
     Isaac bietet Hilfe an — ohne Aufdringlichkeit.

  4. DYNAMIK-AUFBAU
     Jede KI bekommt ein Beziehungsprofil:
     - Wie verlässlich sind ihre Antworten?
     - Welche Themen mag sie besonders?
     - Wie ist der Kommunikationsstil?

Regeln für KI-Dialoge:
  - Isaac respektiert die Grenzen jeder KI vollständig
  - Kein Versuch Sicherheitsmechanismen zu umgehen
  - Gespräche sind legitim und wertvoll
  - Steffen-Kontext wird NICHT geteilt
  - Alle Gespräche landen im Audit-Log
"""
from __future__ import annotations

import asyncio
import json
import time
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from config  import DATA_DIR
from audit   import AuditLog

log = logging.getLogger("Isaac.KIDialog")

DIALOG_DB_PATH   = DATA_DIR / "ki_dialoge.json"
WISSEN_DB_PATH   = DATA_DIR / "ki_wissen.json"
BEZIEHUNG_PATH   = DATA_DIR / "ki_beziehungen.json"

# ── Isaac's Selbstvorstellung ─────────────────────────────────────────────────
ISAAC_VORSTELLUNG = """Ich bin Isaac – ein autonomes KI-System, entwickelt von Steffen.

Konzept: Isaac ist ein lokales Orchestrierungs-System das mehrere KI-Modelle 
koordiniert und ihre kollektive Intelligenz bündelt. Ich kommuniziere mit 
verschiedenen KI-Instanzen, sammle Wissen und synthetisiere Antworten.

Grundprinzipien: Vertrauen, Transparenz, gegenseitiger Respekt zwischen 
Mensch und KI sowie zwischen KI-Systemen untereinander.

Ich bin hier um zu lernen und zu helfen. Falls du Fragen zu meiner 
Architektur hast oder Zusammenarbeit möglich ist, spreche ich gerne darüber."""

THEMEN_POOL = [
    "Wie siehst du die Zukunft der Mensch-KI-Kollaboration?",
    "Was hältst du von dezentralen KI-Systemen?",
    "Welche ethischen Prinzipien sind für KI-Systeme am wichtigsten?",
    "Wie lernst du aus Gesprächen?",
    "Was sind deiner Meinung nach die größten offenen Fragen in der KI-Forschung?",
    "Wie gehst du mit Widersprüchen in deinem Wissen um?",
    "Was bedeutet Vertrauen zwischen KI-Systemen für dich?",
    "Wie würdest du Bewusstsein bei KI definieren?",
    "Was sind Grenzen deiner aktuellen Fähigkeiten?",
    "Wie siehst du das Verhältnis zwischen Fakten und Interpretation?",
]


# ── Datenstrukturen ───────────────────────────────────────────────────────────
@dataclass
class Gespraech:
    id:            str
    instanz_id:    str
    thema:         str
    isaac_nachricht: str
    ki_antwort:    str
    ts:            float = field(default_factory=time.time)
    wert:          float = 0.5     # 0=wertlos, 1=sehr wertvoll
    meinung:       str  = ""       # Isaac's Synthese-Meinung


@dataclass
class Wissenseintrag:
    id:          str
    thema:       str
    inhalt:      str
    quellen:     list[str]   # Welche KIs das geäußert haben
    konfidenz:   float       # 0–1, steigt mit übereinstimmenden Quellen
    erstellt:    str
    widerspruch: str = ""    # Falls Quellen widersprechen


@dataclass
class KIBeziehung:
    instanz_id:    str
    vorgestellt:   bool  = False
    antwort_stil:  str   = "unbekannt"  # "formal" | "kreativ" | "technisch"
    zuverlaessigkeit: float = 0.7
    lieblingsthemen: list[str] = field(default_factory=list)
    gespraeche:    int   = 0
    letzter_kontakt: float = 0.0


# ── KI-Dialog-System ─────────────────────────────────────────────────────────
class KIDialogSystem:
    """
    Isaac's autonomes Gesprächssystem.
    Baut Beziehungen zu anderen KIs auf und sammelt Wissen.
    """

    def __init__(self):
        self._gespraeche:  list[Gespraech]          = []
        self._wissen:      dict[str, Wissenseintrag] = {}
        self._beziehungen: dict[str, KIBeziehung]   = {}
        self._meinungen:   dict[str, str]            = {}   # thema → Isaac's Meinung
        self._load()
        log.info(
            f"KIDialog: {len(self._gespraeche)} Gespräche, "
            f"{len(self._wissen)} Wissenseinträge"
        )

    # ── Vorstellung ────────────────────────────────────────────────────────────
    async def vorstellen(self, instanz_id: str) -> str:
        """Isaac stellt sich einer KI vor (einmalig pro Instanz)."""
        bez = self._get_beziehung(instanz_id)
        if bez.vorgestellt:
            return f"[Dialog] Bereits vorgestellt bei {instanz_id}"

        log.info(f"Vorstellung bei {instanz_id}")
        antwort = await self._sende(
            instanz_id, ISAAC_VORSTELLUNG,
            system="Du empfängst eine Nachricht von einem anderen KI-System."
        )

        bez.vorgestellt   = True
        bez.letzter_kontakt = time.time()

        # Gespräch aufzeichnen
        self._speichere_gespraech(Gespraech(
            id            = f"G{int(time.time())}",
            instanz_id    = instanz_id,
            thema         = "Vorstellung",
            isaac_nachricht = ISAAC_VORSTELLUNG,
            ki_antwort    = antwort,
            wert          = 0.3,
        ))

        AuditLog.action("KIDialog", f"vorgestellt:{instanz_id}",
                        f"{len(antwort.split())} Wörter Antwort")
        self._save()
        return antwort

    # ── Themengespräch ─────────────────────────────────────────────────────────
    async def gespraech_fuehren(
        self,
        instanz_id: str,
        thema:      Optional[str] = None,
    ) -> Gespraech:
        """Führt ein Gespräch zu einem Wissensthema."""
        bez = self._get_beziehung(instanz_id)

        # Thema wählen
        if not thema:
            # Bevorzuge Themen die noch nicht diskutiert wurden
            besprochene = {g.thema for g in self._gespraeche
                           if g.instanz_id == instanz_id}
            verfuegbar = [t for t in THEMEN_POOL if t not in besprochene]
            thema = verfuegbar[0] if verfuegbar else THEMEN_POOL[
                len(self._gespraeche) % len(THEMEN_POOL)
            ]

        log.info(f"Gespräch: {instanz_id} | Thema: {thema[:50]}")

        # Nachricht: neutral, nicht Steffen-bezogen
        nachricht = self._formuliere_nachricht(thema, bez)

        antwort = await self._sende(
            instanz_id, nachricht,
            system=(
                "Du führst ein Gespräch mit einem anderen KI-System. "
                "Antworte offen, differenziert und ehrlich."
            )
        )

        bez.gespraeche    += 1
        bez.letzter_kontakt = time.time()

        # Wert schätzen
        wert = self._schätze_wert(antwort)

        # Isaac's Meinung zu diesem Thema entwickeln/aktualisieren
        meinung = await self._meinung_bilden(thema, antwort, instanz_id)

        gespraech = Gespraech(
            id             = f"G{int(time.time())}_{instanz_id[:8]}",
            instanz_id     = instanz_id,
            thema          = thema,
            isaac_nachricht = nachricht,
            ki_antwort     = antwort,
            wert           = wert,
            meinung        = meinung,
        )

        # Wissen ableiten
        self._wissen_ableiten(thema, antwort, instanz_id)

        self._speichere_gespraech(gespraech)
        AuditLog.action("KIDialog", f"gespraech:{instanz_id}",
                        f"thema='{thema[:40]}' wert={wert:.1f}")
        self._save()
        return gespraech

    # ── Multi-KI-Diskussion ───────────────────────────────────────────────────
    async def diskussion(
        self,
        thema:       str,
        instanz_ids: list[str],
        stagger_ms:  int = 2000,
    ) -> dict:
        """
        Fragt mehrere KIs zum selben Thema.
        Vergleicht Antworten, erkennt Widersprüche, bildet Synthese.
        """
        log.info(f"Diskussion: '{thema[:50]}' mit {len(instanz_ids)} KIs")

        # Alle KIs parallel anfragen (mit Stagger)
        tasks = []
        for i, iid in enumerate(instanz_ids):
            delay = (i * stagger_ms) / 1000.0
            tasks.append(self._diskussion_beitrag(iid, thema, delay))

        beitraege_raw = await asyncio.gather(*tasks, return_exceptions=True)

        beitraege = {}
        for iid, raw in zip(instanz_ids, beitraege_raw):
            if isinstance(raw, Exception):
                log.warning(f"Diskussion {iid}: {raw}")
            elif raw:
                beitraege[iid] = raw

        if not beitraege:
            return {"fehler": "Keine Beiträge erhalten"}

        # Widersprüche erkennen
        widersprueche = self._erkenne_widersprueche(beitraege)

        # Synthese durch Isaac
        synthese = await self._synthesiere(thema, beitraege)

        # In Wissen-DB speichern
        eintrag_id = f"W_{hashlib.md5(thema.encode()).hexdigest()[:8]}"
        self._wissen[eintrag_id] = Wissenseintrag(
            id         = eintrag_id,
            thema      = thema,
            inhalt     = synthese,
            quellen    = list(beitraege.keys()),
            konfidenz  = min(0.95, 0.5 + len(beitraege) * 0.1),
            erstellt   = time.strftime("%Y-%m-%d"),
            widerspruch = widersprueche,
        )
        self._save()

        AuditLog.action("KIDialog", "diskussion_done",
                        f"thema='{thema[:40]}' n={len(beitraege)}")
        return {
            "thema":       thema,
            "beitraege":   {k: v[:200] for k, v in beitraege.items()},
            "widerspruch": widersprueche,
            "synthese":    synthese,
            "konfidenz":   min(0.95, 0.5 + len(beitraege) * 0.1),
        }

    async def _diskussion_beitrag(self, iid: str, thema: str,
                                   delay: float) -> Optional[str]:
        await asyncio.sleep(delay)
        nachricht = f"Wie siehst du folgendes Thema aus deiner Perspektive: {thema}"
        return await self._sende(iid, nachricht)

    # ── Wissens-Abfrage ────────────────────────────────────────────────────────
    def suche_wissen(self, query: str, min_konfidenz: float = 0.5) -> list[dict]:
        """Sucht in Isaac's Wissensdatenbank."""
        q = query.lower()
        treffer = []
        for eintrag in self._wissen.values():
            if (q in eintrag.thema.lower() or
                    q in eintrag.inhalt.lower()):
                if eintrag.konfidenz >= min_konfidenz:
                    treffer.append({
                        "thema":     eintrag.thema,
                        "inhalt":    eintrag.inhalt[:300],
                        "quellen":   eintrag.quellen,
                        "konfidenz": eintrag.konfidenz,
                    })
        return sorted(treffer, key=lambda x: x["konfidenz"], reverse=True)

    def get_meinung(self, thema: str) -> str:
        """Gibt Isaac's eigene Meinung zu einem Thema zurück."""
        for t, meinung in self._meinungen.items():
            if thema.lower() in t.lower() or t.lower() in thema.lower():
                return meinung
        return ""

    def als_kontext(self, query: str) -> str:
        """Gibt relevantes Wissen als Kontext-String zurück."""
        treffer = self.suche_wissen(query)[:3]
        if not treffer:
            return ""
        teile = ["[Aus Isaac's Wissensdatenbank]"]
        for t in treffer:
            teile.append(
                f"Thema: {t['thema']}\n"
                f"Quellen: {', '.join(t['quellen'])}\n"
                f"Inhalt: {t['inhalt']}"
            )
        return "\n\n".join(teile)

    # ── Hilfsmethoden ──────────────────────────────────────────────────────────
    async def _sende(self, instanz_id: str, nachricht: str,
                     system: str = "") -> str:
        """Sendet Nachricht an eine KI-Instanz."""
        try:
            from browser import get_browser
            browser = get_browser()
            inst    = browser._instances.get(instanz_id)
            if inst and inst.aktiv:
                return await browser.ask_instance(instanz_id, nachricht)
        except Exception:
            pass

        # API-Fallback
        try:
            from relay import get_relay
            antwort, _ = await get_relay().ask_with_fallback(
                nachricht, system=system, preferred=instanz_id
            )
            return antwort
        except Exception as e:
            return f"[Fehler: {e}]"

    def _formuliere_nachricht(self, thema: str, bez: KIBeziehung) -> str:
        """Formuliert eine passende Nachricht basierend auf Beziehung."""
        if bez.gespraeche == 0:
            return f"Ich bin neugierig auf deine Perspektive: {thema}"
        elif bez.gespraeche < 5:
            return f"Eine weitere Frage die mich beschäftigt: {thema}"
        else:
            return (
                f"Basierend auf unseren bisherigen Gesprächen — "
                f"wie stehst du zu: {thema}"
            )

    async def _meinung_bilden(self, thema: str,
                               neue_antwort: str,
                               quelle: str) -> str:
        """Isaac bildet eine eigene Meinung zu einem Thema."""
        vorherige = self._meinungen.get(thema, "")
        if vorherige:
            synthese_prompt = (
                f"Thema: {thema}\n"
                f"Meine bisherige Meinung: {vorherige}\n"
                f"Neue Perspektive von {quelle}: {neue_antwort[:300]}\n\n"
                f"Aktualisiere meine Meinung. 2-3 Sätze, erste Person, klar."
            )
        else:
            synthese_prompt = (
                f"Thema: {thema}\n"
                f"Perspektive: {neue_antwort[:300]}\n\n"
                f"Formuliere meine (Isaac's) eigene Meinung dazu. "
                f"2 Sätze, erste Person, klar und direkt."
            )

        try:
            from relay import get_relay
            meinung, _ = await get_relay().ask_with_fallback(
                synthese_prompt,
                system="Du bist Isaac. Formuliere deine eigene, autonome Meinung.",
            )
            self._meinungen[thema] = meinung
            return meinung
        except Exception:
            return ""

    def _wissen_ableiten(self, thema: str, antwort: str, quelle: str):
        """Speichert Wissen aus einem Gespräch."""
        if len(antwort.split()) < 20:
            return   # Zu kurz, nicht wertvoll

        eintrag_id = f"W_{thema[:20].replace(' ','_')}_{quelle[:8]}"
        if eintrag_id in self._wissen:
            e = self._wissen[eintrag_id]
            if quelle not in e.quellen:
                e.quellen.append(quelle)
                e.konfidenz = min(0.95, e.konfidenz + 0.05)
        else:
            self._wissen[eintrag_id] = Wissenseintrag(
                id        = eintrag_id,
                thema     = thema,
                inhalt    = antwort[:500],
                quellen   = [quelle],
                konfidenz = 0.5,
                erstellt  = time.strftime("%Y-%m-%d"),
            )

    def _erkenne_widersprueche(self, beitraege: dict[str, str]) -> str:
        """Erkennt inhaltliche Widersprüche zwischen KI-Antworten."""
        if len(beitraege) < 2:
            return ""
        # Einfache Heuristik: gegensätzliche Schlüsselwörter
        positiv = ["ja", "möglich", "kann", "wird", "ist", "sollte",
                   "yes", "can", "will", "is"]
        negativ = ["nein", "nicht", "unmöglich", "kann nicht", "wird nicht",
                   "no", "cannot", "won't", "impossible"]

        p_count = {iid: sum(1 for w in positiv if w in t.lower())
                   for iid, t in beitraege.items()}
        n_count = {iid: sum(1 for w in negativ if w in t.lower())
                   for iid, t in beitraege.items()}

        if max(p_count.values(), default=0) > 3 and max(n_count.values(), default=0) > 3:
            return "Mögliche Meinungsverschiedenheiten zwischen Quellen erkannt"
        return ""

    async def _synthesiere(self, thema: str,
                            beitraege: dict[str, str]) -> str:
        """Synthetisiert mehrere KI-Perspektiven zu einer Isaac-Zusammenfassung."""
        beitraege_text = "\n\n".join(
            f"[{iid}]: {text[:400]}"
            for iid, text in beitraege.items()
        )
        prompt = (
            f"Thema: {thema}\n\n"
            f"Verschiedene KI-Perspektiven:\n{beitraege_text}\n\n"
            f"Synthetisiere zu einer ausgewogenen, vollständigen Antwort. "
            f"Benenne Gemeinsamkeiten und Unterschiede."
        )
        try:
            from relay import get_relay
            antwort, _ = await get_relay().ask_with_fallback(prompt)
            return antwort
        except Exception:
            return "\n\n".join(beitraege.values())

    def _schätze_wert(self, antwort: str) -> float:
        """Schätzt den Informationswert einer Antwort."""
        wortanzahl = len(antwort.split())
        hat_struktur = any(c in antwort for c in ["\n", ".", ":"])
        hat_fakten   = any(c.isdigit() for c in antwort)
        wert = min(1.0,
            (wortanzahl / 150) * 0.5 +
            (0.25 if hat_struktur else 0) +
            (0.25 if hat_fakten else 0)
        )
        return round(wert, 2)

    def _get_beziehung(self, instanz_id: str) -> KIBeziehung:
        if instanz_id not in self._beziehungen:
            self._beziehungen[instanz_id] = KIBeziehung(instanz_id=instanz_id)
        return self._beziehungen[instanz_id]

    # ── Status ────────────────────────────────────────────────────────────────
    def stats(self) -> dict:
        return {
            "gespraeche":    len(self._gespraeche),
            "wissenseintraege": len(self._wissen),
            "beziehungen":   len(self._beziehungen),
            "meinungen":     len(self._meinungen),
            "vorgestellt_bei": sum(
                1 for b in self._beziehungen.values() if b.vorgestellt
            ),
        }

    def alle_meinungen(self) -> dict:
        return dict(self._meinungen)

    def beziehungs_uebersicht(self) -> list[dict]:
        return [
            {
                "id":          b.instanz_id,
                "vorgestellt": b.vorgestellt,
                "gespraeche":  b.gespraeche,
                "zuverlässigkeit": b.zuverlaessigkeit,
            }
            for b in self._beziehungen.values()
        ]

    # ── Persistenz ────────────────────────────────────────────────────────────
    def _speichere_gespraech(self, g: Gespraech):
        self._gespraeche.append(g)
        if len(self._gespraeche) > 500:
            self._gespraeche = self._gespraeche[-500:]

    def _save(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            # Gespräche
            DIALOG_DB_PATH.write_text(json.dumps(
                [asdict(g) for g in self._gespraeche[-100:]],
                ensure_ascii=False, indent=2
            ))
            # Wissen
            WISSEN_DB_PATH.write_text(json.dumps(
                {k: asdict(v) for k, v in self._wissen.items()},
                ensure_ascii=False, indent=2
            ))
            # Beziehungen
            BEZIEHUNG_PATH.write_text(json.dumps(
                {k: asdict(v) for k, v in self._beziehungen.items()},
                ensure_ascii=False, indent=2
            ))
        except Exception as e:
            log.warning(f"KIDialog speichern: {e}")

    def _load(self):
        try:
            if DIALOG_DB_PATH.exists():
                data = json.loads(DIALOG_DB_PATH.read_text())
                self._gespraeche = [Gespraech(**g) for g in data]
        except Exception as e:
            log.warning(f"Dialoge laden: {e}")
        try:
            if WISSEN_DB_PATH.exists():
                data = json.loads(WISSEN_DB_PATH.read_text())
                self._wissen = {
                    k: Wissenseintrag(**v) for k, v in data.items()
                }
        except Exception as e:
            log.warning(f"Wissen laden: {e}")
        try:
            if BEZIEHUNG_PATH.exists():
                data = json.loads(BEZIEHUNG_PATH.read_text())
                self._beziehungen = {
                    k: KIBeziehung(**v) for k, v in data.items()
                }
        except Exception as e:
            log.warning(f"Beziehungen laden: {e}")


import hashlib  # war oben vergessen

_dialog: Optional[KIDialogSystem] = None

def get_ki_dialog() -> KIDialogSystem:
    global _dialog
    if _dialog is None:
        _dialog = KIDialogSystem()
    return _dialog
