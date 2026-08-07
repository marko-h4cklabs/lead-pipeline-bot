"""
Google Sheets wrapper — čita/piše po lead_id, ne po row indexu.

Autentifikacija: Application Default Credentials (ADC).
  - GitHub Actions: google-github-actions/auth@v2 s Workload Identity Federation
    automatski postavlja ADC; nema JSON ključa.
  - Lokalno: `gcloud auth application-default login --scopes=...spreadsheets`
    ILI postavi GOOGLE_APPLICATION_CREDENTIALS na putanju SA JSON fajla.
Sheet mora imati header u redu 1; lead_id kolona mora biti prisutna.
"""
import os
import logging
from datetime import datetime
from typing import Optional

from google.auth import default as google_auth_default
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Redoslijed kolona mora odgovarati Sheet shemi (od A do Q)
COLUMNS = [
    "lead_id",
    "naziv_firme",
    "oib",
    "vlasnik",
    "telefon",
    "web",
    "grad",
    "zupanija",
    "opis",
    "broj_zaposlenih",
    "godina_osnutka",
    "izvor",
    "status",
    "claimed_by",
    "datum_dodan",
    "datum_poslano",
    "link",
]

COL_INDEX = {name: i for i, name in enumerate(COLUMNS)}
SHEET_NAME = "Leads"  # naziv tab-a u Sheetu

# Stupci gdje vodeće nule moraju biti sačuvane — Sheets s USER_ENTERED bi ih uklonio
_TEXT_COLUMNS = {"lead_id", "oib", "telefon"}


def _to_sheet_value(col: str, val) -> str:
    """
    Serializira vrijednost za upis u Sheet s USER_ENTERED mode-om.
    OIB i telefon dobivaju apostrofni prefix (') koji signalizira Sheetsu
    da pohrani vrijednost kao tekst (apostrof se ne prikazuje u ćeliji).
    """
    s = str(val) if val is not None else ""
    if col in _TEXT_COLUMNS and s:
        return "'" + s
    return s


def _get_service():
    # ADC: radi u GitHub Actions (WIF) i lokalno (gcloud ADC ili GOOGLE_APPLICATION_CREDENTIALS)
    creds, _ = google_auth_default(scopes=SCOPES)
    # Osvježi token ako je istekao (bitno za dugotrajne runove)
    if hasattr(creds, "refresh") and not creds.valid:
        creds.refresh(GoogleAuthRequest())
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _sheet_id():
    sid = os.environ.get("GOOGLE_SHEETS_ID", "")
    if not sid:
        raise ValueError("GOOGLE_SHEETS_ID nije postavljen u env")
    return sid


