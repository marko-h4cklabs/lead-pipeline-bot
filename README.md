# WhatsApp Lead & Voice Outreach Pipeline

B2B outreach pipeline za HR tržište — od skupljanja firmi do personalizirane glasovne poruke u WhatsApp.

```
collect → verify → filter → Voice Stitcher → poslano
```

Sve bez backend servera: Google Sheets kao baza, GitHub Actions za automatizaciju, GitHub Pages za webapp.

---

## Što trebaš napraviti ručno (jednom, prije prvog pokretanja)

### 1. Google Sheet

1. Kreiraj novi Google Sheet s ovim kolonama točno ovim redom (A–Q):

| # | Kolona | Tip |
|---|--------|-----|
| A | lead_id | tekst |
| B | naziv_firme | tekst |
| C | oib | tekst |
| D | vlasnik | tekst |
| E | telefon | tekst |
| F | web | tekst |
| G | grad | tekst |
| H | zupanija | tekst |
| I | opis | tekst |
| J | broj_zaposlenih | broj |
| K | godina_osnutka | broj |
| L | izvor | tekst |
| M | status | tekst |
| N | claimed_by | tekst |
| O | datum_dodan | datum |
| P | datum_poslano | datum/tekst |
| Q | link | formula |

2. Preimenuj tab (karticu) u: **Leads**

3. U ćeliju **Q2** zalijepi ovu formulu (prilagodi URL na kraj):
   ```
   =IFERROR(HYPERLINK("https://USERNAME.github.io/lead-scraper-august/?lead_id="&A2&"&naziv="&ENCODEURL(B2)&"&vlasnik="&ENCODEURL(D2)&"&opis="&ENCODEURL(I2),"Otvori"),"")
   ```
   Zatim povuci formulu do dna (ili koristi ArrayFormula za auto-extend).

4. Zabilježi **Sheet ID** iz URL-a: `https://docs.google.com/spreadsheets/d/**SHEET_ID**/edit`

### 2. Google Cloud Console

1. Idi na [console.cloud.google.com](https://console.cloud.google.com)
2. Kreiraj novi projekt (npr. "lead-pipeline")
3. Omogući **Google Sheets API** i **Google Places API (New)**
4. Kreiraj **Service Account**:
   - IAM & Admin > Service Accounts > Create
   - Preuzmi JSON ključ → pohrani lokalno kao `sa_key.json` (ne commitat!)
   - Dijeli Sheet s emailom Service Accounta (Editor pristup)
5. Kreiraj **OAuth 2.0 Client ID** (za webapp):
   - APIs & Services > Credentials > Create > OAuth client ID
   - Tip: Web application
   - Authorized JavaScript origins: `https://USERNAME.github.io`
   - Zabilježi **Client ID**

### 3. GitHub Secrets

U repozitoriju: Settings > Secrets and variables > Actions > New repository secret:

| Secret | Vrijednost |
|--------|-----------|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Cijeli sadržaj `sa_key.json` |
| `GOOGLE_SHEETS_ID` | ID Sheeta |
| `GOOGLE_PLACES_API_KEY` | Places API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `GITHUB_PAGES_BASE` | `https://USERNAME.github.io/lead-scraper-august/` |

### 4. Webapp konfiguracija

Otvori [`webapp/index.html`](webapp/index.html) i zamijeni ova dva placeholdera:

```javascript
const OAUTH_CLIENT_ID = 'REPLACE_WITH_GOOGLE_OAUTH_CLIENT_ID';  // ← tvoj OAuth Client ID
const SHEETS_ID       = 'REPLACE_WITH_GOOGLE_SHEETS_ID';         // ← tvoj Sheet ID
```

### 5. GitHub Pages

- Idi na repo Settings > Pages
- Source: **GitHub Actions** (koristimo `deploy-webapp.yml`)
- Nakon prvog pusha na `main`, webapp će biti dostupan na `https://USERNAME.github.io/lead-scraper-august/`

---

## Pokretanje pipeline-a

Sve tri faze se pokreću ručno iz GitHub Actions > Actions taba:

```
Collect Leads    → skuplja firme, upisuje status='new'
Verify Leads     → CompanyWall scrape, web-scan, Claude opis → 'verified'/'rejected'/'manual_review'
Filter Leads     → min 5 zaposlenih, min 2 godine → 'qualified'/'filtered_out'
```

Nakon filtriranja, `qualified` redovi se pojavljuju u Voice Stitcher webapp-u.

---

## Voice Stitcher — tijek rada

1. Otvori `https://USERNAME.github.io/lead-scraper-august/`
2. Prijavi se Google računom (isti koji ima pristup Sheetu)
3. Učitaj fiksni pitch (01 — jednom, sprema se u browser)
4. Klikni "Učitaj iz Sheeta" — pojavljuju se qualified leadovi
5. Klikni lead → automatski se bilježi `in_progress` + `claimed_by`
6. Snimi pozdrav → Spoji → Skini `.ogg`
7. Otvori WhatsApp → povuci OGG poruku → Pošalji
8. Klikni "Označi poslano" → Sheet upisuje `sent` + timestamp

ILI: klikni link direktno iz Sheet `Q` kolone → otvori app s tim leadom predučitanim.

---

## Lokalni development

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # popuni s pravim vrijednostima

# Pokreni pojedini script
python -m scripts.collect.main
python -m scripts.verify.main
python -m scripts.filter.main

# Webapp lokalno (za testiranje bez GitHub Pages)
cd webapp && python -m http.server 8080
# Otvori http://localhost:8080
```

---

## Status flow

```
new → verified / rejected / manual_review → qualified / filtered_out → in_progress → audio_ready → sent
```

- `manual_review` — nema mobilnog broja; čeka ručni unos prije snimanja
- `rejected` — firma u blokadi ili ima online shop (nije quote-based)
- `filtered_out` — premalo zaposlenih ili mlada firma

---

## Napomene o CompanyWall scrapingu

- Scraper koristi 3–6s jitter između zahtjeva + backoff na 429/503
- Max 80 lookupa po pokretanju (konfigurabilno `MAX_VERIFY_PER_RUN`)
- Selektori testirani: 04.08.2026. — CompanyWall može promijeniti HTML strukturu bez najave
- Ako scraper prestane raditi: provjeri `scripts/verify/companywall_detail.py` → `_extract_dt_map()`
