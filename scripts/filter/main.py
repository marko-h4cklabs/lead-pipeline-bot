"""
Dio 3 — Filtering
Pokreni: python -m scripts.filter.main

Ulaz: redovi sa status='verified'
Kriterij: min. zaposlenih / min. godina (podesivo env varijablama)
Prazna vrijednost = preskači tu provjeru u potpunosti (bez odbacivanja).
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

# Prazna vrijednost → None → provjera se preskače u potpunosti
_min_emp_raw = os.environ.get("MIN_EMPLOYEES", "5").strip()
MIN_EMPLOYEES: int | None = int(_min_emp_raw) if _min_emp_raw else None

_min_years_raw = os.environ.get("MIN_YEARS", "3").strip()
MIN_YEARS: int | None = int(_min_years_raw) if _min_years_raw else None

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
    Ako je MIN_EMPLOYEES/MIN_YEARS None, ta provjera se preskače u potpunosti.
    """
    employees = _parse_employees(row.get("broj_zaposlenih", ""))
    years = _years_operating(row.get("godina_osnutka", ""))

    reasons = []

    if MIN_EMPLOYEES is not None:
        # Provjera zaposlenih aktiva — nepoznat broj ide u manual_review
        if employees is None:
            return "manual_review", "broj_zaposlenih_nepoznat"
        if employees < MIN_EMPLOYEES:
            reasons.append(f"premalo_zaposlenih:{employees}<{MIN_EMPLOYEES}")

    if MIN_YEARS is not None and years is not None and years < MIN_YEARS:
        reasons.append(f"premladа_firma:{years}g<{MIN_YEARS}g")

    if reasons:
        return "filtered_out", "|".join(reasons)

    emp_label = str(employees) if employees is not None else "?"
    return "qualified", f"zaposleni:{emp_label},godine:{years or '?'}"


def run():
    log.info("=== Filter start — MIN_EMPLOYEES=%s, MIN_YEARS=%s ===",
             MIN_EMPLOYEES if MIN_EMPLOYEES is not None else "bez uvjeta",
             MIN_YEARS if MIN_YEARS is not None else "bez uvjeta")
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
