"""
Jednokratni helper — ispravlja link kolonu u Sheetu.
Zamjenjuje stari Pages base URL s novim u svim postojećim redovima.

Pokretanje via workflow: .github/workflows/fix-links.yml
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.lib.sheets import SheetsClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("fix_links")

OLD_BASE = "https://markosrnec5.github.io/lead-scraper-august/"
NEW_BASE = "https://marko-h4cklabs.github.io/lead-pipeline-bot/"


def run():
    log.info("=== Fix Links start ===")
    log.info("Zamjena: %s → %s", OLD_BASE, NEW_BASE)
    client = SheetsClient()

    all_rows = client.read_all()
    log.info("Ukupno redova: %d", len(all_rows))

    updates = []
    for row in all_rows:
        link = row.get("link", "")
        if OLD_BASE in link:
            new_link = link.replace(OLD_BASE, NEW_BASE, 1)
            updates.append({"lead_id": row["lead_id"], "link": new_link})

    log.info("Redova za ispravak: %d", len(updates))

    if not updates:
        log.info("Nema pogrešnih linkova — završeno.")
        return

    client.batch_update(updates)
    log.info("=== Fix Links završen: %d linkova ispravljeno ===", len(updates))


if __name__ == "__main__":
    run()
