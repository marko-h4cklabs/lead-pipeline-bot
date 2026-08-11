"""
Faza 4 — Retroaktivna provjera OIB kolizija u postojećem Sheetu.

Svaki OIB s više od jednog lead_id-a je potencijalni problem:
- KOLIZIJA: različite firme dijele OIB → greška podataka (OIB je jedinstven u HR)
- LEGITIMNI DUPLIKAT: ista firma, dva retka → _dedup_by_oib je ovo trebao srediti

Razlikovanje: ako je jedna od duplikatnih firma u opis-u "Duplikat OIB...",
sustav je to već obradio. Sve ostalo tretiramo kao potencijalnu koliziju i
prikazujemo za ručni pregled. Sličnost naziva je samo informacija, nije filter.

Pokretanje:
  GOOGLE_SHEETS_ID=... python -m scripts.audit_oib_collisions
  GOOGLE_SHEETS_ID=... python -m scripts.audit_oib_collisions --fix
"""
import argparse
import logging
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.lib.sheets import SheetsClient
from scripts.verify.companywall_detail import name_similarity

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("audit_oib")

# lead_id-ovi koje uvijek ispisujemo za dijagnozu
_WATCH_IDS = {"94c4a9ab", "5204cf6c", "f314daed"}


def _oib_key(raw) -> str:
    """Normalizira OIB na same znamenke — neovisno o formatu pohrane."""
    return re.sub(r"\D", "", str(raw))


def _is_dedup_row(row: dict) -> bool:
    """True ako je red eksplicitno označen kao legitimni duplikat od _dedup_by_oib."""
    opis = row.get("opis", "")
    return "Duplikat OIB" in opis


