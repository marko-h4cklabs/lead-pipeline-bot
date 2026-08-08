"""
Jednokratni helper — vraća qualified/filtered_out redove na 'verified'.
Koristiti kad je Filter pokrenut s pogrešnim parametrima i treba re-run.

Pokretanje via workflow: .github/workflows/revert-filter.yml
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.lib.sheets import SheetsClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("revert_filter")

REVERT_FROM = {"qualified", "filtered_out"}


def run():
    log.info("=== Revert Filter start ===")
    client = SheetsClient()

    all_rows = client.read_all()
    to_revert = [r for r in all_rows if r.get("status") in REVERT_FROM]
    log.info("Pronađeno %d redova za revert (%s → verified)", len(to_revert), REVERT_FROM)

    if not to_revert:
        log.info("Nema redova za revert — završeno.")
        return

    updates = [{"lead_id": r["lead_id"], "status": "verified"} for r in to_revert]
    client.batch_update(updates)
    log.info("=== Revert završen: %d redova vraćeno na 'verified' ===", len(to_revert))


if __name__ == "__main__":
    run()
