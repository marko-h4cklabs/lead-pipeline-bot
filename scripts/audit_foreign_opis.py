"""
Retroaktivna provjera geografske kontaminacije u postojećem Sheetu.

Traži redove gdje opis ili vlasnik polje sadrži naziv strane države,
što je siguran znak da je verify faza upisala podatke pogrešne (inozemne) firme.

Pokretanje:
  GOOGLE_SHEETS_ID=... python -m scripts.audit_foreign_opis
  GOOGLE_SHEETS_ID=... python -m scripts.audit_foreign_opis --fix
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.lib.sheets import SheetsClient
from scripts.verify.main import _has_foreign_country

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("audit_foreign_opis")


def run(fix: bool = False) -> None:
    log.info("=== Retroaktivna provjera geografske kontaminacije ===")
    client = SheetsClient()
    all_rows = client.read_all()
    log.info("Ukupno redova: %d", len(all_rows))

    hits = []
    for row in all_rows:
        for field in ("opis", "vlasnik"):
            val = row.get(field, "") or ""
            marker = _has_foreign_country(val)
            if marker:
                hits.append((row, field, marker, val))
                break  # jedan hit po redu je dovoljan

    if not hits:
        log.info("✓ Nema redova s geografskom kontaminacijom.")
        return

    print(f"\n{'='*65}")
    print(f"GEOGRAFSKA KONTAMINACIJA: {len(hits)} redova")
    print(f"{'='*65}\n")
    for row, field, marker, val in hits:
        snippet = val[:80].replace("\n", " ")
        print(
            f"  row {row['_row']:4d}  [{row.get('lead_id','?')}]"
            f"  {row.get('naziv_firme','?'):<40}"
            f"  {row.get('grad',''):<18}"
            f"  status={row.get('status','?')}"
        )
        print(f"           {field}: …{snippet!r}  [marker: '{marker}']\n")

    print(f"Sažetak: {len(hits)} kontaminiranih redova.")

    if not fix:
        print("\nDRY RUN — nema izmjena. Pokreni s --fix za automatsku korekciju.")
        return

    print(f"\nPokrećem korekciju — {len(hits)} redova → manual_review ...")
    fix_updates = []
    for row, field, marker, _ in hits:
        orig_status = row.get("status", "?")
        fix_updates.append({
            "lead_id": row["lead_id"],
            "status": "manual_review",
            "opis": (
                f"Retroaktivna geografska provjera (bio: {orig_status}): "
                f"polje '{field}' sadrži '{marker}' — ručna provjera"
            ),
            "vlasnik": "",
            "oib": "",
            "telefon": "",
        })

    try:
        client.batch_update(fix_updates)
        log.info("Korekcija završena: %d redova → manual_review", len(fix_updates))
    except Exception as e:
        log.error("batch_update greška: %s", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retroaktivna provjera geografske kontaminacije")
    parser.add_argument("--fix", action="store_true",
                        help="Prebaci kontaminirane redove u manual_review (default: samo izvještaj)")
    args = parser.parse_args()
    run(fix=args.fix)
