"""
Dio 2 — Verifikacija
Pokreni: python -m scripts.verify.main

Ulaz: redovi sa status='new'
Izlaz: status → 'verified' / 'manual_review' / 'rejected'

Kriteriji odbacivanja:
  - Web-scan: detektiran webshop
  - CW blokada
  - CW prihodi eksplicitno 0 EUR
Batch update na kraju runa (1 read_all + 1 batchUpdate, ne 1 po redu).
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

    all_rows = client.read_all()
    log.info("Ukupno redova u Sheetu: %d", len(all_rows))
    if all_rows:
        statuses = {}
        for r in all_rows:
            s = r.get("status", "(prazno)")
            statuses[s] = statuses.get(s, 0) + 1
        log.info("Distribucija statusa: %s", statuses)
        sample = all_rows[0]
        log.info("Primjer [row %d]: lead_id=%r naziv=%r status=%r web=%r",
                 sample.get("_row"), sample.get("lead_id"), sample.get("naziv_firme"),
                 sample.get("status"), sample.get("web", "")[:60])

    new_rows = [r for r in all_rows if r.get("status") == "new"]
    log.info("Pronađeno %d 'new' redova za verifikaciju", len(new_rows))

    if not new_rows:
        log.info("Nema novih redova — završeno.")
        return

    limiter = RateLimiter(max_per_run=MAX_PER_RUN)
    all_updates: list[dict] = []
    processed = 0

    with rate_limited_session(max_per_run=MAX_PER_RUN * 3) as (session, _):
        for row in new_rows:
            if processed >= MAX_PER_RUN:
                log.info("Dosegnut limit od %d po runu — stajemo.", MAX_PER_RUN)
                break

            lead_id = row.get("lead_id", "")
            naziv = row.get("naziv_firme", "")
            log.info("[%s] Verificiram: %s", lead_id, naziv)

            updates = {"lead_id": lead_id}

            # --- Korak 1: Web-scan (samo ako ima pravi web URL, ne CW) ---
            web_url = row.get("web", "")
            if web_url and "companywall.hr" in web_url:
                web_url = ""

            if web_url:
                try:
                    scan_result = scan_website(session, web_url)
                    if scan_result["is_shop"]:
                        log.info("[%s] REJECTED (web-scan): %s", lead_id, scan_result["reason"])
                        updates["status"] = "rejected"
                        updates["opis"] = f"Odbačeno: {scan_result['reason']}"
                        all_updates.append(updates)
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

                all_updates.append(updates)
                continue

            # --- Firma nađena na CW ---

            # Provjera 1: blokada
            if cw_data.get("cw_blocked"):
                log.info("[%s] REJECTED (u blokadi): %s", lead_id, naziv)
                updates.update({
                    "oib": cw_data.get("oib", ""),
                    "status": "rejected",
                    "opis": "Odbačeno: firma u blokadi",
                })
                all_updates.append(updates)
                continue

            # Provjera 2: prihodi == 0 EUR (eksplicitno)
            prihod = cw_data.get("prihod")
            if prihod is not None and prihod == 0:
                log.info("[%s] REJECTED (prihodi 0 EUR): %s", lead_id, naziv)
                updates.update({
                    "oib": cw_data.get("oib", ""),
                    "status": "rejected",
                    "opis": "Odbačeno: prihodi 0 EUR",
                })
                all_updates.append(updates)
                continue

            # Upiši CW podatke
            updates.update({
                "oib": cw_data.get("oib", ""),
                "vlasnik": cw_data.get("vlasnik", ""),
                "telefon": cw_data.get("telefon", ""),
                "broj_zaposlenih": cw_data.get("broj_zaposlenih", ""),
                "godina_osnutka": cw_data.get("godina_osnutka", ""),
            })

            # Status: verified ako ima kontakt, inače manual_review
            if cw_data.get("_has_mobile") or cw_data.get("telefon"):
                updates["status"] = "verified"
            else:
                log.info("[%s] manual_review (nema mobitela): %s", lead_id, naziv)
                updates["status"] = "manual_review"

            # --- Korak 3: Opis (Claude API) — samo za verified s web-om ---
            if updates.get("status") == "verified" and web_url:
                try:
                    web_text = extract_homepage_text(session, web_url)
                    if web_text:
                        opis = generate_opis(naziv, web_text)
                        if opis:
                            updates["opis"] = opis
                except Exception as e:
                    log.warning("[%s] opis greška: %s", lead_id, e)

            log.info("[%s] → status: %s", lead_id, updates.get("status"))
            all_updates.append(updates)

    # --- Batch write svih promjena ---
    if all_updates:
        log.info("Batch update: %d redova...", len(all_updates))
        try:
            client.batch_update(all_updates)
        except Exception as e:
            log.error("Batch update greška: %s", e)

    log.info("=== Verify završen: %d obrađenih redova ===", processed)


if __name__ == "__main__":
    run()
