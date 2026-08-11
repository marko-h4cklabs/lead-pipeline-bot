"""
Pipeline API — backend za Voice Stitcher webapp.

Koristi Service Account credentials za Google Sheets pristup.
Korisnici se ne prijavljuju kroz Google — šalju API ključ u X-API-Key headeru.

Env varijable (postavi na Render.com):
  GOOGLE_SHEETS_ID      — ID Spreadsheeta
  GOOGLE_SA_JSON_B64    — base64-enkodiran SA JSON key
  API_KEY               — dijeljeni pristupni ključ (frontend ga šalje u headeru)
"""
import asyncio
import base64
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from googleapiclient.discovery import build
from pydantic import BaseModel

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ─── CONFIG ────────────────────────────────────────────────────────────────────
SHEETS_ID   = os.environ.get("GOOGLE_SHEETS_ID", "")
API_KEY_ENV = os.environ.get("API_KEY", "")
SCOPES      = ["https://www.googleapis.com/auth/spreadsheets"]
LEADS_TAB   = "Leads"
FP_TAB      = "FixedPitches"
FP_CHUNK_SIZE = 38000

# Kolone Leads taba — mora odgovarati shemi u scripts/lib/sheets.py
COLUMNS = [
    "lead_id", "naziv_firme", "oib", "vlasnik", "telefon", "web",
    "grad", "zupanija", "opis", "broj_zaposlenih", "godina_osnutka",
    "izvor", "status", "claimed_by", "datum_dodan", "datum_poslano",
    "link", "obradjen_flag",
]
_TEXT_COLS = {"lead_id", "oib", "telefon"}


def _to_val(col: str, val) -> str:
    s = str(val) if val is not None else ""
    return ("'" + s) if col in _TEXT_COLS and s else s


# ─── GOOGLE SHEETS SERVICE ─────────────────────────────────────────────────────
_creds = None
_sheets_svc = None


def _get_svc():
    global _creds, _sheets_svc
    if _creds is None:
        sa_b64 = os.environ.get("GOOGLE_SA_JSON_B64", "")
        if sa_b64:
            sa_info = json.loads(base64.b64decode(sa_b64).decode())
            _creds = service_account.Credentials.from_service_account_info(
                sa_info, scopes=SCOPES
            )
        else:
            # Lokalni razvoj: ADC (gcloud auth application-default login)
            from google.auth import default as _default
            _creds, _ = _default(scopes=SCOPES)
    if hasattr(_creds, "refresh") and not _creds.valid:
        _creds.refresh(GoogleAuthRequest())
    if _sheets_svc is None:
        _sheets_svc = build("sheets", "v4", credentials=_creds, cache_discovery=False)
    return _sheets_svc


# ─── ASYNCIO LOCKS (serijalizacija write-ova) ──────────────────────────────────
_leads_lock = asyncio.Lock()
_fp_lock    = asyncio.Lock()


# ─── AUTH ──────────────────────────────────────────────────────────────────────
def _verify_key(x_api_key: str = Header(..., alias="X-API-Key")) -> None:
    if not API_KEY_ENV or x_api_key != API_KEY_ENV:
        raise HTTPException(status_code=401, detail="Pogrešan API ključ")


