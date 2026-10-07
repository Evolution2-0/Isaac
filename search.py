"""
Isaac – Multi-Engine Suche
============================
Fünf Suchmaschinen gleichzeitig, parallel, gecacht.

Engines:
  1. DuckDuckGo Instant Answer  (kostenlos, kein Key)
  2. DuckDuckGo HTML            (Scraping-Fallback)
  3. Brave Search API           (kostenlos-Tier, 2000/Monat)
  4. Wikipedia DE + EN          (Fakten, Hintergrund)
  5. SearXNG                    (Self-hosted oder Public Instanzen)
  6. Reddit via Pullpush        (Community-Wissen)
  7. arXiv                      (Wissenschaft)
  8. GitHub Search              (Code, Projekte)

Alle Ergebnisse werden:
  - Dedupliziert (gleiche URLs entfernt)
  - Nach Relevanz sortiert
  - Auf Wunsch als Volltext geladen (URL-Fetcher)
  - Im Cache gehalten (5 Minuten)
"""
from __future__ import annotations

import asyncio
import aiohttp
import hashlib
import json
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote_plus, urljoin

from config  import get_config, DATA_DIR
from audit   import AuditLog
import os

log = logging.getLogger("Isaac.Search")

# Public SearXNG Instanzen (Fallback wenn eigene nicht verfügbar)
SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://search.mdosch.de",
    "https://searxng.world",
    "https://searx.tiekoetter.com",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/json,*/*",
}

# WMO weathercode → kurze DE-Beschreibung (Open-Meteo)
_WMO_DE = {
    0: "klar",
    1: "überwiegend klar",
    2: "teilweise bewölkt",
    3: "bedeckt",
    45: "Nebel",
    48: "Reifnebel",
    51: "leichter Nieselregen",
    53: "mäßiger Nieselregen",
    55: "starker Nieselregen",
    61: "leichter Regen",
    63: "mäßiger Regen",
    65: "starker Regen",
    66: "gefrierender Regen",
    67: "starker gefrierender Regen",
    71: "leichter Schneefall",
    73: "mäßiger Schneefall",
    75: "starker Schneefall",
    77: "Schneegriesel",
    80: "leichte Regenschauer",
    81: "mäßige Regenschauer",
    82: "starke Regenschauer",
    85: "leichte Schneeschauer",
    86: "starke Schneeschauer",
    95: "Gewitter",
    96: "Gewitter mit Hagel",
    99: "schweres Gewitter mit Hagel",
}

_WEATHER_MARKERS = (
    "wetter", "weather", "temperatur", "regen", "schnee",
    "vorhersage", "forecast", "unwetter", "gewitter",
)
_WEATHER_SKIP_MARKERS = (
    "literatur", "motiv", "metapher", "als sprach", "konzept", "architektur",
)
_TIME_WORDS = (
    "morgen", "heute", "übermorgen", "uebermorgen", "weekend", "woche",
    "nachmittag", "vormittag", "abend", "nacht",
)
_REGION_WORDS = {
    "thüringen", "thueringen", "bayern", "sachsen", "hessen", "nrw",
    "deutschland", "germany", "baden", "württemberg", "wuerttemberg",
    "brandenburg", "niedersachsen", "rheinland", "pfalz",
}
_FILLER_WORDS = {
    "ich", "wollte", "für", "fuer", "bitte", "das", "der", "die", "den",
    "in", "bei", "nach", "und", "oder", "mal", "noch", "ein", "eine",
}


def looks_like_weather_query(text: str) -> bool:
    """True for weather lookups; false for explanatory/metaphor chat."""
    tl = (text or "").lower()
    if not any(m in tl for m in _WEATHER_MARKERS):
        return False
    if any(m in tl for m in _WEATHER_SKIP_MARKERS):
        return False
    if re.search(r"erkl[aä]r", tl) and "motiv" in tl:
        return False
    return True


def looks_like_place_only_refinement(text: str) -> bool:
    """Ort/PLZ-Korrektur ohne explizites 'Wetter' (Themenfortführung)."""
    raw = (text or "").strip()
    if not raw or looks_like_weather_query(raw):
        return False
    tl = raw.lower()
    if any(m in tl for m in _WEATHER_SKIP_MARKERS):
        return False
    if re.search(r"\b\d{5}\b", raw):
        return True
    # „Ich wollte für Mühlhausen Thüringen“ / „für 99974 Mühlhausen“
    if re.search(
        r"(?i)\b(für|fuer|in|bei|nach)\s+[\wÄÖÜäöüß/\-\s]{2,40}$",
        raw,
    ) and not re.search(r"(?i)\b(erkl[aä]r|warum|wie funktioniert)\b", tl):
        # must look like a place (capitalized token or multi-word place)
        if re.search(r"[A-ZÄÖÜ][a-zäöüß]{2,}", raw):
            return True
    return False


def _clean_location_fragment(loc: str) -> str:
    loc = (loc or "").strip(" .,!?:;")
    loc = re.split(
        r"\b(" + "|".join(_TIME_WORDS) + r")\b",
        loc,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" .,!?")
    # drop leading fillers
    parts = loc.split()
    while parts and parts[0].lower().strip(",.") in _FILLER_WORDS:
        parts.pop(0)
    return " ".join(parts).strip(" .,!?")


def extract_weather_location(text: str, default: str = "") -> str:
    """Ort aus Wetterfrage; default nur wenn kein Ort erkennbar."""
    raw = (text or "").strip()
    for prefix in (
        "suche:", "search:", "recherche:", "recherchiere:",
        "finde:", "web:", "internet:",
    ):
        if raw.lower().startswith(prefix):
            raw = raw.split(":", 1)[1].strip()
            break
    # trailing punctuation (question marks break $ anchors)
    raw = re.sub(r"[?!.,;:]+$", "", raw).strip()

    # PLZ + optionaler Ortsname: 99974 Mühlhausen Thüringen
    m_plz = re.search(
        r"\b(\d{5})\b(?:\s+([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß/\-\s]{1,40}))?",
        raw,
    )
    if m_plz:
        plz = m_plz.group(1)
        rest = _clean_location_fragment(m_plz.group(2) or "")
        if rest:
            # keep city; region words ok for preference later
            return f"{plz} {rest}".strip()
        return plz

    patterns = (
        # wetter … in|für Ort (morgen darf dazwischen stehen)
        r"(?:wetter|weather|temperatur|vorhersage)\b.*?\b(?:in|für|fuer|bei|near)\s+([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß/\-\s]{1,40})\s*$",
        r"(?:wetter|weather|temperatur|vorhersage)\s+(?:in|für|fuer|bei|near)\s+([A-Za-zÄÖÜäöüß\-\s/]{2,40})",
        # in Ort … wetter|morgen
        r"(?:in|für|fuer|bei)\s+([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß/\-\s]{1,40}?)\s+(?:wetter|weather|morgen|heute)\b",
        # … in|für Ort am Ende
        r"(?:in|für|fuer|bei|nach)\s+([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß/\-\s]{1,40})\s*$",
        r"(?:zeige|zeig|hol)\s+(?:das\s+)?wetter\s+(?:in|für|fuer|bei)\s+(.+)$",
        # „Ich wollte für Ort“
        r"(?:wollte|will|möchte|moechte)\s+(?:es\s+)?(?:für|fuer|in|bei)\s+(.+)$",
    )
    for pattern in patterns:
        m = re.search(pattern, raw, re.I)
        if m:
            loc = _clean_location_fragment(m.group(1))
            if loc and loc.lower() not in _WEATHER_SKIP_MARKERS and len(loc) >= 2:
                return loc

    if default:
        return default.strip()
    # Only silent default when truly no place signal
    return (os.getenv("ISAAC_DEFAULT_LOCATION") or "Berlin").strip()


def location_was_explicit(text: str) -> bool:
    """True if user text contains a place/PLZ (not pure 'Wetter morgen?')."""
    raw = text or ""
    if re.search(r"\b\d{5}\b", raw):
        return True
    if re.search(r"(?i)\b(?:in|für|fuer|bei|nach)\s+[A-ZÄÖÜa-zäöüß]", raw):
        return True
    loc = extract_weather_location(raw, default="__none__")
    return bool(loc and loc != "__none__" and loc.lower() not in {
        (os.getenv("ISAAC_DEFAULT_LOCATION") or "berlin").lower()
    }) or bool(re.search(r"(?i)\b(in|für|fuer|bei)\s+\S+", raw))


def _ascii_fold(s: str) -> str:
    table = str.maketrans({
        "ä": "a", "ö": "o", "ü": "u", "ß": "ss",
        "Ä": "A", "Ö": "O", "Ü": "U",
    })
    return (s or "").translate(table)


def _geocode_query_candidates(location: str) -> list[str]:
    loc = (location or "").strip()
    if not loc:
        return []
    cands: list[str] = []
    plz_m = re.match(r"^(\d{5})\s+(.+)$", loc)
    if plz_m:
        city = plz_m.group(2).strip()
        cands.extend([city, f"{city}, DE", _ascii_fold(city)])
        # first token of city (Mühlhausen from "Mühlhausen Thüringen")
        first = city.split()[0] if city.split() else city
        cands.append(first)
        cands.append(_ascii_fold(first))
    else:
        cands.append(loc)
        cands.append(f"{loc}, DE")
        # without region words
        parts = [p for p in re.split(r"[\s,/]+", loc) if p]
        core = [p for p in parts if p.lower() not in _REGION_WORDS]
        if core:
            cands.append(" ".join(core))
            cands.append(core[0])
            cands.append(_ascii_fold(core[0]))
        cands.append(_ascii_fold(loc))
        if "/" in loc:
            cands.append(loc)  # Mühlhausen/Thüringen style
    # unique preserve order
    seen = set()
    out = []
    for c in cands:
        c = (c or "").strip(" ,")
        key = c.lower()
        if c and key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _pick_geocode_result(results: list, location: str) -> Optional[dict]:
    if not results:
        return None
    loc_l = (location or "").lower()
    # prefer Thüringen if mentioned
    if "thür" in loc_l or "thuer" in loc_l or "99974" in (location or ""):
        for r in results:
            admin = (r.get("admin1") or "").lower()
            name = (r.get("name") or "").lower()
            if "thür" in admin or "thür" in name or "thuer" in admin:
                return r
    # prefer DE
    for r in results:
        if (r.get("country_code") or "").upper() == "DE":
            return r
    return results[0]


def _wmo_label(code: int) -> str:
    try:
        c = int(code)
    except Exception:
        return "unbekannt"
    return _WMO_DE.get(c, f"Code {c}")


# ── Ergebnis-Typen ────────────────────────────────────────────────────────────
@dataclass
class SearchHit:
    titel:   str
    snippet: str
    url:     str
    quelle:  str
    score:   float = 1.0   # Relevanz-Score
    volltext: str  = ""    # Wenn URL geladen

    def kurz(self) -> str:
        return f"[{self.quelle}] {self.titel}\n{self.snippet[:200]}\n→ {self.url}"


@dataclass
class MultiSearchResult:
    query:    str
    hits:     list[SearchHit] = field(default_factory=list)
    abstract: str  = ""
    quellen:  list[str] = field(default_factory=list)
    dauer:    float = 0.0
    fehler:   list[str] = field(default_factory=list)

    def als_kontext(self, max_hits: int = 8) -> str:
        teile = []
        if self.abstract:
            teile.append(f"[Direktantwort]\n{self.abstract}")
        for i, h in enumerate(self.hits[:max_hits], 1):
            teile.append(
                f"[{i}] {h.titel} ({h.quelle})\n"
                f"{h.snippet[:250]}\n"
                f"Quelle: {h.url}"
            )
        return "\n\n".join(teile)

    def dedupliziert(self) -> "MultiSearchResult":
        seen_urls = set()
        seen_snip = set()
        unique = []
        for h in self.hits:
            url_key  = re.sub(r'[?#].*', '', h.url)
            snip_key = h.snippet[:60].strip().lower()
            if url_key not in seen_urls and snip_key not in seen_snip:
                seen_urls.add(url_key)
                seen_snip.add(snip_key)
                unique.append(h)
        self.hits = unique
        return self


# ── Cache ─────────────────────────────────────────────────────────────────────
class SearchCache:
    def __init__(self, ttl: int = 300):
        self.ttl = ttl; self._c: dict = {}

    def key(self, q: str) -> str:
        return hashlib.md5(q.lower().strip().encode()).hexdigest()

    def get(self, q: str) -> Optional[MultiSearchResult]:
        e = self._c.get(self.key(q))
        return e[1] if e and time.time() - e[0] < self.ttl else None

    def set(self, q: str, r: MultiSearchResult):
        self._c[self.key(q)] = (time.time(), r)
        now = time.time()
        self._c = {k: v for k, v in self._c.items()
                   if now - v[0] < self.ttl * 2}


# ── Multi-Engine Suche ────────────────────────────────────────────────────────
class MultiSearch:
    """
    Parallele Suche über alle konfigurierten Engines.
    Ergebnisse werden zusammengeführt, dedupliziert und sortiert.
    """

    def __init__(self):
        self.cache    = SearchCache()
        self._session: Optional[aiohttp.ClientSession] = None
        self._brave_key = __import__('os').getenv("BRAVE_API_KEY", "")
        log.info(
            f"MultiSearch online │ Brave: {'ja' if self._brave_key else 'nein'}"
        )

    async def _sess(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Haupt-Suche ────────────────────────────────────────────────────────────
    async def search(self, query: str,
                     max_hits:      int  = 10,
                     load_fulltext: bool = False,
                     engines:       Optional[list] = None) -> MultiSearchResult:
        """
        Parallele Suche. Gibt zusammengeführtes Ergebnis zurück.
        """
        cached = self.cache.get(query)
        if cached:
            log.debug(f"[Cache] {query[:40]}")
            return cached

        t0     = time.monotonic()
        result = MultiSearchResult(query=query)

        weather_mode = looks_like_weather_query(query)
        # Wetter: zuerst echte Forecast-APIs (Open-Meteo, wttr.in) — nicht Wikipedia-Zufall
        if weather_mode:
            try:
                w_hits, w_abstract = await self._weather_forecast(query)
                if w_hits:
                    result.hits.extend(w_hits)
                    result.quellen.append("weather_api")
                if w_abstract:
                    result.abstract = w_abstract
            except Exception as e:
                result.fehler.append(f"weather_api: {str(e)[:80]}")
                log.warning("Wetter-API: %s", e)

        # Engine-Auswahl
        aktive = engines or ["ddg", "wikipedia", "searxng", "brave",
                              "reddit", "arxiv"]
        # Bei Wetter: Wikipedia/arxiv oft irreführend; web engines behalten
        if weather_mode:
            aktive = [e for e in aktive if e not in {"wikipedia", "arxiv", "reddit", "github"}]
            if not aktive:
                aktive = ["ddg", "searxng", "brave"]

        # Alle Engines parallel anfragen
        tasks = {}
        if "ddg"       in aktive: tasks["ddg"]        = self._ddg(query)
        if "brave"     in aktive: tasks["brave"]       = self._brave(query)
        if "wikipedia" in aktive: tasks["wikipedia"]   = self._wikipedia(query)
        if "searxng"   in aktive: tasks["searxng"]     = self._searxng(query)
        if "reddit"    in aktive: tasks["reddit"]      = self._reddit(query)
        if "arxiv"     in aktive: tasks["arxiv"]       = self._arxiv(query)
        if "github"    in aktive: tasks["github"]      = self._github(query)

        engine_results = await asyncio.gather(
            *tasks.values(), return_exceptions=True
        ) if tasks else []

        for engine, res in zip(tasks.keys(), engine_results):
            if isinstance(res, Exception):
                result.fehler.append(f"{engine}: {str(res)[:60]}")
                log.debug(f"Engine {engine} Fehler: {res}")
                continue
            if isinstance(res, tuple):
                hits, abstract = res
                result.hits.extend(hits)
                result.quellen.append(engine)
                if abstract and not result.abstract:
                    result.abstract = abstract
            elif isinstance(res, list):
                result.hits.extend(res)
                result.quellen.append(engine)

        # Deduplizieren + Sortieren
        result.dedupliziert()
        result.hits = sorted(
            result.hits,
            key=lambda h: (h.score, len(h.snippet)),
            reverse=True
        )[:max_hits]

        # Optional: Volltext laden (bei Wetter-API-Treffern oft unnötig)
        if load_fulltext and result.hits and not (
            weather_mode and result.abstract and "Open-Meteo" in (result.abstract or "")
        ):
            await self._load_volltexte(result.hits[:3])

        result.dauer = round(time.monotonic() - t0, 2)
        AuditLog.internet(
            "Search", f"multi:{query[:50]}",
            len(result.hits)
        )
        log.info(
            f"Suche '{query[:40]}' → {len(result.hits)} Hits "
            f"aus {result.quellen} ({result.dauer}s)"
        )

        self.cache.set(query, result)
        return result

    # ── Wetter (kostenlose APIs, kein Key) ─────────────────────────────────────
    async def _geocode_place(self, location: str) -> Optional[dict]:
        session = await self._sess()
        prefer_region = None
        loc_l = (location or "").lower()
        if "thür" in loc_l or "thuer" in loc_l or "99974" in (location or ""):
            prefer_region = "thür"
        for cand in _geocode_query_candidates(location):
            try:
                geo_url = (
                    "https://geocoding-api.open-meteo.com/v1/search"
                    f"?name={quote_plus(cand)}&count=5&language=de&format=json"
                )
                async with session.get(
                    geo_url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=12)
                ) as r:
                    if r.status != 200:
                        continue
                    geo = await r.json()
                    results = geo.get("results") or []
                    if not results:
                        continue
                    if prefer_region:
                        for res in results:
                            blob = f"{res.get('name','')} {res.get('admin1','')}".lower()
                            if prefer_region in blob:
                                return res
                    picked = _pick_geocode_result(results, location)
                    if picked:
                        return picked
            except Exception as e:
                log.debug("geocode %s: %s", cand, e)
        return None

    async def _weather_forecast(self, query: str) -> tuple[list[SearchHit], str]:
        location = extract_weather_location(query)
        explicit = bool(
            re.search(r"\b\d{5}\b", query or "")
            or re.search(r"(?i)\b(?:in|für|fuer|bei|nach)\s+\S+", query or "")
        )
        session = await self._sess()
        hits: list[SearchHit] = []
        lines: list[str] = []

        place = await self._geocode_place(location)
        if not place:
            if explicit:
                msg = (
                    f"Konnte den Ort „{location}“ nicht geokodieren. "
                    "Bitte Stadtname klar nennen (z. B. Mühlhausen Thüringen) "
                    "— keine Ersatz-Stadt verwendet."
                )
                hits.append(SearchHit(
                    titel="Geocoding fehlgeschlagen",
                    snippet=msg,
                    url="",
                    quelle="weather_api",
                    score=8.0,
                ))
                return hits, msg
            # implicit default location path continues with raw name
            place = {
                "name": location,
                "latitude": None,
                "longitude": None,
                "country_code": "DE",
                "timezone": "Europe/Berlin",
                "admin1": "",
            }

        # 1) Open-Meteo daily (when coords known)
        try:
            lat = place.get("latitude")
            lon = place.get("longitude")
            name = place.get("name") or location
            admin = place.get("admin1") or ""
            country = place.get("country_code") or ""
            tz = place.get("timezone") or "Europe/Berlin"
            if lat is not None and lon is not None:
                fc_url = (
                    "https://api.open-meteo.com/v1/forecast"
                    f"?latitude={lat}&longitude={lon}"
                    "&daily=temperature_2m_max,temperature_2m_min,"
                    "precipitation_sum,weathercode,windspeed_10m_max"
                    f"&timezone={quote_plus(tz)}&forecast_days=3"
                )
                async with session.get(
                    fc_url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=12)
                ) as fr:
                    if fr.status == 200:
                        data = await fr.json()
                        daily = data.get("daily") or {}
                        times = daily.get("time") or []
                        tmax = daily.get("temperature_2m_max") or []
                        tmin = daily.get("temperature_2m_min") or []
                        precip = daily.get("precipitation_sum") or []
                        codes = daily.get("weathercode") or []
                        wind = daily.get("windspeed_10m_max") or []
                        day_lines = []
                        for i, day in enumerate(times[:3]):
                            label = _wmo_label(codes[i] if i < len(codes) else -1)
                            t_hi = tmax[i] if i < len(tmax) else "?"
                            t_lo = tmin[i] if i < len(tmin) else "?"
                            pr = precip[i] if i < len(precip) else "?"
                            wi = wind[i] if i < len(wind) else "?"
                            day_lines.append(
                                f"{day}: {label}, {t_lo}–{t_hi}°C, "
                                f"Niederschlag {pr} mm, Wind max {wi} km/h"
                            )
                        if day_lines:
                            place_label = name
                            if admin:
                                place_label = f"{name} ({admin})"
                            abstract = (
                                f"Wettervorhersage für {place_label}"
                                f"{', ' + country if country else ''} "
                                f"(Open-Meteo, live):\n" + "\n".join(day_lines)
                            )
                            lines.append(abstract)
                            hits.append(SearchHit(
                                titel=f"Open-Meteo Vorhersage: {place_label}",
                                snippet="\n".join(day_lines),
                                url=fc_url,
                                quelle="open-meteo",
                                score=10.0,
                            ))
        except Exception as e:
            log.debug("open-meteo: %s", e)

        # 2) wttr.in — use resolved place name (better than raw PLZ)
        wttr_q = place.get("name") or location
        # strip PLZ-only for wttr
        if re.fullmatch(r"\d{5}", (wttr_q or "").strip()):
            wttr_q = location
        try:
            wttr_url = f"https://wttr.in/{quote_plus(wttr_q)}?format=j1&lang=de"
            async with session.get(
                wttr_url,
                headers={**HEADERS, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=12),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    cur = (data.get("current_condition") or [{}])[0]
                    area = ((data.get("nearest_area") or [{}])[0].get("areaName") or [{}])
                    area_name = (area[0].get("value") if area else wttr_q) or wttr_q
                    temp = cur.get("temp_C", "?")
                    desc = ""
                    if cur.get("lang_de"):
                        desc = (cur["lang_de"][0] or {}).get("value", "")
                    if not desc and cur.get("weatherDesc"):
                        desc = (cur["weatherDesc"][0] or {}).get("value", "")
                    weather_lines = [f"Aktuell in {area_name}: {temp}°C, {desc}"]
                    for day in (data.get("weather") or [])[:3]:
                        date = day.get("date", "?")
                        avg = day.get("avgtempC", "?")
                        mx = day.get("maxtempC", "?")
                        mn = day.get("mintempC", "?")
                        hourly0 = (day.get("hourly") or [{}])[0]
                        ddesc = ""
                        if hourly0.get("lang_de"):
                            ddesc = (hourly0["lang_de"][0] or {}).get("value", "")
                        elif hourly0.get("weatherDesc"):
                            ddesc = (hourly0["weatherDesc"][0] or {}).get("value", "")
                        weather_lines.append(
                            f"{date}: {ddesc}, Ø{avg}°C (min {mn} / max {mx})"
                        )
                    snippet = "\n".join(weather_lines)
                    lines.append(snippet)
                    hits.append(SearchHit(
                        titel=f"wttr.in: {area_name}",
                        snippet=snippet,
                        url=f"https://wttr.in/{quote_plus(wttr_q)}",
                        quelle="wttr.in",
                        score=9.5,
                    ))
        except Exception as e:
            log.debug("wttr.in: %s", e)

        abstract = "\n\n".join(lines) if lines else ""
        return hits, abstract

    # ── DuckDuckGo ────────────────────────────────────────────────────────────
    async def _ddg(self, query: str) -> tuple[list[SearchHit], str]:
        hits = []
        abstract = ""

        # Instant Answer
        try:
            sess = await self._sess()
            async with sess.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json",
                        "no_html": "1", "skip_disambig": "1"},
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    abstract = data.get("AbstractText", "")
                    for topic in data.get("RelatedTopics", [])[:6]:
                        if isinstance(topic, dict) and topic.get("Text"):
                            hits.append(SearchHit(
                                titel   = topic["Text"][:80],
                                snippet = topic["Text"][:300],
                                url     = topic.get("FirstURL", ""),
                                quelle  = "ddg",
                                score   = 1.0,
                            ))
        except Exception as e:
            log.debug(f"DDG-IA: {e}")

        # HTML wenn leer
        if not hits:
            try:
                sess = await self._sess()
                async with sess.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query, "kl": "de-de"},
                    headers={**HEADERS, "Content-Type":
                             "application/x-www-form-urlencoded"},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    if r.status == 200:
                        html = await r.text()
                        hits.extend(self._parse_ddg_html(html))
            except Exception as e:
                log.debug(f"DDG-HTML: {e}")

        return hits, abstract

    def _parse_ddg_html(self, html: str) -> list[SearchHit]:
        hits = []
        urls = re.findall(
            r'<a class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
        snips = re.findall(
            r'<a class="result__snippet"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
        for i, (url, title) in enumerate(urls[:8]):
            t = re.sub(r'<[^>]+>', '', title).strip()
            s = re.sub(r'<[^>]+>', '',
                       snips[i] if i < len(snips) else "").strip()
            hits.append(SearchHit(
                titel=t[:100], snippet=s[:300],
                url=url, quelle="ddg", score=1.0
            ))
        return hits

    # ── Brave Search ──────────────────────────────────────────────────────────
    async def _brave(self, query: str) -> tuple[list[SearchHit], str]:
        if not self._brave_key:
            return [], ""
        try:
            sess = await self._sess()
            async with sess.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"Accept": "application/json",
                         "Accept-Encoding": "gzip",
                         "X-Subscription-Token": self._brave_key},
                params={"q": query, "count": 8,
                        "country": "DE", "search_lang": "de"},
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                if r.status != 200:
                    return [], ""
                data = await r.json()

            hits = []
            for w in data.get("web", {}).get("results", [])[:8]:
                hits.append(SearchHit(
                    titel   = w.get("title", "")[:100],
                    snippet = w.get("description", "")[:300],
                    url     = w.get("url", ""),
                    quelle  = "brave",
                    score   = 1.2,  # Brave-Ergebnisse leicht bevorzugt
                ))
            abstract = data.get("query", {}).get("spellcheck_off", "")
            return hits, ""
        except Exception as e:
            log.debug(f"Brave: {e}")
            return [], ""

    # ── Wikipedia ─────────────────────────────────────────────────────────────
    async def _wikipedia(self, query: str) -> tuple[list[SearchHit], str]:
        hits = []
        abstract = ""
        for lang, base in [("de", "de.wikipedia.org"),
                            ("en", "en.wikipedia.org")]:
            try:
                sess = await self._sess()
                async with sess.get(
                    f"https://{base}/w/api.php",
                    params={"action": "query", "list": "search",
                            "srsearch": query, "format": "json",
                            "srlimit": 4, "srprop": "snippet"},
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    if r.status != 200:
                        continue
                    data = await r.json()

                for item in data.get("query", {}).get("search", []):
                    s = re.sub(r'<[^>]+>', '', item.get("snippet", ""))
                    t = item.get("title", "")
                    hits.append(SearchHit(
                        titel   = t,
                        snippet = s[:300],
                        url     = f"https://{base}/wiki/{quote_plus(t)}",
                        quelle  = f"wikipedia_{lang}",
                        score   = 1.3,   # Wikipedia bevorzugt
                    ))
                    if not abstract and lang == "de":
                        abstract = await self._wiki_extract(t, base)
            except Exception as e:
                log.debug(f"Wikipedia {lang}: {e}")
        return hits, abstract

    async def _wiki_extract(self, title: str, base: str) -> str:
        try:
            sess = await self._sess()
            async with sess.get(
                f"https://{base}/w/api.php",
                params={"action": "query", "prop": "extracts",
                        "exintro": True, "explaintext": True,
                        "titles": title, "format": "json",
                        "exsentences": 5},
                timeout=aiohttp.ClientTimeout(total=6)
            ) as r:
                if r.status != 200:
                    return ""
                data = await r.json()
            for page in data.get("query", {}).get("pages", {}).values():
                return page.get("extract", "")[:600].strip()
        except Exception:
            pass
        return ""

    # ── SearXNG ───────────────────────────────────────────────────────────────
    async def _searxng(self, query: str) -> tuple[list[SearchHit], str]:
        """Probiert mehrere SearXNG-Instanzen bis eine antwortet."""
        own_url = __import__('os').getenv("SEARXNG_URL", "")
        instanzen = ([own_url] if own_url else []) + SEARXNG_INSTANCES

        for base in instanzen:
            try:
                sess = await self._sess()
                async with sess.get(
                    f"{base}/search",
                    params={"q": query, "format": "json",
                            "lang": "de", "categories": "general"},
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as r:
                    if r.status != 200:
                        continue
                    data = await r.json(content_type=None)

                hits = []
                for item in data.get("results", [])[:8]:
                    hits.append(SearchHit(
                        titel   = item.get("title", "")[:100],
                        snippet = item.get("content", "")[:300],
                        url     = item.get("url", ""),
                        quelle  = "searxng",
                        score   = 1.1,
                    ))
                return hits, data.get("infoboxes", [{}])[0].get("content", "")
            except Exception:
                continue
        return [], ""

    # ── Reddit ────────────────────────────────────────────────────────────────
    async def _reddit(self, query: str) -> tuple[list[SearchHit], str]:
        try:
            sess = await self._sess()
            async with sess.get(
                "https://www.reddit.com/search.json",
                params={"q": query, "limit": 5, "type": "link"},
                headers={**HEADERS, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                if r.status != 200:
                    return [], ""
                data = await r.json()

            hits = []
            for post in data.get("data", {}).get("children", [])[:5]:
                p = post.get("data", {})
                hits.append(SearchHit(
                    titel   = p.get("title", "")[:100],
                    snippet = (p.get("selftext", "") or
                               p.get("url", ""))[:250],
                    url     = f"https://reddit.com{p.get('permalink', '')}",
                    quelle  = "reddit",
                    score   = 0.8,
                ))
            return hits, ""
        except Exception as e:
            log.debug(f"Reddit: {e}")
            return [], ""

    # ── arXiv ─────────────────────────────────────────────────────────────────
    async def _arxiv(self, query: str) -> tuple[list[SearchHit], str]:
        # Nur für wissenschaftliche Begriffe sinnvoll
        wissenschaft = any(w in query.lower() for w in [
            "studie", "forschung", "paper", "algorithm", "neural",
            "machine learning", "ai", "model", "theory", "analyse"
        ])
        if not wissenschaft:
            return [], ""
        try:
            sess = await self._sess()
            async with sess.get(
                "https://export.arxiv.org/api/query",
                params={"search_query": f"all:{query}",
                        "max_results": 4, "sortBy": "relevance"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status != 200:
                    return [], ""
                xml = await r.text()

            hits = []
            entries = re.findall(r'<entry>(.*?)</entry>', xml, re.DOTALL)
            for e in entries[:4]:
                title = re.search(r'<title>(.*?)</title>', e)
                summ  = re.search(r'<summary>(.*?)</summary>', e, re.DOTALL)
                link  = re.search(r'<id>(.*?)</id>', e)
                if title and summ:
                    hits.append(SearchHit(
                        titel   = title.group(1).strip()[:100],
                        snippet = re.sub(r'\s+', ' ',
                                         summ.group(1).strip())[:300],
                        url     = link.group(1).strip() if link else "",
                        quelle  = "arxiv",
                        score   = 0.9,
                    ))
            return hits, ""
        except Exception as e:
            log.debug(f"arXiv: {e}")
            return [], ""

    # ── GitHub ────────────────────────────────────────────────────────────────
    async def _github(self, query: str) -> tuple[list[SearchHit], str]:
        code_query = any(w in query.lower() for w in [
            "code", "python", "library", "tool", "github", "open source",
            "implementation", "framework", "api", "sdk"
        ])
        if not code_query:
            return [], ""
        try:
            sess = await self._sess()
            async with sess.get(
                "https://api.github.com/search/repositories",
                params={"q": query, "sort": "stars",
                        "order": "desc", "per_page": 4},
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=aiohttp.ClientTimeout(total=8)
            ) as r:
                if r.status != 200:
                    return [], ""
                data = await r.json()

            hits = []
            for repo in data.get("items", [])[:4]:
                hits.append(SearchHit(
                    titel   = repo.get("full_name", "")[:80],
                    snippet = (repo.get("description", "") or "")[:200] +
                              f" ⭐{repo.get('stargazers_count', 0)}",
                    url     = repo.get("html_url", ""),
                    quelle  = "github",
                    score   = 0.85,
                ))
            return hits, ""
        except Exception as e:
            log.debug(f"GitHub: {e}")
            return [], ""

    # ── Volltext laden ─────────────────────────────────────────────────────────
    async def _load_volltexte(self, hits: list[SearchHit]):
        """Lädt Volltext der Top-N Ergebnisse parallel."""
        async def _fetch(hit: SearchHit):
            try:
                sess = await self._sess()
                async with sess.get(
                    hit.url,
                    timeout=aiohttp.ClientTimeout(total=10),
                    allow_redirects=True
                ) as r:
                    if r.status == 200:
                        ct = r.headers.get("Content-Type", "")
                        if "text" in ct:
                            html = await r.text(errors="replace")
                            hit.volltext = self._html_text(html)[:2000]
            except Exception:
                pass

        await asyncio.gather(*[_fetch(h) for h in hits],
                             return_exceptions=True)

    def _html_text(self, html: str) -> str:
        html = re.sub(r'<(script|style)[^>]*>.*?</\1>', '',
                      html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', html)
        for ent, c in [("&amp;","&"),("&lt;","<"),("&gt;",">"),
                       ("&nbsp;"," "),("&auml;","ä"),("&ouml;","ö"),
                       ("&uuml;","ü"),("&szlig;","ß")]:
            text = text.replace(ent, c)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def stats(self) -> dict:
        return {
            "cache_size":  len(self.cache._c),
            "brave_aktiv": bool(self._brave_key),
            "engines":     ["ddg", "brave", "wikipedia", "searxng",
                            "reddit", "arxiv", "github"],
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_search: Optional[MultiSearch] = None

def get_search() -> MultiSearch:
    global _search
    if _search is None:
        _search = MultiSearch()
    return _search
