"""
provjera.hr — fallback za potvrdu OIB-a/statusa firme.
Koristi se SAMO ako CompanyWall ne nađe firmu.

URL: https://www.provjera.hr/pretraga?q={naziv ili OIB}
Stranica je statička, bez logina.
"""
import logging
import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE = "https://www.provjera.hr"
SEARCH_URL = BASE + "/pretraga?q={q}"


def check_company(session, name: str) -> dict:
    """
    Traži firmu po nazivu na provjera.hr.
    Vraća {'oib': str, 'active': bool, 'found': bool}.
    Samo za potvrdu postojanja/statusa — ne daje vlasnika/mobitel.
    """
    url = SEARCH_URL.format(q=quote_plus(name))
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        log.warning("provjera.hr greška za '%s': %s", name, e)
        return {"found": False}

    soup = BeautifulSoup(resp.text, "html.parser")

    # Pronađi prvi rezultat
    # provjera.hr prikazuje rezultate u tablici/listi
    result = soup.select_one("table tr:nth-child(2), .search-result:first-child, .company-row")
    if not result:
        # Pokušaj generičko — pronađi OIB pattern u tekstu
        text = soup.get_text()
        oib_match = re.search(r"\b\d{11}\b", text)
        if oib_match:
            return {"found": True, "oib": oib_match.group(), "active": True}
        return {"found": False}

    text = result.get_text(separator=" ")
    oib_match = re.search(r"\b(\d{11})\b", text)
    oib = oib_match.group(1) if oib_match else ""

    # Status — traži signal neaktivnosti
    active = True
    if any(word in text.lower() for word in ["brisana", "neaktivna", "stečaj", "likvidacija"]):
        active = False

    return {"found": True, "oib": oib, "active": active}
