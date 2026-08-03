# TP-acc backend — datamodell och beräkning

Backend för TP-acc-modulen i Stockpit: hämtar riktkursdata från FMP, lagrar i
Supabase och exponerar tidsviktad acceleration per ticker.

## Två epoker — blanda aldrig serierna

| Epok | Lagras i | Innehåll |
|---|---|---|
| **pre-raw** | `consensus_snapshots` (epoch='pre-raw') | Dagligt FMP-aggregat, ett värde/dag. Arkiv — kan inte rekonstrueras bakåt. |
| **parity** | `consensus_snapshots` (epoch='parity') | Snapshots som körs PARALLELLT med rådata i ~30 dagar för att diffa de två acc-vägarna innan panelen byter källa. |
| **raw** | `tp_revisions` | En rad per riktkursrevision per hus. Aggregatet BERÄKNAS härur (carry-forward), lagras aldrig. |

`consensus_snapshots` tas aldrig bort — den är read-only-arkiv. Den beräknade
carry-forward-serien och det gamla snapshot-aggregatet får aldrig ligga i samma
tidsserie: brytpunkten är den dag `tp_revisions` börjar fyllas (backfillen ger
~20 mån överlapp, så det blir en överlappszon, inte ett hårt brott).

## Datamodell (`tp_revisions`)

En rad per revision. Lagrar bara de irreducibla fakta FMP ger; `old_target`,
`delta_abs`, `delta_pct` och proveniens HÄRLEDS i vyn `tp_revisions_enriched`
(via `lag` per hus), inte frysta i tabellen — så en backfill som fyller en lucka
självrättar "föregående" för efterföljande poster.

- `analyst_house` — normaliserat husnamn (`normalize_house`): alias-tabellen
  `analyst_house_alias` mergar kända varianter ("MS" → "Morgan Stanley").
  Okända namn behålls oförändrade — aggressiv strippning som kan slå ihop olika
  hus är förbjuden ("aldrig hittepå" åt båda håll).
- `old_target_kalla` — `'kalla'` om FMP gav prior (ovanligt på Starter), annars
  `'harledd'` (samma hus föregående mål). Härlett förväxlas aldrig med verifierat.
- **Reiterationer** (samma hus, samma mål, nytt datum) lagras — bekräftelse är
  information — men räknas INTE i `n_revisions`: klustermetriken räknar bara
  poster med `delta_abs <> 0`, annars blåser upprepningar upp "klustret".
- Idempotens: unik nyckel `(ticker, analyst_house, revision_date, new_target)`.
  Omkörning och backfill dubbellagrar aldrig.

## Så beräknas accelerationen (formeln utskriven)

Formeln är oförändrad från snapshot-eran (tidsviktad andra-derivata) — bara
INDATA byts: en **carry-forward-konsensus** byggd ur rådatat i stället för det
dagliga FMP-aggregatet.

1. **Carry-forward-konsensus** för varje dag `d` i fönstret (default 30 dagar):

   ```
   konsensus(d) = medel över hus av husets SENASTE new_target
                  där revision_date <= d  OCH  revision_date > d − STALE
   ```

   `STALE` (default **180 dagar**, konfigurerbart via `p_stale_days`) är i
   praktiken en formelparameter: ett hus vars senaste mål är äldre än fönstret
   bärs INTE med. Utan det driver döda mål konsensusen ifrån verkligheten i takt
   med att inaktuella hus ackumuleras.

2. **Tidsviktad d²TP/dt²** på den dagliga serien:

   ```
   v(dag)  = Δkonsensus / Δt                       (första derivata, TP/dag)
   a(dag)  = Δv / (medel av angränsande Δt)         (andra derivata, TP/dag²)
   acc     = Σ(a · vikt) / Σ(vikt),  vikt = max(fönster − ålder_i_dagar, 1)
   ```

   Färska accelerationer väger linjärt tyngre. Panelen visar `acc / konsensus ·
   10000` i **bp/dag²** (normaliserat så olika prisnivåer blir jämförbara).

   Kanonisk definition: SQL-funktionen `get_tp_acceleration` (migration 0001).
   JS-spegeln `scripts/tp_acceleration.js` används av testerna och paritetsjobbet
   och hålls i synk — samma mönster som resten av repot.

