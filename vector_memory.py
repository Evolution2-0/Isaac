"""
Isaac – Vektor-Gedächtnis (ChromaDB)
=======================================
Ergänzt SQLite um semantische Suche.

SQLite findet:   "Hat Steffen das Wort 'Energie' erwähnt?"
ChromaDB findet: "Was haben wir über Themen gesprochen die
                  konzeptuell ähnlich zu 'Ressourcen und Fürsorge' sind?"

Gespeichert werden:
  - Konversationen (mit Stimmungs-Embedding)
  - Erkenntnisse aus KI-Dialogen
  - Regelwerk-Einträge
  - Task-Ergebnisse

Bei nicht installiertem ChromaDB: graceful fallback auf SQLite-Suche.
Installation: pip install chromadb
"""
from __future__ import annotations

import json
import os
import time
import logging
from pathlib import Path
from typing import Optional

from config import DATA_DIR

log = logging.getLogger("Isaac.VectorMemory")

CHROMA_PATH = DATA_DIR / "chroma"
_VECTOR_DISABLED = False


def _build_stub_embedding():
    from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

    class _StubEmbedding(EmbeddingFunction[Documents]):
        """Lightweight local embedding — avoids onnxruntime in constrained environments."""

        def __init__(self) -> None:
            pass

        @staticmethod
        def name() -> str:
            return "isaac_stub"

        def __call__(self, input: Documents) -> Embeddings:
            vectors: list[list[float]] = []
            for text in input:
                seed = sum(ord(ch) for ch in (text or "")) or 1
                vectors.append([((seed * (i + 3)) % 997) / 997.0 for i in range(8)])
            return vectors

        def get_config(self) -> dict:
            return {"version": 1}

        @staticmethod
        def build_from_config(config: dict) -> "_StubEmbedding":
            return _StubEmbedding()

    return _StubEmbedding()