# ─── FASTAPI APP ───────────────────────────────────────────────────────────────
app = FastAPI(title="Pipeline API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


# ─── HELPERS: LEADS ────────────────────────────────────────────────────────────
def _read_all_leads() -> list[dict]:
    last_col = chr(ord("A") + len(COLUMNS) - 1)
    result = (
        _get_svc().spreadsheets().values()
        .get(spreadsheetId=SHEETS_ID, range=f"'{LEADS_TAB}'!A:{last_col}")
        .execute()
    )
    rows = result.get("values") or []
    if len(rows) < 2:
        return []
    out = []
    for i, row in enumerate(rows[1:], start=2):
        padded = row + [""] * (len(COLUMNS) - len(row))
        d = dict(zip(COLUMNS, padded))
        d["_row"] = i
        out.append(d)
    return out


def _update_lead_row(all_rows: list[dict], lead_id: str, updates: dict) -> bool:
    last_col = chr(ord("A") + len(COLUMNS) - 1)
    for row in all_rows:
        if row.get("lead_id") != lead_id:
            continue
        row_num = row["_row"]
        for k, v in updates.items():
            if k in {c: True for c in COLUMNS}:
                row[k] = v
        new_row = [_to_val(col, row.get(col, "")) for col in COLUMNS]
        _get_svc().spreadsheets().values().update(
            spreadsheetId=SHEETS_ID,
            range=f"'{LEADS_TAB}'!A{row_num}:{last_col}{row_num}",
            valueInputOption="USER_ENTERED",
            body={"values": [new_row]},
        ).execute()
        return True
    return False


# ─── HELPERS: FIXED PITCHES ────────────────────────────────────────────────────
def _fp_ensure_tab() -> None:
    meta = _get_svc().spreadsheets().get(spreadsheetId=SHEETS_ID).execute()
    existing = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if FP_TAB in existing:
        return
    _get_svc().spreadsheets().batchUpdate(
        spreadsheetId=SHEETS_ID,
        body={"requests": [{"addSheet": {"properties": {"title": FP_TAB}}}]},
    ).execute()
    _get_svc().spreadsheets().values().update(
        spreadsheetId=SHEETS_ID,
        range=f"'{FP_TAB}'!A1:F1",
        valueInputOption="USER_ENTERED",
        body={"values": [["pitch_id", "naziv", "audio_data_url", "spremio",
                          "datum_spremanja", "aktivan"]]},
    ).execute()


def _read_fp_tab() -> list[dict]:
    try:
        result = (
            _get_svc().spreadsheets().values()
            .get(spreadsheetId=SHEETS_ID, range=f"'{FP_TAB}'!A:F")
            .execute()
        )
    except Exception as e:
        if "Unable to parse range" in str(e) or "not found" in str(e).lower():
            return []
        raise
    rows = (result.get("values") or [])[1:]  # preskoči header

    main_rows: list[list] = []
    chunk_map: dict[str, list[str]] = {}
    for r in rows:
        r = r + [""] * 6
        id_ = r[0]
        m = re.match(r"^(.+)#(\d+)$", id_)
        if m:
            base_id, n = m.group(1), int(m.group(2))
            if base_id not in chunk_map:
                chunk_map[base_id] = []
            while len(chunk_map[base_id]) < n:
                chunk_map[base_id].append("")
            chunk_map[base_id][n - 1] = r[2]
        elif id_:
            main_rows.append(r)

    pitches = []
    for r in main_rows:
        r = r + [""] * 6
        id_, naziv, audio_cell, spremio, datum, aktivan = (
            r[0], r[1], r[2], r[3], r[4], r[5]
        )
        if not id_:
            continue
        if audio_cell.startswith("data:"):
            m2 = re.match(r"^data:([^;]+);base64,(.+)$", audio_cell, re.DOTALL)
            if not m2:
                continue
            mime, b64 = m2.group(1), m2.group(2)
        elif audio_cell.startswith("chunk:"):
            mime = audio_cell[6:]
            b64 = "".join(chunk_map.get(id_, []))
        else:
            continue
        pitches.append({
            "id": id_,
            "naziv": naziv or "Bez naziva",
            "mime": mime,
            "b64": b64,
            "active": aktivan.upper() == "DA",
            "spremio": spremio,
            "datum_spremanja": datum,
        })
    return pitches


def _pitch_to_rows(pitch: dict) -> list[list]:
    pid    = pitch["id"]
    naziv  = pitch.get("naziv", "")
    mime   = pitch["mime"]
    b64    = pitch["b64"]
    active = "DA" if pitch.get("active") else "NE"
    spr    = pitch.get("spremio", "")
    datum  = pitch.get("datum_spremanja", "")

    if len(b64) <= FP_CHUNK_SIZE:
        return [[pid, naziv, f"data:{mime};base64,{b64}", spr, datum, active]]

    rows = [[pid, naziv, f"chunk:{mime}", spr, datum, active]]
    for i, start in enumerate(range(0, len(b64), FP_CHUNK_SIZE), 1):
        rows.append([f"{pid}#{i}", "", b64[start : start + FP_CHUNK_SIZE], "", "", ""])
    return rows


def _fp_write_all(pitches: list[dict]) -> None:
    """Clear + rewrite cijelog FP taba. Poziva se isključivo unutar _fp_lock."""
    _get_svc().spreadsheets().values().clear(
        spreadsheetId=SHEETS_ID,
        range=f"'{FP_TAB}'!A2:F9999",
        body={},
    ).execute()
    if not pitches:
        return
    rows: list[list] = []
    for p in pitches:
        rows.extend(_pitch_to_rows(p))
    _get_svc().spreadsheets().values().update(
        spreadsheetId=SHEETS_ID,
        range=f"'{FP_TAB}'!A2",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()


# ─── LEADS ENDPOINTS ──────────────────────────────────────────────────────────
@app.get("/api/leads")
async def get_leads(_: None = Depends(_verify_key)):
    """Vraća sve leadove (svi statusi). Frontend filtrira po statusu."""
    leads = await asyncio.to_thread(_read_all_leads)
    for l in leads:
        l.pop("_row", None)
    return leads


@app.get("/api/leads/{lead_id}")
async def get_lead(lead_id: str, _: None = Depends(_verify_key)):
    leads = await asyncio.to_thread(_read_all_leads)
    for l in leads:
        if l.get("lead_id") == lead_id:
            l.pop("_row", None)
            return l
    raise HTTPException(status_code=404, detail="Lead nije nađen")


class StatusUpdate(BaseModel):
    status: Optional[str] = None
    claimed_by: Optional[str] = None
    obradjen_flag: Optional[str] = None
    datum_poslano: Optional[str] = None


@app.post("/api/leads/{lead_id}/status")
async def update_lead_status(
    lead_id: str, body: StatusUpdate, _: None = Depends(_verify_key)
):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        return {"ok": True}
    async with _leads_lock:
        all_rows = await asyncio.to_thread(_read_all_leads)
        found = await asyncio.to_thread(_update_lead_row, all_rows, lead_id, updates)
    if not found:
        raise HTTPException(status_code=404, detail="Lead nije nađen")
    return {"ok": True}


# ─── FIXED PITCHES ENDPOINTS ──────────────────────────────────────────────────
@app.get("/api/fixed-pitches")
async def get_fixed_pitches(_: None = Depends(_verify_key)):
    await asyncio.to_thread(_fp_ensure_tab)
    pitches = await asyncio.to_thread(_read_fp_tab)
    return pitches


class NewPitch(BaseModel):
    naziv: str
    mime: str
    b64: str
    email: Optional[str] = None


@app.post("/api/fixed-pitches")
async def create_fixed_pitch(body: NewPitch, _: None = Depends(_verify_key)):
    await asyncio.to_thread(_fp_ensure_tab)
    new_id = str(int(datetime.now(tz=timezone.utc).timestamp() * 1000))
    async with _fp_lock:
        pitches = await asyncio.to_thread(_read_fp_tab)
        has_active = any(p.get("active") for p in pitches)
        new_pitch = {
            "id": new_id,
            "naziv": body.naziv,
            "mime": body.mime,
            "b64": body.b64,
            "active": not has_active,
            "spremio": body.email or "",
            "datum_spremanja": datetime.now(tz=timezone.utc).isoformat(),
        }
        pitches.append(new_pitch)
        await asyncio.to_thread(_fp_write_all, pitches)
    return {
        "id": new_id,
        "naziv": body.naziv,
        "active": new_pitch["active"],
        "datum_spremanja": new_pitch["datum_spremanja"],
        "mime": body.mime,
    }


@app.delete("/api/fixed-pitches/{pitch_id}")
async def delete_fixed_pitch(pitch_id: str, _: None = Depends(_verify_key)):
    await asyncio.to_thread(_fp_ensure_tab)
    async with _fp_lock:
        pitches = await asyncio.to_thread(_read_fp_tab)
        remaining = [p for p in pitches if p["id"] != pitch_id]
        if len(remaining) == len(pitches):
            raise HTTPException(status_code=404, detail="Pitch nije nađen")
        # Ako je obrisani bio aktivan → postavi prvog preostalog kao aktivnog
        deleted_was_active = any(
            p.get("active") and p["id"] == pitch_id for p in pitches
        )
        if deleted_was_active and remaining:
            remaining[0]["active"] = True
        await asyncio.to_thread(_fp_write_all, remaining)
    return {"ok": True, "remaining": len(remaining)}


@app.post("/api/fixed-pitches/{pitch_id}/activate")
async def activate_fixed_pitch(pitch_id: str, _: None = Depends(_verify_key)):
    await asyncio.to_thread(_fp_ensure_tab)
    async with _fp_lock:
        pitches = await asyncio.to_thread(_read_fp_tab)
        found = False
        for p in pitches:
            p["active"] = p["id"] == pitch_id
            if p["id"] == pitch_id:
                found = True
        if not found:
            raise HTTPException(status_code=404, detail="Pitch nije nađen")
        await asyncio.to_thread(_fp_write_all, pitches)
    return {"ok": True}


# ─── HEALTH ───────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "sheets_id": SHEETS_ID[:8] + "…" if SHEETS_ID else "not set"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False
    )
