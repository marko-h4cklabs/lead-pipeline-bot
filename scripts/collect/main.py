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

# Opcionalni parametri — prazno znači "koristi default"
NICHE_QUERY = os.environ.get("NICHE_QUERY", "").strip()
_exc_raw = os.environ.get("EXCLUDED_COUNTIES", "Međimurska,Varaždinska").strip()
EXCLUDED_COUNTIES: set[str] = {c.strip() for c in _exc_raw.split(",") if c.strip()}


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

    # Spillover kvote: Places dobiva soft cap (pola max_new). CW limit
    # se računa NAKON Places-a — uzima neiskorištenu kvotu automatski.
    # Primjer: max_new=80, Places nađe 4 → CW dobiva 76 umjesto 40.
    places_soft_cap = MAX_NEW_PER_RUN // 2

    # --- Parametri logiranje ---
    if NICHE_QUERY:
        log.info("NICHE_QUERY: %r (overrideava default liste)", NICHE_QUERY)
    else:
        log.info("NICHE_QUERY: prazno — koriste se default niše")
    log.info("EXCLUDED_COUNTIES: %s", EXCLUDED_COUNTIES or "(bez filtera)")
    log.info("Kvota: max=%d, Places soft cap=%d (spillover → CW)", MAX_NEW_PER_RUN, places_soft_cap)

    # --- Izvor 1: Google Places ---
    log.info("Google Places: pokretanje...")
    places_queries = [NICHE_QUERY] if NICHE_QUERY else None
    places_count = 0
    for lead in iter_all_searches(queries=places_queries, excluded_counties=EXCLUDED_COUNTIES):
        if places_count >= places_soft_cap:
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
            "obradjen_flag": "NE",
        })
        new_leads.append(lead)
        checker.register(lead)
        places_count += 1
    log.info("Places: %d novih leadova (soft cap: %d)", places_count, places_soft_cap)

    # --- Izvor 2: CompanyWall search ---
    # Spillover: CW dobiva sve što Places nije iskoristio od svog soft capa.
    cw_limit = MAX_NEW_PER_RUN - places_count
    log.info("CompanyWall: pokretanje (kvota: %d)...", cw_limit)
    if EXCLUDED_COUNTIES:
        log.info("Napomena: CW county filter nije dostupan u collect fazi — nema zupanija podatka pri searchu.")
    cw_queries = [NICHE_QUERY] if NICHE_QUERY else NISE_QUERIES
    cw_count = 0
    for query in cw_queries:
        if cw_count >= cw_limit:
            break
        for item in search_companywall(query, max_pages=2):
            if cw_count >= cw_limit:
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
                "obradjen_flag": "NE",
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
    try:
        client.batch_append(new_leads)
    except Exception as e:
        log.error("Greška batch upisa: %s", e)

    log.info("=== Collect završen: %d novih redova ===", len(new_leads))


if __name__ == "__main__":
    run()
