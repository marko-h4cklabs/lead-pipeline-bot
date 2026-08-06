"""
Jednokratni reset script — briše sve podatke iz Leads taba i postavlja header red.

Pokretanje (lokalno s ADC-om):
  GOOGLE_SHEETS_ID=... python -m scripts.reset_sheet

Workflow: .github/workflows/reset-sheet.yml (workflow_dispatch)
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.lib.sheets import _get_service, _sheet_id, COLUMNS, SHEET_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("reset_sheet")


def run():
    log.info("=== Reset Sheeta ===")
    svc = _get_service()
    sid = _sheet_id()

    # Provjeri da tab 'Leads' postoji i dohvati sheetId
    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    sheet_id = None
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == SHEET_NAME:
            sheet_id = s["properties"]["sheetId"]
            row_count = s["properties"]["gridProperties"]["rowCount"]
            col_count = s["properties"]["gridProperties"]["columnCount"]
            log.info("Tab '%s' pronađen: sheetId=%d, %d×%d", SHEET_NAME, sheet_id, row_count, col_count)
            break

    if sheet_id is None:
        log.error("Tab '%s' nije pronađen! Provjeri Sheet ID i naziv taba.", SHEET_NAME)
        sys.exit(1)

    # Korak 1: obriši SVE podatke iz taba (clear, ne delete — čuva strukturu taba)
    clear_range = f"'{SHEET_NAME}'!A1:ZZ10000"
    svc.spreadsheets().values().clear(spreadsheetId=sid, range=clear_range, body={}).execute()
    log.info("Obrisani svi podaci iz '%s'", SHEET_NAME)

    # Korak 2: upiši header red u A1:Q1
    header = [COLUMNS]
    svc.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"'{SHEET_NAME}'!A1:Q1",
        valueInputOption="RAW",
        body={"values": header},
    ).execute()
    log.info("Header red upisan: %s", COLUMNS)

    # Korak 3: smrzni header red (freeze row 1) da ostane vidljiv pri skrolanju
    freeze_req = {
        "requests": [{
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        }]
    }
    svc.spreadsheets().batchUpdate(spreadsheetId=sid, body=freeze_req).execute()
    log.info("Header red zamrznut (frozen row 1)")

    log.info("=== Reset završen — Sheet spreman za novi collect run ===")


if __name__ == "__main__":
    run()
