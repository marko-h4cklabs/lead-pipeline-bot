"""
Dio 1 — Lead Collecting
Pokreni: python -m scripts.collect.main

Izvora: Google Places API + CompanyWall.hr search
Dedupe → upis u Google Sheet s status='new'
"""
import logging
import os
import sys
from datetime import datetime

# dodaj projekt root u path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from scripts.lib.dedupe import DupeChecker
from scripts.lib.lead_id import new_lead_id
from scripts.lib.sheets import SheetsClient
from scripts.collect.places_client import iter_all_searches
from scripts.collect.companywall_search import search_companywall, NISE_QUERIES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("collect")

GITHUB_PAGES_BASE = os.environ.get("GITHUB_PAGES_BASE", "https://YOUR_USERNAME.github.io/YOUR_REPO/")
MAX_NEW_PER_RUN = int(os.environ.get("MAX_NEW_PER_RUN", "80"))


def make_link(lead_id: str, naziv: str, vlasnik: str = "", opis: str = "") -> str:
    from urllib.parse import urlencode
    params = {"lead_id": lead_id, "naziv": naziv, "vlasnik": vlasnik, "opis": opis}
    return GITHUB_PAGES_BASE + "?" + urlencode(params)


def run():
    log.info("=== Collect start ===")
    client = SheetsClient()
    existing = client.read_all()
    checker = DupeChecker(existing)
    log.info("Učitano %d postojećih redova iz Sheeta", len(existing))

    new_leads = []
    seen_cw_urls: set[str] = set()

    # --- Izvor 1: Google Places ---
    log.info("Google Places: pokretanje...")
    places_count = 0
    for lead in iter_all_searches():
        if len(new_leads) >= MAX_NEW_PER_RUN:
            break
        is_dup, reason = checker.is_duplicate(lead)
        if is_dup:
            log.debug("Places duplikat: %s (%s)", lead.get("naziv_firme"), reason)
            continue
        lid = new_lead_id()
        lead.update({
            "lead_id": lid,
            "status": "new",
            "datum_dodan": datetime.now().strftime("%Y-%m-%d"),
            "link": make_link(lid, lead.get("naziv_firme", "")),
        })
        new_leads.append(lead)
        checker.register(lead)
        places_count += 1
    log.info("Places: %d novih leadova", places_count)

    # --- Izvor 2: CompanyWall search ---
    log.info("CompanyWall: pokretanje...")
    cw_count = 0
    for query in NISE_QUERIES:
        if len(new_leads) >= MAX_NEW_PER_RUN:
            break
        for item in search_companywall(query, max_pages=2):
            if len(new_leads) >= MAX_NEW_PER_RUN:
                break
            cw_url = item.get("cw_url", "")
            if cw_url in seen_cw_urls:
                continue
            seen_cw_urls.add(cw_url)

            # Postavi web na CW URL PRIJE dedupe — bez toga name+domain check ne radi
            # (cw_url je jedinstven po firmi, pa "companywall.hr" + naziv = dobar ključ)
            if not item.get("web"):
                item["web"] = cw_url

            # CompanyWall u collect fazi — nemamo OIB/tel pa koristimo naziv+cw_url za dedupe
            is_dup, reason = checker.is_duplicate(item)
            if is_dup:
                log.debug("CW duplikat: %s (%s)", item.get("naziv_firme"), reason)
                continue

            lid = new_lead_id()
            item.update({
                "lead_id": lid,
                "web": item.get("web") or cw_url,  # web je već postavljen gore
                "status": "new",
                "datum_dodan": datetime.now().strftime("%Y-%m-%d"),
                "link": make_link(lid, item.get("naziv_firme", "")),
            })
            new_leads.append(item)
            checker.register(item)
            cw_count += 1

    log.info("CompanyWall: %d novih leadova", cw_count)

    if not new_leads:
        log.info("Nema novih leadova — završeno.")
        return

    # --- Upis u Sheet (batch — jedan API poziv za sve redove) ---
    log.info("Upisujem %d novih leadova u Sheet...", len(new_leads))

    # DEBUG: provjeri sadržaj prvog reda PRIJE batch_append
    from scripts.lib.sheets import COLUMNS as _COLS
    _first = new_leads[0]
    _row0 = [_first.get(col, "__MISSING__") for col in _COLS]
    log.info("DEBUG values[0] keys u leadu: %s", list(_first.keys()))
    log.info("DEBUG values[0] (%d elemenata): %s", len(_row0), _row0)
    log.info("DEBUG lead_id na poziciji 0: %r", _row0[0])
    log.info("DEBUG link na poziciji 16: %r", _row0[16])

    try:
        client.batch_append(new_leads)
    except Exception as e:
        log.error("Greška batch upisa: %s", e)

    log.info("=== Collect završen: %d novih redova ===", len(new_leads))


if __name__ == "__main__":
    run()