class SheetsClient:
    def __init__(self):
        self._svc = _get_service()
        self._sid = _sheet_id()
        self._sheet_ref = f"'{SHEET_NAME}'!A:Q"

    def _range(self, row_number: int) -> str:
        return f"'{SHEET_NAME}'!A{row_number}:Q{row_number}"

    def read_all(self) -> list[dict]:
        """Vraća sve redove kao list of dict (bez header reda)."""
        result = (
            self._svc.spreadsheets()
            .values()
            .get(spreadsheetId=self._sid, range=self._sheet_ref)
            .execute()
        )
        rows = result.get("values", [])
        if not rows:
            return []
        header = rows[0]
        out = []
        for i, row in enumerate(rows[1:], start=2):
            padded = row + [""] * (len(COLUMNS) - len(row))
            d = dict(zip(COLUMNS, padded))
            d["_row"] = i
            out.append(d)
        return out

    def read_by_status(self, status: str | list[str]) -> list[dict]:
        """Filtrira redove po statusu (jedan ili više)."""
        if isinstance(status, str):
            status = [status]
        return [r for r in self.read_all() if r.get("status") in status]

    def find_by_lead_id(self, lead_id: str) -> Optional[dict]:
        all_rows = self.read_all()
        for r in all_rows:
            if r.get("lead_id") == lead_id:
                return r
        return None

    def append_row(self, data: dict) -> None:
        """Dodaje jedan red na kraj Sheeta (koristi batch_append za višestruke redove)."""
        self.batch_append([data])

    def batch_append(self, rows: list[dict]) -> None:
        """
        Dodaje više redova u jednom API pozivu — koristi update() s eksplicitnim
        row brojevima da izbjegne INSERT_ROWS table-detection ambiguity.
        """
        if not rows:
            return
        # Nađi zadnji red s podacima (ne koristimo append/INSERT_ROWS jer je nepredvidiv)
        result = (
            self._svc.spreadsheets()
            .values()
            .get(spreadsheetId=self._sid, range=self._sheet_ref)
            .execute()
        )
        existing = result.get("values", [])
        next_row = len(existing) + 1  # 1-based; pišemo iza zadnjeg reda s podacima

        values = [[_to_sheet_value(col, data.get(col, "")) for col in COLUMNS] for data in rows]
        # Samo start cell — API sam izračuna kraj iz dimenzija matrice.
        # Eksplicitni end column (npr. :Q) uzrokuje pomak desno jer API
        # tretira end column kao sidro umjesto granice.
        write_range = f"'{SHEET_NAME}'!A{next_row}"
        log.info(
            "batch_append: pišem %d redova od %s | width=%d | first_row=%s",
            len(rows), write_range, len(values[0]) if values else 0, values[0] if values else [],
        )
        body = {"values": values}
        resp = self._svc.spreadsheets().values().update(
            spreadsheetId=self._sid,
            range=write_range,
            valueInputOption="USER_ENTERED",
            body=body,
        ).execute()
        log.info(
            "batch_append: API updatedRange=%s updatedRows=%s updatedColumns=%s",
            resp.get("updatedRange"), resp.get("updatedRows"), resp.get("updatedColumns"),
        )

    def update_row(self, lead_id: str, updates: dict) -> bool:
        """
        Ažurira samo navedene stupce za red s lead_id.
        Vraća True ako je red nađen i ažuriran.
        """
        row_data = self.find_by_lead_id(lead_id)
        if not row_data:
            log.warning("update_row: lead_id %s nije nađen", lead_id)
            return False
        row_num = row_data["_row"]
        for key, val in updates.items():
            if key in COL_INDEX:
                row_data[key] = val
        new_row = [_to_sheet_value(col, row_data.get(col, "")) for col in COLUMNS]
        body = {"values": [new_row]}
        self._svc.spreadsheets().values().update(
            spreadsheetId=self._sid,
            range=self._range(row_num),
            valueInputOption="USER_ENTERED",
            body=body,
        ).execute()
        log.debug("Updated lead_id=%s row=%d keys=%s", lead_id, row_num, list(updates.keys()))
        return True

    def batch_update(self, updates: list[dict]) -> None:
        """
        Batch write — popis dict-ova s lead_id + polja za update.
        Čita sve redove jednom, pa piše sve promjene u jednom batch pozivu.
        """
        all_rows = {r["lead_id"]: r for r in self.read_all()}
        data = []
        for upd in updates:
            lid = upd.get("lead_id")
            if lid not in all_rows:
                log.warning("batch_update: lead_id %s nije nađen, preskačem", lid)
                continue
            row_data = all_rows[lid]
            row_num = row_data["_row"]
            for key, val in upd.items():
                if key in COL_INDEX and key != "lead_id":
                    row_data[key] = val
            new_row = [_to_sheet_value(col, row_data.get(col, "")) for col in COLUMNS]
            data.append({
                "range": self._range(row_num),
                "values": [new_row],
            })
        if not data:
            return
        body = {"valueInputOption": "USER_ENTERED", "data": data}
        self._svc.spreadsheets().values().batchUpdate(
            spreadsheetId=self._sid, body=body
        ).execute()
        log.info("batch_update: ažurirano %d redova", len(data))

    def get_all_lead_ids(self) -> set[str]:
        return {r["lead_id"] for r in self.read_all() if r.get("lead_id")}
