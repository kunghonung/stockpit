# STOCKPIT — datakontrakt (v1.0)

Frontenden läser **en** datafil per läge. En konfigurationsrad i `index.html` styr läget:

```js
var DATALAGE = "TEST";   // "TEST" → data/sample_data.json · "LIVE" → data/live_data.json
```

`data/sample_data.json` och den framtida `data/live_data.json` ska ha **exakt samma schema** — pipelinen (FactSet-parsning, sektor-ETF-priser, SEC EDGAR, FI:s Excel-export, FRED) byggs separat och skriver bara om filen. Frontenden rörs inte.

**File://-not:** öppnas sidan direkt från disk (utan webbserver) kan `fetch` inte läsa lokala filer; därför bär `index.html` en **inbäddad kopia** av sample-datan (blocket `<script type="application/json" id="datalager">`). Kopian ska vara byte-identisk med filen — QC-kontrollen extraherar blocket och diffar mot `data/sample_data.json`. Serverad över HTTP är filen alltid sanningen.

## Toppnycklar

| Nyckel | Innehåll |
|---|---|
| `schemaVersion`, `lage`, `asOfWeek`, `asOfDatum`, `genererad`, `illustrativ` | metadata; `asOfWeek` är snapshotens gemensamma ISO-vecka |
| `rimlighet` | sanity-intervall per nyckeltal — dev-assertions varnar utanför |
| `rrgMetod` | `ratioFonster` (12 v), `momKort` (2 v), `momLang` (6 v) — deklareras i UI |
| `linsvikter` | vikter för tidig-cykel- och momentum-linsen; null-komponenter viktas bort |
| `sektorer[]` | de 11 GICS-sektorerna — se nedan |
| `flodenNoter[]` | flödesfakta som inte är sektorstaplar (regioner, småbolag) |
| `tripwires[]`, `regim`, `riskregler[]` | riskpanelens underlag (som tidigare sprintar) |
| `makro` | räntekurva, bredd, sentiment (26 v-serier), realränta, guld/silver/koppar |
| `megatrender[]` | fyra temamatriser (rader × 3 celler, tema→sektor-koppling) — se avsnittet nedan |
| `aktier[]`, `parlnyckel`, `screenadeAntal`, `aktierLineage` | konvergenslistan; `sektorId` kopplar aktie→sektor (insynskluster per sektor räknas härifrån — måste stämma med `insynKluster`) |
| `geo` | geografisk RRG (statisk illustration i testdatan) |

## Sektorobjekt

```json
{ "id": "XLE", "namn": "Energi", "etf": "XLE",
  "perfH1":   { "v": 19.7, "kalla": "S&P sektorindex", "vintage": "2026-06-30" },
  "fwdPE":    { "v": 12.9, "kalla": "FactSet", "vintage": "2026-07" },
  "fwdPE10y": { "v": 15.6, "kalla": "Koyfin 10-årssnitt", "vintage": "approx" },
  "revQ2":    { "v": 55,   "kalla": "FactSet EI", "vintage": "2026-07-11" },
  "revRiktning": "accelererar | stiger | flat | vänder | faller",
  "flodeM":   { "v": 13.0, "andelInflode": 78, "kalla": "SSGA månadsdata", "vintage": "2026-06" }  // eller null,
  "insynKluster": 0,
  "rsRank": 9, "rsRankForra": 10,
  "relSerie19": [ ...19 veckovisa punkter, sektor/SPX indexerat... ]
}
```

- **Lineage-regel:** varje nyckeltal är ett objekt `{v, kalla, vintage}`. `vintage: "approx"` = rimligt antagande, INTE verifierat — UI:t särskiljer inte i dag men fältet finns. Verifierade fakta bär datum.
- **`flodeM` är MÅNADSDATA** (separat vintage). Veckoflöden får aldrig läggas i samma fält — nytt fält `flodeV` när pipeline finns, och panelen renderar dem separat.
- **`relSerie19`:** 19 veckopunkter (18 veckors historik). Frontenden beräknar RRG själv: `x = (pris/SMA12 − 1)·100`, `y_kort = x(nu) − x(nu−2)`, `y_lang = x(nu) − x(nu−6)`. Position ritas på långa läget; avviker korta kvadranten → pulsmarkering ("obekräftad vändning"). Briefen bad om 14 v historik — 19 punkter krävs för 12 v-fönster + 6 v-momentumsteg; avvikelsen är dokumenterad här.
- **`rsRank`/`rsRankForra`:** RRG-placering 1–11 (verifierade lägen v.29: XLV 6, XLU 7, XLE 9, XLF 10 improving). Rank är eget fält, inte härlett ur serien — assertions korskollar rimligheten.
- Saknat underlag = `null`, aldrig hittepå — UI visar "—" och linsen viktar om.

## Linserna (beräknas i frontenden, inget förberäknat i filen)