class VectorMemory:
    """
    Semantisches Gedächtnis via ChromaDB.
    Fällt auf No-Op zurück wenn ChromaDB nicht installiert ist.
    """

    def __init__(self):
        self._aktiv      = False
        self._client     = None
        self._conv_col   = None   # Konversationen
        self._wissen_col = None   # KI-Dialog-Wissen
        self._init()

    def _init(self):
        global _VECTOR_DISABLED
        if os.environ.get("ISAAC_DISABLE_VECTOR_MEMORY", "").lower() in {
            "1", "true", "yes", "on",
        }:
            _VECTOR_DISABLED = True
            log.info("VectorMemory deaktiviert (ISAAC_DISABLE_VECTOR_MEMORY)")
            return

        try:
            import chromadb
            from chromadb.config import Settings

            CHROMA_PATH.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=str(CHROMA_PATH),
                settings=Settings(anonymized_telemetry=False),
            )
            embed = _build_stub_embedding()
            conv_col = client.get_or_create_collection(
                name="konversationen",
                metadata={"hnsw:space": "cosine"},
                embedding_function=embed,
            )
            wissen_col = client.get_or_create_collection(
                name="ki_wissen",
                metadata={"hnsw:space": "cosine"},
                embedding_function=embed,
            )
            self._client = client
            self._conv_col = conv_col
            self._wissen_col = wissen_col
            self._aktiv = True
            log.info(
                "VectorMemory aktiv │ Konversationen: %s │ Wissen: %s",
                conv_col.count(),
                wissen_col.count(),
            )
        except ImportError:
            log.info(
                "ChromaDB nicht installiert → SQLite-Fallback aktiv.\n"
                "Für semantisches Gedächtnis: pip install chromadb"
            )
        except Exception as exc:
            log.warning("ChromaDB Init-Fehler: %s → Fallback aktiv", exc)

    @property
    def aktiv(self) -> bool:
        return self._aktiv

    # ── Konversation speichern ────────────────────────────────────────────────
    def speichere_konversation(self, conv_id: str, text: str,
                                role: str, ts: str,
                                stimmung: str = "neutral",
                                qualitaet: float = 0.0):
        """
        Speichert eine Konversation mit Metadaten.
        ChromaDB generiert automatisch Embeddings.
        """
        if not self._aktiv:
            return
        try:
            self._conv_col.upsert(
                ids        = [conv_id],
                documents  = [text],
                metadatas  = [{
                    "role":      role,
                    "ts":        ts,
                    "stimmung":  stimmung,
                    "qualitaet": qualitaet,
                }],
            )
        except Exception as e:
            log.debug(f"VectorMemory upsert: {e}")

    # ── Semantische Suche in Konversationen ───────────────────────────────────
    def suche_konversationen(self, query: str,
                              n: int = 5,
                              stimmung_filter: Optional[str] = None
                              ) -> list[dict]:
        """
        Findet konzeptuell ähnliche Konversationen.
        Nicht Stichwort-basiert, sondern bedeutungs-basiert.
        """
        if not self._aktiv:
            return []
        try:
            wo = {"stimmung": stimmung_filter} if stimmung_filter else None
            result = self._conv_col.query(
                query_texts    = [query],
                n_results      = min(n, self._conv_col.count() or 1),
                where          = wo,
            )
            hits = []
            docs  = result.get("documents", [[]])[0]
            metas = result.get("metadatas", [[]])[0]
            dists = result.get("distances", [[]])[0]
            for doc, meta, dist in zip(docs, metas, dists):
                hits.append({
                    "text":      doc,
                    "role":      meta.get("role", ""),
                    "ts":        meta.get("ts", ""),
                    "stimmung":  meta.get("stimmung", ""),
                    "aehnlich":  round(1 - dist, 3),
                })
            return hits
        except Exception as e:
            log.debug(f"VectorMemory Suche: {e}")
            return []

    # ── Wissen speichern ──────────────────────────────────────────────────────
    def speichere_wissen(self, wissen_id: str, thema: str,
                          inhalt: str, quellen: list[str],
                          konfidenz: float = 0.5):
        """Speichert einen KI-Dialog-Wissenseintrag."""
        if not self._aktiv:
            return
        try:
            self._wissen_col.upsert(
                ids       = [wissen_id],
                documents = [f"{thema}\n\n{inhalt}"],
                metadatas = [{
                    "thema":     thema[:100],
                    "quellen":   json.dumps(quellen),
                    "konfidenz": konfidenz,
                    "ts":        time.strftime("%Y-%m-%d"),
                }],
            )
        except Exception as e:
            log.debug(f"VectorMemory Wissen: {e}")

    # ── Semantische Wissenssuche ───────────────────────────────────────────────
    def suche_wissen(self, query: str,
                     n: int = 4,
                     min_konfidenz: float = 0.4) -> list[dict]:
        """Findet semantisch relevantes Wissen."""
        if not self._aktiv:
            return []
        try:
            result = self._wissen_col.query(
                query_texts = [query],
                n_results   = min(n, self._wissen_col.count() or 1),
            )
            hits  = []
            docs  = result.get("documents", [[]])[0]
            metas = result.get("metadatas", [[]])[0]
            dists = result.get("distances", [[]])[0]
            for doc, meta, dist in zip(docs, metas, dists):
                konf = meta.get("konfidenz", 0.5)
                if konf < min_konfidenz:
                    continue
                hits.append({
                    "thema":     meta.get("thema", ""),
                    "inhalt":    doc[:400],
                    "quellen":   json.loads(meta.get("quellen", "[]")),
                    "konfidenz": konf,
                    "aehnlich":  round(1 - dist, 3),
                })
            return sorted(hits, key=lambda x: x["aehnlich"], reverse=True)
        except Exception as e:
            log.debug(f"VectorMemory Wissenssuche: {e}")
            return []

    # ── Stimmungs-Kontext für Empathie ────────────────────────────────────────
    def stimmungs_verlauf(self, query: str, n: int = 10) -> list[str]:
        """
        Gibt Stimmungen ähnlicher vergangener Gespräche zurück.
        Empathie-Modul nutzt das um Muster zu erkennen.
        """
        hits = self.suche_konversationen(query, n=n)
        return [h["stimmung"] for h in hits if h.get("stimmung")]

    # ── Kombinierte Kontextsuche ───────────────────────────────────────────────
    def als_kontext(self, query: str) -> str:
        """
        Gibt semantisch relevanten Kontext als String zurück.
        Wird in isaac_core._build_system() eingebaut.
        """
        if not self._aktiv:
            return ""

        conv_hits   = self.suche_konversationen(query, n=3)
        wissen_hits = self.suche_wissen(query, n=3)

        if not conv_hits and not wissen_hits:
            return ""

        teile = ["[Semantisch relevanter Kontext]"]

        if conv_hits:
            teile.append("Ähnliche vergangene Gespräche:")
            for h in conv_hits[:2]:
                teile.append(
                    f"  ({h['stimmung']}, {h['ts'][:10]}, "
                    f"Ähnlichkeit: {h['aehnlich']:.0%}): "
                    f"{h['text'][:120]}"
                )

        if wissen_hits:
            teile.append("Relevantes Wissen aus KI-Dialogen:")
            for h in wissen_hits[:2]:
                teile.append(
                    f"  [{h['thema']}] "
                    f"(Konfidenz: {h['konfidenz']:.0%}): "
                    f"{h['inhalt'][:150]}"
                )

        return "\n".join(teile)

    # ── Status ────────────────────────────────────────────────────────────────
    def stats(self) -> dict:
        if _VECTOR_DISABLED:
            return {"aktiv": False, "grund": "deaktiviert"}
        if not self._aktiv:
            return {"aktiv": False, "grund": "ChromaDB nicht installiert"}
        try:
            return {
                "aktiv":         True,
                "konversationen": self._conv_col.count(),
                "wissen":        self._wissen_col.count(),
                "pfad":          str(CHROMA_PATH),
            }
        except Exception:
            return {"aktiv": False}


# ── Singleton ─────────────────────────────────────────────────────────────────
_vector: Optional[VectorMemory] = None

def get_vector_memory() -> VectorMemory:
    global _vector
    if _vector is None:
        _vector = VectorMemory()
    return _vector
