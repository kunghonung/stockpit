# KADENS.md — datakadens och färskhetskontrakt (v1.0)

Sidans hela poäng är att hålla sig relevant utan handpåläggning. Detta dokument är kontraktet som garanterar det: **varje fält har en källa, en uppdateringsmekanism, en kadens och en färskhetsgräns — och ett definierat beteende när gränsen passeras.** Det bor i repo-roten bredvid `DATA_SCHEMA.md` och `REGELVERK.md` och är normerande.

**Statuskoder:** `AUTO` = uppdateras automatiskt i dag · `S8` = automatiseras av Sprint 8 (screenern) · `S9` = automatiseras av Sprint 9 (automationssvepet) · `MANUELL Ö` = medvetet kurerad, med åldersvakt — aldrig en tyst manuell lucka.

Ett viktigt förtydligande: **alla beräknade lager är automatiska per definition** — linserna, RRG-matematiken, rad-/temapoängen, alla rankingar (REGELVERK §1–§6) och riskaptiten beräknas i frontenden vid varje datainläsning. Deras färskhet = insatsfältens färskhet. Kontraktet nedan handlar därför om KÄLLFÄLTEN.

## Kadenskontraktet

### Var 30:e minut — `update.yml` → `scripts/update_data.py`

| Fält/panel | Källa | Färskhetsgräns | Status |
|---|---|---|---|
| Räntekurva, 2s10s-not, HY-spread, realränta | FRED | 24 h | AUTO |
| VIX, MOVE (tripwires + riskregler) | Yahoo | 24 h | AUTO |
| Guld, silver, koppar (inkl. 200d-status), olja | Yahoo | 24 h | AUTO |
| Cross-asset-kvoter (alla 7, index mot 13 v) | Yahoo | 24 h | AUTO |
| Sektor-RRG: `relSerie19` + `rsRank` (11 sektorer) | Yahoo veckoserier mot SPY | 24 h | AUTO |
| Insynsråflöde US (`insynFlode.us`) | SEC EDGAR full-text | 24 h | AUTO |
| Insynsråflöde SE (`insynFlode.se`) | FI:s CSV-export | 24 h | AUTO |
| **Geo-RRG (`geo`)** — i dag statisk illustration! | Yahoo mot ACWI (ETF-/indexproxys: ^STOXX, EEM, ^N225, MCHI, SPY, QQQ, INDA) | 24 h | **S9** — läggs in i samma 30-min-jobb med samma relSerie-matte |

### Dagligen vardagar — `daily-ingest.yml` (tpa) + `screen.yml` (S8)

| Fält/panel | Källa | Färskhetsgräns | Status |
|---|---|---|---|
| TP-acc (riktkurser, accBp, TPA-regimkvoter) | FMP + Yahoo → Supabase | 3 d | AUTO |
| Konvergensscreener: hela rankinglistan (`data/screen.json`), inkl. `insynKluster`-underlag | EDGAR + FI + FMP + Yahoo | 3 d | S8 |
| Bredd: S&P > 50d och 200d-andel (`makro.bredd`) — i dag kurerad! | Beräknas ur screenerns dagliga prisserier (samma hämtning som TK-signalen: 1 anrop/ticker ger både veckoserier och SMA50/200) | 3 d | **S9** |
| KÖPREK-loggposter (nya rek + statusändringar) | Rek-motorn speglad i pipelinen (paritetstestad mot frontenden) | 3 d | **S9** |

### Veckovis — nytt `weekly.yml` (S9, fredag kväll efter US-stängning)

| Fält/panel | Källa | Färskhetsgräns | Status |
|---|---|---|---|
| Sektorestimat `revQ2`/`revRiktning` — i dag kurerade | FMP-aggregat per sektor (andel upprevideringar + medelrevidering, cap-viktat över universum). Märks `kalla: "FMP-aggregat (proxy för FactSet EI)"` — FactSet EI förblir referens vid kurering | 10 d | **S9** |
| `fwdPE` per sektor — i dag kurerad | FMP, cap-viktat ur universum | 10 d | **S9** |
| Sentiment (AAII) — i dag kurerad | AAII:s publika undersökningssida (artig hämtning, UA med kontakt); vid fetchfel: behåll föregående + åldersflagga | 14 d | **S9** |
| Crowding/trängsel (`regim.crowding`) — i dag kurerad | CFTC COT (officiell, fri, publiceras fredagar): spekulativ nettopositionering → percentil 0–100. BAML-komponenten utgår ur autoflödet (stängd källa) och noteras i `kalla` | 10 d | **S9** |
| Track record-utfall (`utfall.v1/v4/v12`) | Yahoo: relativavkastning mot SPY från loggpostens datum; `null` tills mätbart | 8 d | **S9** |