- **Tidig-cykel** (0–100): eftersläpning 30 % (rsRank normaliserad), värdering 25 % (rabatt mot `fwdPE10y`, negativ rabatt = 0), revideringar 35 % (`revQ2` normaliserad −15→+55 + riktningsbonus accelererar/vänder), flödesvändning 10 % (`flodeM` bytt tecken; null → bortviktad). Makrojustering: brantande 2s10s ger Finans +8 (visas explicit på kortet).
- **Momentum** (0–100): RS-rank 40 %, kvadrant (lång) 25 %, flödesandel 20 %, insynskluster 15 %.
- Handling: tidig-cykel-linsens topp 2 → ÖKA; momentum-topp med tidig < 25 och bruten trängselregel → MINSKA; `rsRankForra − rsRank ≥ 3` samtidigt som `revQ2` är sektorminimum → BEVAKA med flaggan "pris före fundamenta". **Reglerna är fältbaserade — inga sektornamn i koden.**
- Estimatregler (körs som assertions + rendering): sektorminimum i `revQ2` kan aldrig få ÖKA utan synlig varningsflagga; sektormaximum i `revQ2` med lägst `fwdPE` ska alltid upp i ÖKA-korten.

## Omprövningsvillkor (maskinläsbara)

Varje kort genererar villkor som `{text, test:{falt, op, varde}}` där `falt` är en punktsökväg i snapshoten (t.ex. `sektor.XLE.revQ2.v`). `kontrolleraOmprovning()` utvärderar alla villkor vid varje datainläsning (i drift: dagligen) och visar ✓/✗ per villkor på kortet.

## `data/track_record.json`

Poster: `{id, datum, lins, handling, etf, sektor, ingang:{rsRank, fwdPE, revQ2}, utfall:{v1, v4, v12}, omprovningTriggad, kommentar?}`. Utfall = relativ avkastning mot S&P 500 i procentenheter; `null` = ännu ej mätbart; för MINSKA räknas negativt utfall som träff. Vyn beräknar träffsäkerhet per lins/horisont. Nuvarande innehåll är **illustrativt** och märkt så — skarp loggning appendar poster med samma schema.

## Vad pipelinen ska fylla (senare, utan frontendändring)

| Fält | Källa |
|---|---|
| `sektorer[].revQ2`, `revRiktning` | fredagsparsning FactSet Earnings Insight |
| `sektorer[].relSerie19`, `rsRank` | sektor-ETF-priser (dagliga → veckovisa) mot SPY |
| `sektorer[].flodeM` (+ framtida `flodeV`) | SSGA månadsrapport / VettaFi |
| `aktier[]`, `insynKluster` | SEC EDGAR Form 4 + FI:s insynsregister (Excel-export) |
| `tripwires`, `makro.rantekurva`, `realranta` | FRED |
| `megatrender[]` | bolagsrapporter/branschdata (halvmanuellt, kurerad per tema) |

## `tpa` — TP-acceleration (Aktier-fliken TP-acc)

Enda modulen med **extern körtidskälla**: i LIVE hämtar webbläsaren direkt från datapanelens Supabase (egen kadens — dagliga snapshots vardagar 22:00 UTC, hör inte hemma i 30-minuterssnapshoten). `tpa`-blocket i `sample_data.json` är därför bara **fallback**: det visas när läsnyckeln saknas i `TPA_KONFIG` (index.html), när anropet faller, eller på `file://`.

Blockets form: `{lage, kalla, vintage, fonsterDagar, kommentar, regim:[{id, namn, mekanik, delta5d}], rader:[{ticker, accBp, uppsida, analytiker, dagar}]}`.

- `accBp` = tidsviktad d²TP/dt² normerad mot TP-nivån, i **baspunkter/dag²**. Kanonisk algoritm: `get_target_price_acceleration` i target-price-acceleration-repots `schema.sql` — JS-kopian (`tpaAcceleration`) ska hållas i synk. `null` vid < 3 snapshotdagar (visas som "samlar X/3 d" — förstklassigt tillstånd, inte ett fel).
- `regim[].delta5d` = kvotens förändring i % över ca 5 handelsdagar; `null` när < 2 makrorader finns (visas "—").
- **Mot regimen-flaggan** (⇄): positiv `accBp` i en ticker ur `TPA_AI_KORG` samtidigt som SOX/SPY-deltat är negativt. Okända tickers flaggas aldrig.
- Modulens lineage-badge speglar **TPA-källans** läge, inte sidans globala — Supabase-LIVE kan vara aktiv i TEST-läge och tvärtom.
- Nyckeln i `TPA_KONFIG` ska vara projektets **publishable-nyckel** (publik per design, RLS tillåter endast SELECT) — aldrig service-nyckeln.

## `megatrender[]` — fyra temamatriser (ersätter `megatrend` fr.o.m. Sprint 8)

