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
from typing import Optional
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from scripts.lib.rate_limit import rate_limited_session, BatchPausedException

log = logging.getLogger(__name__)

BASE = "https://www.companywall.hr"
SEARCH_URL = BASE + "/pretraga?q={q}"

# Hrvatski mobilni prefiksi
MOBILE_PREFIXES = {"091", "092", "095", "097", "098", "099"}


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
                # zadnji cell je najnovija godina
                raw = cells[-1].replace(",", ".").strip()
                try:
                    return str(int(float(raw)))
                except ValueError:
                    return raw
    return ""


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

    # Status badge
    status_badge = soup.select_one(".badge-success, .badge-danger, .badge-warning")
    is_active = (status_badge and "aktiv" in status_badge.get_text(strip=True).lower()) or not blocked

    return {
        "oib": oib,
        "vlasnik": vlasnik,
        "telefon": telefon,
        "godina_osnutka": godina,
        "broj_zaposlenih": employees,
        "cw_blocked": blocked,
        "cw_active": is_active,
        "_has_mobile": bool(telefon and _is_mobile(telefon)),
    }


def _find_cw_url_by_name(session, name: str) -> Optional[str]:
    """Pretraga CW po imenu firme → URL prve detail stranice."""
    url = SEARCH_URL.format(q=quote_plus(name))
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        log.warning("CW search by name '%s' greška: %s", name, e)
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    link = soup.select_one("h3.text-uppercase.m-0.text-bold a[href*='/tvrtka/']")
    if link:
        return urljoin(BASE, link["href"])
    return None


def scrape_detail(lead: dict, limiter) -> dict:
    """
    Ulaz: lead dict s barem naziv_firme (i opcionalno cw_url ili web).
    Izlaz: enriched dict s CW podacima.

    Tok:
      1. Ako lead ima cw_url → koristi direktno
      2. Inače → search po nazivu → uzmi prvi rezultat
      3. Scrape detail page
    """
    with rate_limited_session(max_per_run=999) as (session, _):
        # Odredi CW URL
        cw_url = lead.get("cw_url")
        if not cw_url:
            # Provjeri je li web kolona CW URL (iz collect faze)
            web = lead.get("web", "")
            if "companywall.hr/tvrtka/" in web:
                cw_url = web

        if not cw_url:
            # Search po imenu
            limiter.check_limit()
            limiter.wait()
            try:
                cw_url = _find_cw_url_by_name(session, lead["naziv_firme"])
            except BatchPausedException:
                raise
            if cw_url:
                limiter.record_success()

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
        data["_cw_found"] = True
        data["_cw_url"] = cw_url
        return data
