"""
CompanyWall.hr detail page scraper — Dio 2 verify faza.

Potvrđeni selektori (testirani na 2 profila 04.08.2026.):
  - dl dt → dd parovi za sve ključne podatke
  - span.badge-success → status "Aktivan"
  - Telefoni: span s /^0\d{8,9}$/ — mobile prefiksi 091/092/095/097/098/099
  - Broj zaposlenih: tr koji sadrži "Broj zaposlenih" → zadnji td

Ako firma nije pronađena po CW URL-u, search po nazivu pa uzmi prvi rezultat.
"""
import logging
import re
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from scripts.lib.rate_limit import rate_limited_session, BatchPausedException

log = logging.getLogger(__name__)

BASE = "https://www.companywall.hr"
SEARCH_URL = BASE + "/pretraga?q={q}"

# Prag sličnosti naziva pri name-based CW lookup (SequenceMatcher ratio, 0–1)
# Ispod praga → manual_review bez upisa OIB/kontakata
NAME_SIMILARITY_THRESHOLD = 0.55

# Markeri koji ukazuju na inozemnu (ne-HR) firmu na CW stranici.
# CompanyWall indeksira firme iz RS, BA, SI, ME, MK — i za HR leade one su uvijek greška.
FOREIGN_CW_MARKERS = frozenset({
    "srbija",
    "republika srbija",
    "bosna i hercegovina",
    "republika srpska",
    "federacija bosne i hercegovine",
    "slovenija",
    "crna gora",
    "makedonija",
    "sjeverna makedonija",
    "albanija",
    "kosovo",
    "registarski broj",  # srpski/bosanski poslovni registar (umjesto OIB-a)
    "pib:",              # Poreski identifikacioni broj (srpski PDV ID)
    "jib:",              # Jedinstveni identifikacioni broj (bosanski porezni ID)
    "matični broj preduzeća",  # srpski MB
})

# Hrvatski mobilni prefiksi
MOBILE_PREFIXES = {"091", "092", "095", "097", "098", "099"}

# Pravni sufiksi koji se ignoriraju pri usporedbi naziva firmi
_LEGAL_SUFFIXES = re.compile(
    r'\b(d\.?o\.?o|j\.?d\.?o\.?o|d\.?d|o\.?b\.?r|s\.?p|k\.?d|p\.?o\.?o|z\.?a\.?d|d\.?n\.?o)\.?\b',
    re.IGNORECASE,
)


def _normalize_name(name: str) -> str:
    """Lowercase, ukloni pravni sufiks, normaliziraj whitespace."""
    n = _LEGAL_SUFFIXES.sub('', name.lower())
    return re.sub(r'\s+', ' ', n).strip()


def _is_foreign_company(soup: BeautifulSoup) -> str | None:
    """
    Vraća nađeni marker (string) ako CW stranica pripada inozemnoj (ne-HR) firmi,
    inače None. Koristi se kao rana barijera — HR firme uvijek imaju OIB, ne PIB/JIB/MB.
    """
    page_text = soup.get_text(separator=" ", strip=True).lower()
    for marker in FOREIGN_CW_MARKERS:
        if marker in page_text:
            return marker
    return None


def name_similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio između normaliziranih naziva firmi (0.0–1.0)."""
    return SequenceMatcher(None, _normalize_name(a), _normalize_name(b)).ratio()


def _is_mobile(phone: str) -> bool:
    """Vraća True ako je broj mobilni (HR prefiksi)."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("385"):
        digits = "0" + digits[3:]
    return len(digits) >= 9 and digits[:3] in MOBILE_PREFIXES


def _extract_dt_map(soup: BeautifulSoup) -> dict[str, str]:
    """
    Dohvaća sve dl dt → dd parove.
    Ključevi su lowercase normalized tekst dt-a (bez ikona/whitespace).
    """
    dt_map = {}
    for dt in soup.select("dl dt"):
        # Uzmi samo text node-ove (ignoriraj ikone <i>)
        key_parts = [c for c in dt.children if isinstance(c, str)]
        key = " ".join(k.strip() for k in key_parts).strip().lower()
        if not key:
            key = dt.get_text(separator=" ", strip=True).lower()
        dd = dt.find_next_sibling("dd")
        if dd:
            dt_map[key] = dd.get_text(separator="\n", strip=True)
    return dt_map


