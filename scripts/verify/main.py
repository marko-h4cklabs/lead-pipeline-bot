"""
Dio 2 — Verifikacija
Pokreni: python -m scripts.verify.main

Ulaz: redovi sa status='new'
Izlaz: status → 'verified' / 'manual_review' / 'rejected'
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from scripts.lib.rate_limit import RateLimiter, rate_limited_session, BatchPausedException
from scripts.lib.sheets import SheetsClient
from scripts.verify.companywall_detail import scrape_detail
from scripts.verify.provjera_hr import check_company
from scripts.verify.web_scan import scan_website, extract_homepage_text
from scripts.verify.claude_summary import generate_opis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("verify")

MAX_PER_RUN = int(os.environ.get("MAX_VERIFY_PER_RUN", "80"))


def run():
    log.info("=== Verify start ===")
    client = SheetsClient()

    # Dohvati samo 'new' redove
    new_rows = client.read_by_status("new")
    log.info("Pronađeno %d 'new' redova za verifikaciju", len(new_rows))

    if not new_rows:
        log.info("Nema novih redova — završeno.")
        return

    limiter = RateLimiter(max_per_run=MAX_PER_RUN)
    processed = 0

    with rate_limited_session(max_per_run=MAX_PER_RUN * 3) as (session, _):
        for row in new_rows:
            if processed >= MAX_PER_RUN:
                log.info("Dosegnut limit od %d po runu — stajemo.", MAX_PER_RUN)
                break

            lead_id = row.get("lead_id", "")
            naziv = row.get("naziv_firme", "")
            log.info("[%s] Verificiram: %s", lead_id, naziv)

            updates = {}

            # --- Korak 1: Web-scan (ako ima web URL) ---
            web_url = row.get("web", "")
            # Preskoči CW URL-ove iz collect faze
            if web_url and "companywall.hr" in web_url:
                web_url = ""

            if web_url:
                try:
                    scan_result = scan_website(session, web_url)
                    if scan_result["is_shop"]:
                        log.info("[%s] REJECTED (web-scan): %s", lead_id, scan_result["reason"])
                        updates = {"status": "rejected", "opis": f"Odbačeno: {scan_result['reason']}"}
                        client.update_row(lead_id, updates)
                        processed += 1
                        continue
                except Exception as e:
                    log.warning("[%s] web-scan greška: %s", lead_id, e)

            # --- Korak 2: CompanyWall scrape ---
            try:
                cw_data = scrape_detail(row, limiter)
            except BatchPausedException as e:
                log.error("Batch pauza: %s — zaustavljam run", e)
                break

            processed += 1

            if not cw_data.get("_cw_found"):
                # --- Fallback: provjera.hr ---
                log.info("[%s] CW nije našao '%s' — pokušavam provjera.hr", lead_id, naziv)
                try:
                    provjera = check_company(session, naziv)
                    if provjera.get("found"):
                        if provjera.get("oib"):
                            updates["oib"] = provjera["oib"]
                        if not provjera.get("active", True):
                            updates["status"] = "rejected"
                            updates["opis"] = "Neaktivna firma (provjera.hr)"
                        else:
                            updates["status"] = "manual_review"
                    else:
                        updates["status"] = "manual_review"
                except Exception as e:
                    log.warning("[%s] provjera.hr greška: %s", lead_id, e)
                    updates["status"] = "manual_review"

                client.update_row(lead_id, updates)
                continue

            # --- Firma nađena na CW ---
            # Provjeri blokadu
            if cw_data.get("cw_blocked"):
                log.info("[%s] REJECTED (u blokadi): %s", lead_id, naziv)
                updates = {
                    "oib": cw_data.get("oib", ""),
                    "status": "rejected",
                    "opis": "Odbačeno: firma u blokadi",
                }
                client.update_row(lead_id, updates)
                continue

            # Upiši CW podatke
            updates.update({
                "oib": cw_data.get("oib", ""),
                "vlasnik": cw_data.get("vlasnik", ""),
                "telefon": cw_data.get("telefon", ""),
                "broj_zaposlenih": cw_data.get("broj_zaposlenih", ""),
                "godina_osnutka": cw_data.get("godina_osnutka", ""),
            })

            # Status: ima li mobitel?
            if cw_data.get("_has_mobile") or cw_data.get("telefon"):
                updates["status"] = "verified"
            else:
                # Nema mobitela — manual_review (ne odbacuje se)
                log.info("[%s] manual_review (nema mobitela): %s", lead_id, naziv)
                updates["status"] = "manual_review"

            # --- Korak 3: Opis (Claude API) — samo za verified ---
            if updates.get("status") == "verified" and web_url:
                try:
                    web_text = extract_homepage_text(session, web_url)
                    if web_text:
                        opis = generate_opis(naziv, web_text)
                        if opis:
                            updates["opis"] = opis
                except Exception as e:
                    log.warning("[%s] opis greška: %s", lead_id, e)

            client.update_row(lead_id, updates)
            log.info("[%s] → status: %s", lead_id, updates.get("status"))

    log.info("=== Verify završen: %d obrađenih redova ===", processed)


if __name__ == "__main__":
    run()
