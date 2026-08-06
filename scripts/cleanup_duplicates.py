"""
Jednokratni cleanup script — brisanje duplikata iz Google Sheeta.

Logika:
  - Grupira redove po (normalized_naziv, cw_domain_slug) gdje je cw_domain_slug
    path segmenta CompanyWall URL-a (jer svi dijele domenu companywall.hr).
    Ako web nije CW URL, grupira po (normalized_naziv, extracted_domain).
  - U svakoj grupi zadržava najstariji red (najmanji row broj = prvi upisani).
  - Briše sve ostale redove odozdo prema gore (da ne miče indekse).

Pokretanje (lokalno, s ADC-om):
  GOOGLE_SHEETS_ID=... python -m scripts.cleanup_duplicates

Ili s explicit credentials:
  GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa_key.json GOOGLE_SHEETS_ID=... python -m scripts.cleanup_duplicates
"""
import logging
import os
import sys
from collections import defaultdict
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from scripts.lib.sheets import SheetsClient, SHEET_NAME
from scripts.lib.dedupe import normalize_name, extract_domain

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("cleanup")


def _cw_slug(web: str) -> str:
    """Vraća path slug CompanyWall URL-a (jedinstveni ID firme na CW-u)."""
    if not web:
        return ""
    try:
        parsed = urlparse(web)
        if "companywall" in parsed.netloc:
            return parsed.path.rstrip("/").lower()
    except Exception:
        pass
    return ""


def dedup_key(row: dict) -> str:
    """
    Ključ za grupiranje duplikata.
    Prioritet: CW slug > naziv+domena > naziv.
    """
    web = row.get("web", "")
    slug = _cw_slug(web)
    nn = normalize_name(row.get("naziv_firme", ""))
    if slug:
        return f"cw:{slug}"
    domain = extract_domain(web)
    if domain:
        return f"nd:{nn}|{domain}"
    return f"n:{nn}" if nn else f"row:{row.get('_row', '?')}"


def run(dry_run: bool = False):
    log.info("=== Cleanup duplikata — %s ===", "DRY RUN" if dry_run else "LIVE")
    client = SheetsClient()
    all_rows = client.read_all()
    log.info("Ukupno redova u Sheetu: %d", len(all_rows))

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in all_rows:
        key = dedup_key(row)
        groups[key].append(row)

    rows_to_delete: list[int] = []
    for key, group in groups.items():
        if len(group) == 1:
            continue
        # Sortiraj po row broju — zadržavamo prvi (najmanji = najstariji)
        group_sorted = sorted(group, key=lambda r: r["_row"])
        keep = group_sorted[0]
        duplicates = group_sorted[1:]
        log.info(
            "Duplikat [%s]: zadržavam row %d (lead_id=%s), brišem rows %s",
            key[:60],
            keep["_row"],
            keep.get("lead_id", "?"),
            [r["_row"] for r in duplicates],
        )
        rows_to_delete.extend(r["_row"] for r in duplicates)

    if not rows_to_delete:
        log.info("Nema duplikata — Sheet je čist.")
        return

    log.info("Redova za brisanje: %d", len(rows_to_delete))

    if dry_run:
        log.info("DRY RUN — ništa nije obrisano. Pokreni bez --dry-run za brisanje.")
        return

    # Briši odozdo prema gore da ne miče indekse
    rows_to_delete.sort(reverse=True)

    svc = client._svc
    sid = client._sid

    # Sheets API koristi 0-based row indeks za deleteDimension
    # Red 1 = header → data počinje od reda 2 (indeks 1 u API-u)
    requests = []
    for row_num in rows_to_delete:
        zero_based = row_num - 1  # Sheet row je 1-based, API želi 0-based
        requests.append({
            "deleteDimension": {
                "range": {
                    "sheetId": 0,  # pretpostavljamo prvi sheet; ažurira se dolje
                    "dimension": "ROWS",
                    "startIndex": zero_based,
                    "endIndex": zero_based + 1,
                }
            }
        })

    # Dohvati pravi sheetId za tab "Leads"
    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    sheet_id = None
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == SHEET_NAME:
            sheet_id = s["properties"]["sheetId"]
            break
    if sheet_id is None:
        log.error("Tab '%s' nije pronađen u Sheetu!", SHEET_NAME)
        return

    for req in requests:
        req["deleteDimension"]["range"]["sheetId"] = sheet_id

    # Pošalji zahtjeve jedan po jedan (svaki briše jednu vrstu)
    # Moramo slati jeden po jedan jer svaki brisanje mijenja indekse —
    # ali budući da ih šaljemo odozdo prema gore, indeksi iznad ostaju nepromijenjeni.
    body = {"requests": requests}
    svc.spreadsheets().batchUpdate(spreadsheetId=sid, body=body).execute()
    log.info("Obrisano %d duplikata.", len(rows_to_delete))
    log.info("=== Cleanup završen ===")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv or "-n" in sys.argv
    run(dry_run=dry)
