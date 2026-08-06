"""
CompanyWall.hr search — Dio 1 collect faza.
Samo dohvaća naziv + URL detail stranice, ne hita svaki profil (to je Dio 2).

URL: https://companywall.hr/pretraga?q={query}&p={page}
Svaki result: h3.text-uppercase a[href*="/tvrtka/"] → detail URL
Paginacija:   nav[aria-label] a[href*="p="]
"""
import logging
import re
from typing import Iterator
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from scripts.lib.rate_limit import rate_limited_session, BatchPausedException

log = logging.getLogger(__name__)

BASE = "https://www.companywall.hr"
SEARCH_URL = BASE + "/pretraga?q={q}&p={p}"

# Nišni upiti za HR tržište
NISE_QUERIES = [
    "vodoinstalater", "elektroinstalater", "klimatizacija servis",
    "fasader", "krovopokrivač", "soboslikar ličilac", "keramičar",
    "stolar namještaj", "bravар locksmith",
    "autolakirer", "servis kotlova", "dimnjačar",
    "selitbena agencija", "zaštitarska tvrtka",
    "krajobrazno uređenje", "hortikultura",
    "građevinska tvrtka adaptacija", "renoviranje stanova",
    "usluge čišćenja tvrtka", "odvoz otpada tvrtka",
    "servis klima uređaja", "solarni paneli montaža",
    "ugradnja prozora vrata", "parketari",
]


def _search_one_page(session, query: str, page: int = 1) -> list[dict]:
    """Dohvaća jednu stranicu search rezultata; vraća listu {naziv, cw_url}."""
    url = SEARCH_URL.format(q=quote_plus(query), p=page)
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        log.warning("CW search greška '%s' str.%d: %s", query, page, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for a in soup.select("h3.text-uppercase.m-0.text-bold a[href*='/tvrtka/']"):
        href = a.get("href", "")
        if not href:
            continue
        full_url = urljoin(BASE, href)
        naziv = a.get_text(strip=True)
        if naziv:
            results.append({"naziv_firme": naziv, "cw_url": full_url, "izvor": "CompanyWall"})
    return results


def _max_page(soup) -> int:
    """Pronađi zadnju stranicu paginacije."""
    nav = soup.select_one("nav[aria-label] .pagination")
    if not nav:
        return 1
    nums = []
    for a in nav.find_all("a", href=True):
        m = re.search(r"p=(\d+)", a["href"])
        if m:
            nums.append(int(m.group(1)))
    return max(nums) if nums else 1


def search_companywall(query: str, max_pages: int = 3) -> Iterator[dict]:
    """
    Pretražuje CompanyWall za dani upit, prolazi kroz stranice.
    Vraća dict-ove s naziv_firme + cw_url.
    max_pages ograničava broj stranica po upitu.
    """
    with rate_limited_session(max_per_run=200) as (session, limiter):
        for page in range(1, max_pages + 1):
            try:
                url = SEARCH_URL.format(q=quote_plus(query), p=page)
                resp = limiter.get(session, url, timeout=15)
                resp.raise_for_status()
            except BatchPausedException:
                log.warning("CW batch limit dosegnut za '%s'", query)
                return
            except Exception as e:
                log.warning("Greška stranica %d za '%s': %s", page, query, e)
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            items = []
            for a in soup.select("h3.text-uppercase.m-0.text-bold a[href*='/tvrtka/']"):
                href = a.get("href", "")
                if not href:
                    continue
                full_url = urljoin(BASE, href)
                naziv = a.get_text(strip=True)
                if naziv:
                    items.append({"naziv_firme": naziv, "cw_url": full_url, "izvor": "CompanyWall"})

            if not items:
                break

            for item in items:
                yield item

            if page == 1:
                max_pg = _max_page(soup)
                max_pages = min(max_pages, max_pg)