**Känd egenskap (dokumenteras, göms inte):** carry-forward-serien är en trappa.
Ett hopp följt av platt läses av d²/dt² som inbromsning; ett gammalt hopp med
lång platt läses som ~0. Med MÅNGA hus (t.ex. NVDA, 34 st) rör varje enskild
revision snittet lite → serien blir jämn och acc meningsfull. Med FÅ hus blir
den taggig — därför flaggas sådana värden (se nedan). Detta är motsatt artefakt
mot snapshot-vägen, som läste stale-hus som FALLER UR FMP:s fönster som
revisionsacceleration (noll ny information). Rådatavägen + stale-fönstret
eliminerar exakt den artefakten.

## Metadata per acc-värde (Åtgärd 2 & 3)

Vyn `tp_acceleration_current` ger per ticker:

- `n_revisions` — revisioner med `delta<>0` i 30 d-fönstret.
- `n_houses` — unika hus bakom dem. **Visas i panelen bredvid acc.**
- `n_houses_live` — hus med levande mål idag (konsensusens bredd).
- `largest_single_contribution_pct` — största enskilda `|delta|` / total `|delta|`.
  **> 60 % → panelen flaggar "◆ ej kluster"** (META-typfallet: en ensam revision).
- `analyst_count`, `upside_pct`.

**Nolldata-spärr:** 0 revisioner i fönstret → `acceleration` = null → panelen
renderar "—", aldrig 0,0. `analyst_count` under tröskeln (default 8) → raden
nedtonad "tunt underlag". Ingesten loggar rader där uppsida är beräkningsbar men
analytiker = 0 i `ingest_anomalies` — datakvalitetsfel ska synas, inte tyst passera.

## Datatäckning och gränser

- **Icke-US-tickers saknar per-hus-data.** FMP:s price-target-news är US-centrerad;
  europeiska listningar (t.ex. `OR.PA`, `SIE.DE`) ger "inga poster". De får därför
  inga `tp_revisions`, och `tp_acceleration_current` ger dem ingen rad → panelen
  renderar "—" enligt nolldata-regeln. Det är väntat, inte ett fel att felsöka.
- **Historikdjupet varierar per ticker även efter paginering.** Pagineringen hämtar
  ALLA sidor FMP har, men FMP:s historik är olika lång per bolag (bevakningsstart,
  börsintroduktion). Acc räknas alltid på samma 30-dagarsfönster, så acc-jämförelser
  mellan tickers är rättvisa. Men **leda/följa-analys mellan tickers** kan träffa
  olika historikdjup — kontrollera täckningen först, t.ex.
  `select ticker, min(revision_date), max(revision_date), count(*) from tp_revisions group by ticker`.

## Drift och migration

1. Kör migrationerna i Supabase SQL-editorn (service-rollen — DDL går ej via REST),
   i ordning. Var och en är reversibel via sin `..._down.sql`:
   - `supabase/migrations/0001_tp_revisions_up.sql` — tabeller, vyer, funktioner.
   - `supabase/migrations/0002_grants_up.sql` — rättigheter: ger service_role
     SELECT på vyerna (annars nekas backfill/parity), drar tillbaka anon från
     råtabellerna (minsta-privilegium — panelen läser bara vyn
     `tp_acceleration_current`) och gör `get_tp_acceleration` till SECURITY
     DEFINER så vyn är rättighetsgränsen.
2. Backfilla historiken en gång: `node scripts/backfill_revisions.js` (env satt).
   Skriptet PAGINERAR price-target-news (`page`-parametern, 100 poster/sida) tills
   en tom/partiell sida — annars stannar de mest bevakade tickrarna på sidtaket
   100 och får för grund historik. Idempotent, kan köras om.
3. Daglig ingest (`ingest-data.js`) skriver nu BÅDE snapshots (epoch='parity')
   OCH rådata-revisioner — parallellperioden.
4. Verifiera/diffa vägarna: `node scripts/parity_check.js`.
5. Efter ~30 dagars paritetsgranskning: flippa `TPA_KALLA = "raw"` i `index.html`.
   Då tänds n_houses + kluster-flagga + tomt/tunt i panelen.

Tester: `node --test tests/` (idempotens, husnormalisering, acc mot handräknat
facit, stale-fönster, nolldata-spärr, klusterflagga).
