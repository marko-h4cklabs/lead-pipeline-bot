# Build Brief: WhatsApp Lead & Voice Outreach Pipeline

Ovo je build brief za Claude Code — cilj je da samostalno izgradiš cijeli projekt, testiraš lokalno (uključujući otvaranje browsera gdje treba), i pitaš me samo kad ti stvarno treba odluka ili API ključ koji ja moram ručno pribaviti. Ne trebaš me pitati prompt-po-prompt za svaki fajl/funkciju — radi po ovom planu i javi se na checkpointima označenim niže.

## Kontekst
Ja (Marko) vodim B2B WhatsApp outreach za građevinske/uslužne firme u Hrvatskoj koje posao dobivaju kroz dolazne upite (quote-based). Trebam pipeline koji: skuplja lead-ove → verificira ih → filtrira → mi omogući snimanje personalizirane glasovne poruke → prati status slanja. Sve kroz Google Sheets kao source of truth, GitHub Actions za automatizaciju, GitHub Pages za statičnu web app. Bez backend servera.

## Repo struktura (predlažem, prilagodi ako ima smisla)
```
/scripts
  /collect       -> Dio 1: Google Places + CompanyWall + dedupe -> upiši u Sheet
  /verify        -> Dio 2: CompanyWall scrape (vlasnik/mobitel/status) + provjera.hr fallback + Claude API opis-sažetak
  /filter        -> Dio 3: primjena kriterija na 'verified' redove
  /lib           -> zajedničke funkcije (Sheets API wrapper, dedupe logika, rate-limit/backoff helper)
/.github/workflows
  collect.yml    -> workflow_dispatch, pokreće /scripts/collect
  verify.yml     -> workflow_dispatch, pokreće /scripts/verify
  filter.yml     -> workflow_dispatch, pokreće /scripts/filter
/webapp          -> Voice Stitcher (GitHub Pages) — VEĆ POSTOJI, vidi napomenu ispod
/docs            -> ovaj brief + spec + setup upute
.env.example     -> popis svih env varijabli s placeholder vrijednostima
README.md
```

## Postojeći fajl koji treba ubaciti u repo
Već imam radni **Voice Stitcher** (HTML/JS, snimanje pozdrava preko mikrofona, spajanje s fiksnim pitchem, export u OGG/Opus preko opus-recorder@8.0.5 s cdnjs-a). Prilažem ga kao `whatsapp-voice-stitcher.html` — stavi ga u `/webapp/index.html` i **proširi** ga da:
1. Na loadu čita URL query parametre (`lead_id`, `naziv`, `vlasnik`, `opis`) i ako postoje, odmah učita taj lead umjesto prikaza liste
2. Doda Google OAuth (Google Identity Services, client-side, bez servera) za čitanje/pisanje u Google Sheet
3. Na klik "Zadovoljan sam — spoji" upiše nazad u Sheet status `in_progress` + `claimed_by`, a nakon uspješnog spajanja `audio_ready`
4. "Označi poslano" upisuje `sent` + timestamp

Ne moraš rušiti postojeću logiku snimanja/spajanja — samo je omotaj sa Sheets integracijom.

## Redoslijed gradnje (izvrši u ovom redu, javi se na svakom **CHECKPOINT**)

### 1. Google Sheet setup
- Kreiraj shemu prema `docs/lead-pipeline-spec.md` (kolone: lead_id, naziv_firme, oib, vlasnik, telefon, web, grad, zupanija, opis, broj_zaposlenih, godina_osnutka, izvor, status, claimed_by, datum_dodan, datum_poslano, link)
- `link` kolona = formula koja generira URL na GitHub Pages app s query paramima tog reda

**CHECKPOINT 1:** Reci mi kad je shema spremna — ja ću ručno kreirati sam Sheet u svom Google računu i dati ti Sheet ID (ne trebaš ti kreirati Sheet umjesto mene, samo mi reci točan raspored kolona ako se razlikuje od plana).

### 2. `/scripts/lib` — zajedničke funkcije
- Sheets API wrapper (čitanje/pisanje redova po `lead_id`, ne po row indexu)
- Dedupe funkcija (OIB → naziv+telefon → naziv+domena, slojevito)
- Rate-limit/backoff helper (3-6s jitter, backoff 1min→5min→15min na 429/503, cap ~80-100 po runu)

