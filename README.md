# Stockpit

AI-driven analysplattform i fem vyer bakom en hash-router: **Översikt** (hero + modulkort),
**Sektorrotation**, **Makro & Likviditet**, **Aktier & Konsensus** och **Trångsektorer**.
Fortfarande en enda `index.html`, noll beroenden i frontend — all data läses från
`data/data.json` som uppdateras automatiskt var 30:e minut av GitHub Actions.
Gamla panelankare (`#rek`, `#aktier` …) redirectar till rätt vy.

**Live:** aktiveras via GitHub Pages (Settings → Pages → Deploy from a branch → `main` / root).

## Så hänger det ihop

```
.github/workflows/update.yml   kör var 30:e minut (cron är inte sekundprecis)
        └── scripts/update_data.py
                ├── FRED (räntekurva, HY-spread, realränta)      — fredgraph.csv, ingen nyckel
                ├── Yahoo Finance (olja, guld, silver, koppar,
                │   VIX, sektor-ETF:er, kvotpar)                 — publikt chart-API
                ├── SEC EDGAR full-text search (Form 4, USA)     — JSON, User-Agent med kontakt
                └── FI marknadssök (svenska insynsaffärer)       — EXAKT EN CSV-export per körning,
                                                                    aldrig HTML-skrapning, backoff vid fel
                └──> skriver data/data.json + data/state.json och committar bara vid förändring
                     → pushen får GitHub Pages att publicera om sidan
```

`update_data.py` **mergar**: endast fält med livekälla skrivs över; kurerade fält
(estimatrevideringar, flöden, konvergenskandidater m.m.) behålls med sina egna vintage-märken.
Kontraktet för varje fält finns i [DATA_SCHEMA.md](DATA_SCHEMA.md).

## Filer

| Fil | Roll |
|---|---|
| `index.html` | Hela plattformen — router, fem vyer, motor och en inbäddad TEST-kopia av datat |
| `data/data.json` | LIVE-ögonblicksbilden (skrivs av botten) |
| `data/sample_data.json` | Testdatat — byte-identiskt med det inbäddade blocket |
| `data/state.json` | Bottens minne (senast sedda FI-publicering, felräknare) |
| `data/track_record.json` | Utfallslogg för track record-fliken |
| `scripts/update_data.py` | Datainsamlaren (Python 3.12, endast `requests`) |
| `scripts/screen_data.py` | Konvergensscreenern: hela universum mot sju signaler dagligen (`screen.yml`, vardagar 04:23 UTC) → `data/screen.json` + bredd + rek-loggning |
| `scripts/weekly.py` | Veckojobbet (`weekly.yml`, fredagar 23:43 UTC): estimatproxy, PE-historik, AAII-sentiment, COT-crowding, AI-capex, track record-utfall |
| `KADENS.md` | Färskhetskontraktet: kadens och åldersvakt per fält, trenivåförfall |
| `scripts/bygg_universum.py` | Bygger `data/universum.json` (S&P 500 ur Wikipedia + kurerad Sthlm-seed) — körs vid behov |
| `REGELVERK.md` | Normerande: alla listors rankningsnycklar, vikter och konvergenskedjan till KÖPREK |
| `tpa/` | Backend för TP-acc-fliken: Node-ingest (FMP + Yahoo → Supabase) + SQL-schema; körs av `daily-ingest.yml` vardagar 22:00 UTC med secrets `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`/`FMP_API_KEY`. Frontenden läser samma Supabase direkt med publik läsnyckel. |

## TEST eller LIVE

En rad i `index.html` styr: `var DATALAGE = "LIVE";` (eller `"TEST"`).
Sidan sätter dock alltid badges efter vad datat *säger* (`lage`-fältet) — badgen kan aldrig ljuga.
Öppnas filen direkt från disk (utan server) faller den ärligt tillbaka till inbäddat testdata.

## Köra lokalt

```
python -m http.server 8123
# öppna http://127.0.0.1:8123/?debug=1  — debug-badgen ska visa "assertions=gröna"
```

## Verifiera att pipelinen lever

1. **Actions-fliken** — senaste "Uppdatera data"-körning grön.
2. **Commit-historiken** på `data/data.json` — ny commit när något ändrats.
3. **Sidan själv** — stämpeln "Data per v.X" uppe till höger; blir datat äldre än 24 h
   visar sidan en gul åldersvarning = pipelinen har stannat.