def run(fix: bool = False) -> None:
    log.info("=== Retroaktivna OIB kolizijska provjera ===")
    client = SheetsClient()
    all_rows = client.read_all()
    log.info("Ukupno redova: %d", len(all_rows))

    # Raspodjela statusa — potvrda da su svi statusi uključeni
    status_dist: dict[str, int] = {}
    for row in all_rows:
        s = row.get("status", "(prazno)")
        status_dist[s] = status_dist.get(s, 0) + 1
    log.info("Raspodjela statusa: %s", status_dist)

    # Grupiraj po OIB-u — samo znamenke, svi statusi
    oib_groups: dict[str, list] = defaultdict(list)
    rows_no_oib = 0
    for row in all_rows:
        oib = _oib_key(row.get("oib", ""))
        if len(oib) >= 8:
            oib_groups[oib].append(row)
        else:
            rows_no_oib += 1
    log.info(
        "OIB statistika: %d jedinstvenih OIB-ova, %d/%d redova s OIB-om, %d bez",
        len(oib_groups), len(all_rows) - rows_no_oib, len(all_rows), rows_no_oib,
    )

    # Dijagnostički WATCH log
    for row in all_rows:
        if row.get("lead_id", "") in _WATCH_IDS:
            oib = _oib_key(row.get("oib", ""))
            log.info(
                "WATCH [%s] '%s' status=%s oib=%r group_size=%d dedup_row=%s",
                row.get("lead_id"), row.get("naziv_firme"), row.get("status"),
                oib, len(oib_groups.get(oib, [])), _is_dedup_row(row),
            )

    # Pronađi sve multi-row OIB grupe — BEZ filtriranja po sličnosti naziva.
    # Sličnost je samo anotacija za ljudskog pregledatelja, ne kriterij isključenja.
    # BERIKO/MARKODOM su primjer zašto similarity-filter ne radi: dijele "montažni objekti"
    # u nazivu pa izgledaju slično čak i iako su različite firme.
    multi_groups = []
    for oib, group in oib_groups.items():
        if len(group) < 2:
            continue
        names = [r.get("naziv_firme", "") for r in group]
        worst_sim = min(
            name_similarity(names[i], names[j])
            for i in range(len(names))
            for j in range(i + 1, len(names))
        )
        # Razlikovanje: je li ovo legitiman dedup ili potencijalna kolizija?
        # Heuristika: ako je _dedup_by_oib već obradio grupu, barem jedan red
        # ima "Duplikat OIB" u opis-u.
        already_deduped = any(_is_dedup_row(r) for r in group)
        multi_groups.append((oib, group, worst_sim, already_deduped))

    if not multi_groups:
        log.info("✓ Nema višestrukih OIB-ova u Sheetu.")
        return

    # Razdvoji na kolizije i legitimne duplikate
    collisions = [(oib, g, sim) for oib, g, sim, deduped in multi_groups if not deduped]
    legit_dups = [(oib, g, sim) for oib, g, sim, deduped in multi_groups if deduped]

    # --- Ispis kolizija ---
    if collisions:
        print(f"\n{'='*60}")
        print(f"POTENCIJALNE KOLIZIJE: {len(collisions)} OIB-ova ({sum(len(g) for _,g,_ in collisions)} redova)")
        print(f"(OIB s više lead_id-ova, nije označeno kao legitimni duplikat)")
        print(f"{'='*60}\n")
        for oib, group, worst_sim in collisions:
            tag = "KOLIZIJA" if worst_sim < 0.55 else "SUMNJIVO (slični nazivi, ali različiti lead_id)"
            print(f"OIB {oib}  sličnost={worst_sim:.0%}  [{tag}]")
            for row in group:
                opis_snippet = (row.get("opis", "") or "")[:60]
                print(
                    f"  row {row['_row']:4d}  [{row.get('lead_id','?')}]"
                    f"  {row.get('naziv_firme','?'):<40}"
                    f"  {row.get('grad',''):<18}"
                    f"  status={row.get('status','?')}"
                    f"  opis={opis_snippet!r}"
                )
            print()
    else:
        print("\n✓ Nema neobradenih potencijalnih kolizija.")

    # --- Ispis legitimnih duplikata (informativno) ---
    if legit_dups:
        print(f"LEGITIMNI DUPLIKATI (već obrađeni od _dedup_by_oib): {len(legit_dups)} OIB-ova")
        for oib, group, worst_sim in legit_dups:
            print(f"  OIB {oib}  sličnost={worst_sim:.0%}  ({len(group)} redova — OK)")

    total_collision_rows = sum(len(g) for _, g, _ in collisions)
    print(f"\nSažetak: {len(collisions)} potencijalna kolizija ({total_collision_rows} redova), "
          f"{len(legit_dups)} legitimnih duplikata.")

    if not fix:
        print("\nDRY RUN — nema izmjena. Pokreni s --fix za automatsku korekciju kolizija.")
        return

    if not collisions:
        log.info("Nema kolizija za ispravljanje.")
        return

    # --fix: samo potencijalne kolizije → manual_review (ne diraj legitimne duplikate)
    print(f"\nPokrećem korekciju — {total_collision_rows} redova → manual_review ...")
    fix_updates = []
    for oib, group, _ in collisions:
        names_str = " | ".join(r.get("naziv_firme", "?") for r in group)
        for row in group:
            orig_status = row.get("status", "?")
            fix_updates.append({
                "lead_id": row["lead_id"],
                "status": "manual_review",
                "opis": (
                    f"Retroaktivna OIB kolizija (audit, bio: {orig_status}): OIB {oib} "
                    f"kod {len(group)} različitih firmi ({names_str}) — ručna provjera"
                ),
            })

    try:
        client.batch_update(fix_updates)
        log.info("Korekcija završena: %d redova → manual_review", len(fix_updates))
    except Exception as e:
        log.error("batch_update greška: %s", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retroaktivna OIB kolizijska provjera")
    parser.add_argument("--fix", action="store_true",
                        help="Prebaci kolizije u manual_review (default: samo izvještaj)")
    args = parser.parse_args()
    run(fix=args.fix)
