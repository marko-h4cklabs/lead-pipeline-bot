# WhatsApp Lead & Voice Outreach Pipeline — Build Spec

Google Sheets kao centralni "source of truth". Svaki red = jedan prospekt. Status kolona prati napredak kroz pipeline. GitHub Actions + statična GitHub Pages app, bez backend servera.

## Status flow
`new` → `verified` / `rejected` → `qualified` / `filtered_out` → `in_progress` → `audio_ready` → `sent`

## Sheet schema (kolone)
| Kolona | Opis |
|---|---|
| lead_id | jedinstveni ID (generiran pri kreiranju, ne mijenja se — koristi se za lookup, ne row index) |
| naziv_firme | |
| oib | popunjava se u verifikaciji preko CompanyWall.hr |
| vlasnik | ime vlasnika/direktora, preko CompanyWall.hr |
| telefon | |
| web | |
| grad / zupanija | |
| opis | 1-2 rečenice čime se firma bavi — generira se u verifikaciji (scrape + Claude API sažetak) |
| broj_zaposlenih | |
| godina_osnutka | |
| izvor | Google Places / CompanyWall / ručno |
| status | vidi flow gore |
| claimed_by | Marko / kolega — soft lock kad netko otvori link za taj red |
| datum_dodan | |
| datum_poslano | |
| link | `=HYPERLINK(...)` formula koja otvara Stitcher app s parametrima ovog reda |

## Dio 1 — Lead Collecting
- Trigger: `workflow_dispatch` (ručno, po potrebi) na GitHub Actions
- Izvori: Google Places API (New) + CompanyWall.hr pretraga + ručni unos (svi u istom dedupe krugu)
- Niša: sve firme kojima posao dolazi kroz dolazne upite (quote-based, bez cjenika online)
- Regija: cijela Hrvatska osim Međimurske i Varaždinske županije
- Dedupe (slojevito, bilo koji match = duplikat): OIB → naziv+telefon → naziv+web domena
- Output: novi red, status `new`

## Dio 2 — Verifikacija
Prioritet: **ime vlasnika, broj mobitela, status firme (aktivna/u blokadi)** — to su tri ključna polja, sve ostalo je sekundarno.

**Izvori (slojevito):**
1. **CompanyWall.hr** — primarni i praktički jedini izvor za sva tri ključna polja (vlasnik, mobitel, status). Mobitel je redovito naveden (potvrđeno ručnim testiranjem), ne samo fiksni/centrala.
2. **provjera.hr** — besplatan, bez logina, koristi se SAMO kao fallback za potvrdu OIB-a/statusa ako CompanyWall ne nađe firmu (ne za vlasnika/mobitel — provjera.hr te podatke ne prikazuje direktno, samo link-out na Sudski registar)
3. Sudski registar / FINA API-ji — odgođeno, nije blokirajuće za sada (login problem), razmotriti kasnije kao dodatni cross-check ako se pojavi potreba

**Logika po redu:**
- CompanyWall vrati vlasnika + mobitel + status → `verified`, upiši sve
- CompanyWall vrati status ali NEMA mobitel → `manual_review` (čeka ručni unos broja prije nego može u Dio 4, ne odbacuje se automatski)
- CompanyWall ne nađe firmu uopće → probaj provjera.hr samo za potvrdu da firma postoji/aktivna → ako ni to ne uspije, `manual_review`
- Web-scan: traži cjenik/checkout/"dodaj u košaricu" → ako postoji, automatski `rejected` (nije quote-based)
- Scrape about/homepage teksta → Claude API sažetak u 1-2 rečenice → kolona `opis`
- Status: `new` → `verified` / `manual_review` / `rejected` (s razlogom)

**Rate-limit / cooldown (CompanyWall scraping):**
- 1 zahtjev na 3-6 sekundi, nasumični jitter (ne fiksni interval)
- Na 429/503: backoff 1 min → 5 min → 15 min → ako i dalje pada, batch staje do sljedećeg ručnog pokretanja
- Max ~80-100 lookupova po pokretanju (workflow_dispatch), ne cijeli batch odjednom
- Sheet služi kao cache — nikad ponovni lookup za red koji već ima popunjen status

## Dio 3 — Filtering
- Ulaz: samo `verified` redovi
- Kriterij: min. 5 zaposlenih (bez gornje granice), min. 2-3 godine poslovanja
- Status: `verified` → `qualified` ili `filtered_out`

## Dio 4 — Audio Crafting (Voice Stitcher app)
- GitHub Pages statična app, Google OAuth (Google Identity Services) direktno iz browsera za čitanje/pisanje u Sheet — bez servera
- Otvara se preko `link` kolone u Sheetu (URL params: lead_id, naziv, vlasnik, opis) ILI kroz listu unutar app-a (qualified & neobrađeni redovi)
- Prikazuje: naziv firme, vlasnik, opis
- Snimanje pozdrava (mikrofon) → spajanje s fiksnim pitchem → izvoz kao OGG/Opus (postojeća funkcionalnost iz Voice Stitcher alata)
- Nakon spajanja: upis natrag u Sheet — status `audio_ready`
- Download + drag-and-drop u WhatsApp ostaje ručan korak (WhatsApp nema pouzdan API za personal/business slanje glasovnih poruka bez rizika banova)

## Dio 5 — Sending & tracking
- Klik na link u Sheetu → app odmah upiše `status: in_progress`, `claimed_by: [ime]` (soft lock protiv sudara s kolegom)
- Nakon ručnog slanja u WhatsAppu → klik "Označi poslano" u appu → `status: sent`, `datum_poslano` timestamp
- Praćenje odgovora: ručno u WhatsAppu (bez automatike za sada)

## Jednokratni setup (Marko, prije build faze)
1. Google Cloud Console projekt + omogućen Sheets API + OAuth Client ID (za Stitcher app)
2. Claude API key kao GitHub Actions secret (za opis-sažetak u Dio 2)
3. CompanyWall.hr pristup/rate limit provjera za automatizirani scraping