def _extract_phones(soup: BeautifulSoup) -> tuple[str, list[str]]:
    """
    Vraća (mobile_primary, sve_phone_liste).
    Primary je prvi mobilni broj; ako nema mobilnog, prvi dostupni.
    """
    phone_spans = [
        s.get_text(strip=True)
        for s in soup.find_all("span")
        if re.match(r"^0\d{8,9}$", s.get_text(strip=True))
    ]
    # Dedupe, čuvaj redoslijed
    seen = set()
    unique = []
    for p in phone_spans:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    mobiles = [p for p in unique if _is_mobile(p)]
    primary = mobiles[0] if mobiles else (unique[0] if unique else "")
    return primary, unique


def _extract_employees(soup: BeautifulSoup) -> str:
    """Uzima broj zaposlenih iz zadnjeg stupca retka 'Broj zaposlenih' u fin. tablici."""
    for row in soup.find_all("tr"):
        if "Broj zaposlenih" in row.get_text():
            cells = [td.get_text(strip=True) for td in row.find_all("td") if td.get_text(strip=True)]
            if len(cells) >= 2:
                raw = cells[-1].replace(",", ".").strip()
                try:
                    return str(int(float(raw)))
                except ValueError:
                    return raw
    return ""


def _parse_hr_number(raw: str) -> Optional[int]:
    """
    Parsira HR broj format: '1.234.567,00' → 1234567, '0,00' → 0.
    Vraća None ako parsing ne uspije ili string je '-'/'–'/prazan.
    """
    raw = raw.strip()
    if not raw or raw in ("-", "–", "N/A", "n/a"):
        return None
    # Ukloni tisućice separatore (točke u HR formatu), zamijeni decimalni zarez s točkom
    cleaned = raw.replace(".", "").replace(",", ".")
    # Uzmi samo znamenke i decimalnu točku
    cleaned = re.sub(r"[^\d.]", "", cleaned)
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _extract_revenue(soup: BeautifulSoup) -> Optional[int]:
    """
    Vraća zadnji dostupni godišnji prihod (Ukupni prihodi) u EUR/HRK.
    Returns None ako podaci nisu dostupni na stranici.
    Returns 0 ako je eksplicitno 0 (firma bez prihoda).
    """
    keywords = ("Ukupni prihodi", "Prihodi od prodaje", "Prihodi")
    for row in soup.find_all("tr"):
        row_text = row.get_text()
        if any(kw in row_text for kw in keywords) and "rashodi" not in row_text.lower():
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            # Zadrži samo neprazne ćelije (preskači "-" i prazne)
            non_empty = [c for c in cells if c and c not in ("-", "–")]
            if non_empty:
                return _parse_hr_number(non_empty[-1])
    return None


def _extract_year(date_str: str) -> str:
    """'21.02.2022.' → '2022'"""
    m = re.search(r"\b(\d{4})\b", date_str)
    return m.group(1) if m else ""


def _is_blocked(dt_map: dict, soup: BeautifulSoup) -> bool:
    """True ako je firma u blokadi (računi ili badge)."""
    racuni = dt_map.get("računi", "").lower()
    if "u blokadi" in racuni and "nije" not in racuni:
        return True
    # Fallback: provjeri da nema badge-danger za status
    for badge in soup.select(".badge-danger, .badge-warning"):
        txt = badge.get_text(strip=True).lower()
        if "blokad" in txt or "neaktiv" in txt:
            return True
    return False


def _parse_detail_page(html: str) -> dict:
    """Parsira HTML detail stranice i vraća dict s podacima."""
    soup = BeautifulSoup(html, "html.parser")
    dt_map = _extract_dt_map(soup)

    oib = dt_map.get("oib", "").strip()
    vlasnik = dt_map.get("vlasnik", dt_map.get("direktor", "")).strip()
    datum_osn = dt_map.get("datum osnivanja", "").strip()
    godina = _extract_year(datum_osn)

    telefon, _ = _extract_phones(soup)
    employees = _extract_employees(soup)
    blocked = _is_blocked(dt_map, soup)
    prihod = _extract_revenue(soup)

    # Status badge
    status_badge = soup.select_one(".badge-success, .badge-danger, .badge-warning")
    is_active = (status_badge and "aktiv" in status_badge.get_text(strip=True).lower()) or not blocked

    foreign_marker = _is_foreign_company(soup)
    return {
        "oib": oib,
        "vlasnik": vlasnik,
        "telefon": telefon,
        "godina_osnutka": godina,
        "broj_zaposlenih": employees,
        "prihod": prihod,          # int u EUR/HRK, None = nije dostupno
        "cw_blocked": blocked,
        "cw_active": is_active,
        "_has_mobile": bool(telefon and _is_mobile(telefon)),
        "_cw_foreign": foreign_marker,  # None za HR firme; string markera za inozemne
    }


