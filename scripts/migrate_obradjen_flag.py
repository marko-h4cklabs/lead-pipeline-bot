"""
Jednokratna migracija — dodaje 'obradjen_flag' u header R1 i popunjava R2:R...

  DA → redovi čiji je status 'audio_ready' ili 'sent' (već obrađeni)
  NE → svi ostali

Piše isključivo u kolonu R — kolone A-Q se ne diraju.
Workflow: .github/workflows/migrate-obradjen-flag.yml
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.lib.sheets import _get_service, _sheet_id, SHEET_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("migrate_obradjen_flag")

DONE_STATUSES = {"audio_ready", "sent"}
STATUS_COL_IDX = 12  # M kolona (0-indexed) — mora odgovarati COL["status"] u sheets.py


def run():
    log.info("=== Migracija obradjen_flag (kolona R) ===")
    svc = _get_service()
    sid = _sheet_id()

    # Korak 1: upiši header 'obradjen_flag' u R1
    svc.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"'{SHEET_NAME}'!R1",
        valueInputOption="RAW",
        body={"values": [["obradjen_flag"]]},
    ).execute()
    log.info("Header 'obradjen_flag' upisan u R1")

    # Korak 2: dohvati status kolonu (M = indeks 12) za sve podatkovne redove
    result = svc.spreadsheets().values().get(
        spreadsheetId=sid,
        range=f"'{SHEET_NAME}'!A:M",
    ).execute()
    rows = result.get("values", [])

    if len(rows) < 2:
        log.info("Nema podatkovnih redova — završeno.")
        return

    data_rows = rows[1:]  # preskoči header
    log.info("Pronađeno %d podatkovnih redova", len(data_rows))

    # Korak 3: izračunaj zastavice
    flags = []
    da_count = ne_count = 0
    for r in data_rows:
        status = r[STATUS_COL_IDX] if len(r) > STATUS_COL_IDX else ""
        flag = "DA" if status in DONE_STATUSES else "NE"
        flags.append([flag])
        if flag == "DA":
            da_count += 1
        else:
            ne_count += 1

    # Korak 4: upiši zastavice u R2:R{n+1}
    n = len(flags)
    write_range = f"'{SHEET_NAME}'!R2:R{n + 1}"
    svc.spreadsheets().values().update(
        spreadsheetId=sid,
        range=write_range,
        valueInputOption="RAW",
        body={"values": flags},
    ).execute()
    log.info(
        "Upisano %d zastavica u %s: DA=%d, NE=%d",
        n, write_range, da_count, ne_count,
    )
    log.info("=== Migracija završena — provjeri R kolonu u Sheetu ===")


if __name__ == "__main__":
    run()