Lista av teman: `{id, namn, kalla, vintage, kolumner[3], not?, rader[]}`. Radformat:
`{namn, sektor, tema, celler[3]}` där varje cell är `{v, i, pil?, k, n, enhet}`.

- **`n` + `enhet`** är nya: cellens numeriska värde (`null` när ett ärligt tal saknas) och enhet
  (`"pct_aa"` = procent år/år · `"man"` = månader · `"pp"` = procentenheter · `null`).
  Kvalitativa celler har `n: null` och deltar inte i poängen — aldrig hittepå.
- **Kolumnroller är positionella** (0 = kö/backlogg, 1 = ledtid, 2 = marginal/trend) oavsett
  kolumnnamn — poängformlerna i `REGELVERK.md` §3 går på position, visningen på namn.
- Celler med "approx" i `k`-fältet har `i ≤ 3` och ärver approx-märkning i rank-tooltips.
- `rader[].sektor` måste matcha `sektorer[].namn` (konsekvensraden/rekFor kräver det).
- `not` renderas som dämpad rad under temats matris.
- Rad- och temapoäng beräknas i frontenden enligt `REGELVERK.md` §3 — aldrig förberäknade i filen.
- Bakåtkompatibilitet: frontenden läser `megatrender[]` i första hand och faller tillbaka på
  gamla `megatrend`-objektet (visas då som enda temat) tills LIVE-datats kurerade post migrerats.

## `data/screen.json` — konvergensscreenern (daglig pipeline, Sprint 8)

Skrivs av `scripts/screen_data.py` (screen.yml, vardagar 04:23 UTC). Toppnivå:
`{schemaVersion, vintage, genererad, universum:{antal, kalla}, vikter, tackning, rotation, lista[]}`.
Listposter: `{ticker, namn, marknad, sektorId, parlor[], sparade, delpoang{}, styrka, skal, url}` —
`sparade` = antal signaler med underlag (null-signaler krymper nämnaren: "4/6 spårade"),
`delpoang` endast för aktiva signaler, `skal` genereras ur aktiva signalers faktiska siffror.

- Sortering: konvergens → styrka → IN → ES → VÄ (REGELVERK §2). Hela listan, inte topp 5.
- Skrivregel: filen skrivs ENDAST vid ≥ 90 % TK-täckning av universum — annars behålls
  gårdagens fil och orsaken loggas i workflowen. Aldrig en halvtom lista.
- Källbeslut v1 (dokumenterade proxies, aldrig tysta): FL = sektorflöde (proxy),
  VÄ = P/E mot sektorns 10-årssnitt (proxy tills egen historik finns), MA = sektor
  topp 4 på revQ2-/RS-rank (proxy för linserna), ES i FMP-5-dagarsrotation
  (gratisplanens anropstak), BL null för US och tills ~4 v blankningshistorik byggts (SE).
- `data/screen_state.json` är pipelinens minne (EDGAR-accessioncache, FMP-rotation,
  blankningshistorik) — committas av workflowen, läses aldrig av frontenden.
- Frontenden läser filen med egen lineage-badge (`screener v1 · vintage`); saknas filen
  visas kurerade urvalet med ärlig underrad — inga mockade screenerdata i testblocket.

## Sprint 9 — nya filer och ändrade kontrakt

- **`data/pe_historik.json`** — veckovisa trailing-P/E-aggregat per sektor (likaviktade ur
  FMP-rotationen); grund för framtida egenberäknat 10-årssnitt. `fwdPE` i data.json förblir
  kurerad ö tills forward-källa finns.
- **`data/cot_historik.json`** — CFTC COT: E-mini S&P 500 noncommercial netto/OI per rapportvecka;
  `regim.crowding.score` = percentil av senaste mot 3-årsfönstret. BAML-komponenten utgår (stängd källa).
- **`regim.aiCapex`** blir numeriskt (1 + TTM-capex-å/å för `AI_CAPEX_KORG`); detaljer i
  `regim.aiCapexDetalj {capexYoY, korg, ttmMdUsd, kalla, vintage}`. §3-crowdingflaggan läser talet.
- **`makro.bredd`** skrivs av screen-jobbet (US-delen av universum, ≥300 bolag); `makro.sentiment`
  av veckojobbet (AAII-veckostaplarna); `geo.marknader[].relSerie19` av 30-min-jobbet mot ACWI.
- **Track record-utfall**: relativavkastning mot SPY i procentenheter, båda benen i lokal valuta
  (SE-bolag jämförs ocurrency-justerat mot SPY:s procentuella förändring — medvetet val, dokumenterat här).
- **Fältägarskap**: tre jobb skriver data.json (update 30 min · screen dagligen · weekly fredagar),
  vart och ett rör bara sina fält och committar med pull-rebase; merge-principen i update_data.py
  bevarar allt den inte känner.