### 3. `/scripts/collect` — Dio 1
- Google Places API (New) integracija
- CompanyWall.hr pretraga (research strukturu stranice prije pisanja scrapera — otvori par primjera u browseru da vidiš točan HTML/selektor)
- Dedupe protiv postojećih redova prije upisa
- Filter: quote-based niša (heuristika: bez cjenika/checkout na webu — može se djelomično raditi i u Dio 1 kao pre-filter, djelomično u Dio 2)

### 4. `/scripts/verify` — Dio 2
- CompanyWall.hr scrape: vlasnik, mobitel, status (aktivna/blokada)
- provjera.hr kao fallback SAMO za OIB/status potvrdu ako CompanyWall ne nađe firmu
- Web-scan homepage za cjenik/checkout signale → `rejected` ako nađe
- Claude API poziv za `opis` (1-2 rečenice sažetak about/homepage teksta)
- **VAŽNO — provjeri empirijski prije pisanja logike:** da li CompanyWall.hr stranice pouzdano prikazuju i `broj_zaposlenih` i `godina_osnutka` (potrebno za Dio 3 filter). Otvori par primjera u browseru i provjeri. Ako ne, javi mi — možda treba dodati infobiz.fina.hr kao dodatni izvor samo za ta dva polja.

**CHECKPOINT 2:** Prije nego napišeš finalni scraper za CompanyWall, otvori 3-4 primjera stranica u browseru, pokaži mi strukturu podataka koju vidiš (screenshot ili opis), potvrdimo selektore zajedno — CompanyWall nema službeni API pa je scraper osjetljiv na promjene strukture stranice.

### 5. `/scripts/filter` — Dio 3
- Min. 5 zaposlenih (bez gornje granice), min. 2-3 godine poslovanja
- `verified` → `qualified` ili `filtered_out`

### 6. `.github/workflows/*.yml`
- Sva tri kao `workflow_dispatch` (ručni trigger), ne cron
- Secrets referencirani preko `${{ secrets.NAZIV }}` — vidi `.env.example` za popis

### 7. `/webapp` proširenje (Voice Stitcher + Sheets)
Kao opisano gore. Testiraj lokalno (može i preko `python -m http.server` ili slično) — otvori browser, provjeri cijeli flow: link s parametrima → prikaz leada → snimanje → spajanje → OGG export → upis u Sheet.

**CHECKPOINT 3:** Kad je webapp gotov i radi lokalno, javi se prije nego ga deployaš na GitHub Pages — želim sam testirati cijeli flow (snimanje glasa + slanje u WhatsApp) prije nego postane "živ" alat koji koristim svaki dan.

### 8. `.env.example` — popis svih placeholdera
```
GOOGLE_SHEETS_ID=
GOOGLE_OAUTH_CLIENT_ID=          # za webapp, client-side, nije tajna
GOOGLE_PLACES_API_KEY=
ANTHROPIC_API_KEY=               # za opis-sažetak, ide kao GitHub Actions secret
COMPANYWALL_SESSION=             # ako treba login/session za scraping, TBD nakon research-a
```
Ja ću ove vrijednosti dodati naknadno — ne čekaj mene, gradi s ovim kao placeholderima i jasno označi u kodu gdje se koriste.

## Ono što JA (Marko) trebam ručno napraviti, odvojeno od tebe
- Kreirati Google Sheet i podijeliti mi Sheet ID
- Google Cloud Console: projekt, Sheets API, OAuth Client ID
- Google Places API key
- Anthropic API key
- Provjeriti CompanyWall pristup (da li treba login/plaćeni plan za scraping u ovom volumenu)

Ne trebaš me čekati za ovo da počneš graditi strukturu, logiku, i sve što ide s placeholder vrijednostima — samo mi jasno označi u README-u koji koraci čekaju moj ručni unos prije nego pipeline stvarno proradi end-to-end.

## Sažetak filozofije rada
Gradi samostalno, testiraj usput, javi se na 3 checkpointa gore + ako naiđeš na nešto što stvarno zahtijeva moju odluku (ne tehnički detalj koji možeš sam riješiti). Cilj je kvalitetan, robusan build — ne najbrži mogući, nego onaj koji neće pući na prvi edge case.