### Kvartalsvis — `weekly.yml` räknar, rapportsäsong styr

| Fält/panel | Källa | Färskhetsgräns | Status |
|---|---|---|---|
| AI-capexcykel (`regim.aiCapex`) — i dag kurerad | FMP fundamentals: aggregerad capex å/å TTM för hyperscaler-korgen (fältbaserad tickerlista i konfig) | 120 d | **S9** |

### Medvetna manuella öar — kurerade, med åldersvakt

| Fält/panel | Ritual | Färskhetsgräns | Status |
|---|---|---|---|
| Sektorflöden `flodeM` + `flodenNoter` | Månadsritual (SSGA månadsdata). **S10-utredning:** om SSGA:s flödesrapport finns maskinläsbart (XLSX/CSV) → automatisera; tills det verifierats är detta en ö | 45 d | MANUELL Ö |
| `fwdPE10y` (10-årssnitt per sektor) | Kvartalsritual; kan på sikt ackumuleras ur egen sparad `fwdPE`-historik i stället för Koyfin | 120 d | MANUELL Ö |
| Megatrendceller (`megatrender[].rader`) | Kvartalsritual efter rapportsäsong (backloggar/ledtider/marginaler ur bolagsrapporter). Rad-/temaRANKEN räknas automatiskt ur befintliga celler — det är cellinnehållet som är kurerat. S10-kandidat: Marg-Δ-celler via FMP för namngivna leverantörer | 1 kvartal + 30 d | MANUELL Ö |
| Konvergensidéer `aktier[]` (kurerade topp 5) | Behålls som redaktionellt urval bredvid screenern tills screenern validerats; därefter beslut om avveckling | 30 d | MANUELL Ö (ersätts gradvis av S8) |
| Texter: `parlnyckel`, riskreglernas trösklar, metodtexter | Redaktionellt innehåll, ändras via regelverkets ändringsdisciplin | — | MANUELL (statisk per design) |

## Förfallobeteende (tre nivåer — gäller varje fält med vintage)

1. **Inom färskhetsgränsen:** normal rendering.
2. **Äldre än gränsen:** gul åldersflagga på modulens lineage-badge ("`flodeM` 52 d — förfallen"). Samma mönster som snapshotens befintliga 24 h-varning, generaliserat.
3. **Äldre än 2× gränsen:** fältet behandlas som **null i alla poäng och linser** (viktas bort enligt null-regeln, REGELVERK princip 3) och gråmarkeras "inaktuell" i UI. **Sidan får aldrig ranka på ruttet data** — hellre en ärligt omviktad poäng än en fräsch-låtsande siffra.

Gränserna kodas som en konstant-tabell i frontenden som speglar detta dokument (`KADENSGRANSER`), och en assertion verifierar vid varje datainläsning att tabellen täcker alla fält med vintage.

## Datahälsa-kortet (Översikten)

Översikten får ett kort "Datahälsa" genererat enbart ur befintliga vintage-fält (ingen ny källa): per källgrupp (FRED, Yahoo, EDGAR, FI, FMP/Supabase, SSGA-ritualen, megatrend-ritualen) visas senaste lyckade uppdatering, ålder mot gräns och status grön/gul/grå. Pipelinefel syns därmed på sidan själv inom en kadensperiod — inte bara i Actions-fliken. Befintlig backoff-/felräknarmekanik i `state.json` behålls.

## Garantin, sammanfattad

Efter Sprint 9 gäller: **varje lista på sidan uppdateras antingen automatiskt enligt kadensen ovan, eller är en uttryckligt märkt manuell ö med fungerande åldersvakt och förfallobeteende.** Tysta manuella fält är förbjudna — kontraktet ovan är uttömmande, och assertionen på `KADENSGRANSER` håller det uttömmande över tid.
