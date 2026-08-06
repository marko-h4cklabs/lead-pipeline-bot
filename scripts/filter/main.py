"""
Dio 3 — Filtering
Pokreni: python -m scripts.filter.main

Ulaz: redovi sa status='verified'
Kriterij: min. 5 zaposlenih, min. 2 godine poslovanja
Izlaz: status → 'qualified' ili 'filtered_out'
"""
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from scripts.lib.sheets import SheetsClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("filter")

MIN_EMPLOYEES = int(os.environ.get("MIN_EMPLOYEES", "5"))
MIN_YEARS = int(os.environ.get("MIN_YEARS", "2"))
CURRENT_YEAR = datetime.now().year


def _parse_employees(val: str) -> int | None:
    """Parsira broj zaposlenih iz stringa (može biti "7" ili "7,00")."""
    if not val:
        return None
    try:
        return int(float(val.replace(",", ".")))
    except (ValueError, AttributeError):
        return None


def _years_operating(godina_str: str) -> int | None:
    """Koliko godina firma posluje (od osnivanja do danas)."""
    if not godina_str:
        return None
    try:
        year = int(godina_str.strip())
        return CURRENT_YEAR - year
    except (ValueError, AttributeError):
        return None


def evaluate(row: dict) -> tuple[str, str]:
    """
    Evaluira red i vraća (novi_status, razlog).
    """
    employees = _parse_employees(row.get("broj_zaposlenih", ""))
    years = _years_operating(row.get("godina_osnutka", ""))

    reasons = []

    if employees is None:
        # Nepoznat broj zaposlenih — ne odbacujemo, preskačemo filtriranje
        return "qualified", "broj_zaposlenih_nepoznat"

    if employees < MIN_EMPLOYEES:
        reasons.append(f"premalo_zaposlenih:{employees}<{MIN_EMPLOYEES}")

    if years is not None and years < MIN_YEARS:
        reasons.append(f"premladа_firma:{years}g<{MIN_YEARS}g")

    if reasons:
        return "filtered_out", "|".join(reasons)

    return "qualified", f"zaposleni:{employees},godine:{years or '?'}"


def run():
    log.info("=== Filter start ===")
    client = SheetsClient()

    verified_rows = client.read_by_status("verified")
    log.info("Pronađeno %d 'verified' redova za filtriranje", len(verified_rows))

    if not verified_rows:
        log.info("Nema verified redova — završeno.")
        return

    updates = []
    qualified = 0
    filtered = 0

    for row in verified_rows:
        new_status, reason = evaluate(row)
        log.info(
            "[%s] %s → %s (%s)",
            row.get("lead_id", ""), row.get("naziv_firme", ""), new_status, reason
        )
        updates.append({"lead_id": row["lead_id"], "status": new_status})

        if new_status == "qualified":
            qualified += 1
        else:
            filtered += 1

    # Batch update
    client.batch_update(updates)

    log.info(
        "=== Filter završen: %d qualified, %d filtered_out ===",
        qualified, filtered
    )


if __name__ == "__main__":
    run()
