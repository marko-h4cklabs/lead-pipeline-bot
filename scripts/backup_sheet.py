"""
Backup script — kopira sve podatke iz Leads taba u novi tab s vremenskim žigom.
Backup tab ostaje u istom Spreadsheetu: Leads_backup_YYYYMMDD_HHMMSS

Pokretanje (lokalno s ADC-om):
  GOOGLE_SHEETS_ID=... python -m scripts.backup_sheet

Workflow: .github/workflows/backup-sheet.yml (workflow_dispatch)
"""
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.lib.sheets import _get_service, _sheet_id, SHEET_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("backup_sheet")


def run():
    log.info("=== Backup Sheeta ===")
    svc = _get_service()
    sid = _sheet_id()

    # Dohvati sve podatke iz Leads taba (RAW — čuva apostrofne prefikse)
    result = svc.spreadsheets().values().get(
        spreadsheetId=sid,
        range=f"'{SHEET_NAME}'!A1:ZZ10000",
        valueRenderOption="FORMULA",  # čuva '09... kao tekst, ne konvertira u broj
    ).execute()
    values = result.get("values", [])

    if not values:
        log.warning("Leads tab je prazan — nema što backupati.")
        return

    log.info("Dohvaćeno %d redova (header + podaci)", len(values))

    # Kreiraj novi tab s vremenskim žigom
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"Leads_backup_{timestamp}"

    add_req = {
        "requests": [{
            "addSheet": {
                "properties": {
                    "title": backup_name,
                    "gridProperties": {
                        "rowCount": max(len(values) + 20, 100),
                        "columnCount": 26,
                    },
                }
            }
        }]
    }
    resp = svc.spreadsheets().batchUpdate(spreadsheetId=sid, body=add_req).execute()
    new_sheet_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    log.info("Novi backup tab '%s' kreiran (sheetId=%d)", backup_name, new_sheet_id)

    # Upiši podatke — USER_ENTERED čuva tekst kao tekst (ne reinterpretira formule)
    svc.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"'{backup_name}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()

    log.info("Backup završen: %d redova → tab '%s'", len(values), backup_name)
    log.info("=== Sada možeš pokrenuti reset-sheet ===")


if __name__ == "__main__":
    run()