def _find_cw_url_by_name(session, name: str) -> tuple[Optional[str], Optional[str]]:
    """
    Pretraga CW po imenu firme.
    Returns (cw_url, found_company_name) — oba None ako pretraga ne vrati rezultat.
    """
    url = SEARCH_URL.format(q=quote_plus(name))
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        log.warning("CW search by name '%s' greška: %s", name, e)
        return None, None
    soup = BeautifulSoup(resp.text, "html.parser")
    link = soup.select_one("h3.text-uppercase.m-0.text-bold a[href*='/tvrtka/']")
    if link:
        found_name = link.get_text(strip=True)
        return urljoin(BASE, link["href"]), found_name
    return None, None


def scrape_detail(lead: dict, limiter) -> dict:
    """
    Ulaz: lead dict s barem naziv_firme (i opcionalno cw_url ili web).
    Izlaz: enriched dict s CW podacima.

    Tok:
      1. Ako lead ima cw_url / web s CW URL-om → koristi direktno (potvrđen izvor)
      2. Inače → search po nazivu → provjera sličnosti → scrape (izveden izvor)

    Sigurnosna provjera za izveden put:
      Ako je name_similarity(traženi_naziv, pronađeni_naziv) < NAME_SIMILARITY_THRESHOLD,
      vraća {"_cw_found": False, "_name_mismatch": True, ...} — caller ga tretira
      kao manual_review bez upisa OIB/kontakata.
    """
    with rate_limited_session(max_per_run=999) as (session, _):
        # Odredi CW URL
        cw_url = lead.get("cw_url")
        via_name_search = False
        cw_found_name: Optional[str] = None

        if not cw_url:
            # Provjeri je li web kolona CW URL (iz collect faze)
            web = lead.get("web", "")
            if "companywall.hr/tvrtka/" in web:
                cw_url = web

        if not cw_url:
            # Izveden put: search po imenu firme
            limiter.check_limit()
            limiter.wait()
            try:
                cw_url, cw_found_name = _find_cw_url_by_name(session, lead["naziv_firme"])
            except BatchPausedException:
                raise
            if cw_url:
                limiter.record_success()
                via_name_search = True
                # Provjera sličnosti naziva — prva barijera protiv pogrešnog matchanja
                sim = name_similarity(lead["naziv_firme"], cw_found_name or "")
                if sim < NAME_SIMILARITY_THRESHOLD:
                    log.warning(
                        "CW name mismatch za '%s' — pronađeno '%s' (sličnost=%.0f%% < %.0f%%) → manual_review",
                        lead["naziv_firme"], cw_found_name, sim * 100, NAME_SIMILARITY_THRESHOLD * 100,
                    )
                    return {
                        "_cw_found": False,
                        "_name_mismatch": True,
                        "_cw_found_name": cw_found_name,
                        "_similarity": sim,
                    }

        if not cw_url:
            log.info("CW: nije nađena firma '%s'", lead.get("naziv_firme"))
            return {"_cw_found": False}

        # Scrape detail page
        try:
            resp = limiter.get(session, cw_url, timeout=20)
            if resp.status_code == 404:
                log.info("CW 404 za '%s'", lead.get("naziv_firme"))
                return {"_cw_found": False}
            resp.raise_for_status()
        except BatchPausedException:
            raise
        except Exception as e:
            log.warning("CW detail greška '%s': %s", lead.get("naziv_firme"), e)
            return {"_cw_found": False, "_cw_error": str(e)}

        data = _parse_detail_page(resp.text)

        # Rana barijera: CW vratio stranicu inozemne firme (RS, BA, SI, ...).
        # HR firme uvijek imaju OIB — prisustvo PIB/JIB/MB ili naziva strane države
        # je siguran znak da smo udarili u krivi rezultat CW pretrage.
        foreign_marker = data.get("_cw_foreign")
        if foreign_marker:
            log.warning(
                "CW inozemna firma za '%s' — detektiran marker '%s' na %s "
                "— vraćam _cw_found=False",
                lead.get("naziv_firme"), foreign_marker, cw_url,
            )
            return {
                "_cw_found": False,
                "_foreign": True,
                "_foreign_marker": foreign_marker,
                "_cw_url": cw_url,
            }

        data["_cw_found"] = True
        data["_cw_url"] = cw_url
        data["_via_name_search"] = via_name_search
        if cw_found_name:
            data["_cw_found_name"] = cw_found_name
        return data
