"""
Faza 4 — Retroaktivna provjera OIB/naziv kolizija u postojećem Sheetu.

Grupira sve redove po OIB-u i provjerava sličnost naziva unutar svake grupe.
Isti OIB + različiti nazivi = logička nemoguucnost (OIB je jedinstven) = greška podataka.

Pokretanje (lokalno s ADC-om):
  GOOGLE_SHEETS_ID=... python -m scripts.audit_oib_collisions

  # Samo izvještaj (bez izmjena):
  GOOGLE_SHEETS_ID=... python -m scripts.audit_oib_collisions

  # Automatska korekcija (prebacuje kolidirujuće redove u manual_review):
  GOOGLE_SHEETS_ID=... python -m scripts.audit_oib_collisions --fix
"""
import argparse
import logging
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.lib.sheets import SheetsClient
from scripts.verify.companywall_detail import name_similarity, NAME_SIMILARITY_THRESHOLD

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("audit_oib")


def run(fix: bool = False) -> None:
    log.info("=== Retroaktivna OIB kolizijska provjera ===")
    client = SheetsClient()
    all_rows = client.read_all()
    log.info("Ukupno redova: %d", len(all_rows))

    # Grupiraj po OIB-u (ignoriraj prazne / kratke OIB-ove)
    oib_groups: dict[str, list] = defaultdict(list)
    for row in all_rows:
        oib = row.get("oib", "").strip().lstrip("'")
        if oib and len(oib) >= 8:
            oib_groups[oib].append(row)

    collisions = []
    for oib, group in oib_groups.items():
        if len(group) < 2:
            continue
        names = [r.get("naziv_firme", "") for r in group]
        # Najgori par (najmanji similarity)
        worst_sim = min(
            name_similarity(names[i], names[j])
            for i in range(len(names))
            for j in range(i + 1, len(names))
        )
        if worst_sim < NAME_SIMILARITY_THRESHOLD:
            collisions.append((oib, group, worst_sim))

    if not collisions:
        log.info("✓ Nema OIB/naziv kolizija u Sheetu. Svi OIB-ovi su konzistentni.")
        return

    print(f"\n{'='*60}")
    print(f"PRONAĐENO {len(collisions)} OIB KOLIZIJA")
    print(f"{'='*60}\n")

    total_affected = 0
    for oib, group, worst_sim in collisions:
        print(f"OIB: {oib}  (sličnost={worst_sim:.0%})")
        for row in group:
            status = row.get("status", "?")
            lead_id = row.get("lead_id", "?")
            naziv = row.get("naziv_firme", "?")
            grad = row.get("grad", "")
            print(f"  row {row['_row']:4d}  [{lead_id}]  {naziv:<40}  {grad:<20}  status={status}")
            total_affected += 1
        print()

    print(f"Ukupno zahvaćenih redova: {total_affected}")
    print(f"Redovi s 'verified'/'qualified' statusom koji su možda netočni:")
    risky = [
        (oib, row)
        for oib, group, _ in collisions
        for row in group
        if row.get("status") in ("verified", "qualified", "new")
    ]
    for oib, row in risky:
        print(f"  !! OIB {oib}  [{row['lead_id']}]  {row.get('naziv_firme','')}  status={row.get('status','')}")

    if not fix:
        print(f"\nDRY RUN — nema izmjena. Pokreni s --fix za automatsku korekciju.")
        return

    print(f"\nPokrećem korekciju — {total_affected} redova → manual_review ...")
    fix_updates = []
    for oib, group, _ in collisions:
        names_str = " | ".join(r.get("naziv_firme", "?") for r in group)
        for row in group:
            if row.get("status") == "rejected":
                continue  # ne.diraj rejected redove
            fix_updates.append({
                "lead_id": row["lead_id"],
                "status": "manual_review",
                "opis": (
                    f"Retroaktivna OIB kolizija (audit): OIB {oib} "
                    f"pronađen kod {len(group)} različitih firmi ({names_str}) — ručna provjera"
                ),
            })

    if fix_updates:
        try:
            client.batch_update(fix_updates)
            log.info("Korekcija završena: %d redova prebačeno u manual_review", len(fix_updates))
        except Exception as e:
            log.error("batch_update greška: %s", e)
    else:
        log.info("Nema redova za korekciju (svi su već rejected).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retroaktivna OIB kolizijska provjera")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Prebaci kolidirujuće redove u manual_review (default: samo izvještaj)",
    )
    args = parser.parse_args()
    run(fix=args.fix)
