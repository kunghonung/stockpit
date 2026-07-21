# REGELVERK — ranking, vikter och konvergens (v1.0)

Detta dokument definierar hur ALLT som listas i stockpit rankas, vilka vikter som gäller, och hur signalerna konvergerar till köprekommendationer. Det bor i repo-roten bredvid `DATA_SCHEMA.md` och är normerande: renderingen får inte sortera någon lista på annat sätt än vad som står här.

**Fem principer:**

1. **Allt som listas rankas.** Varje panel har en dokumenterad rankningsnyckel, vikter, tie-breakers och null-regel. Osorterade listor är förbjudna.
2. **Fältbaserat.** Reglerna refererar datafält, aldrig namn på sektorer, bolag eller teman (samma princip som linsreglerna).
3. **Null viktas bort, aldrig hittepå.** Saknad komponent → övriga vikter renormeras (samma mekanik som linserna). En rad utan poängbara fält rankas sist och visar "—". **Förfallna fält räknas som null:** ett fält vars vintage passerat 2× sin färskhetsgräns enligt `KADENS.md` behandlas som saknat i alla poäng och linser — sidan rankar aldrig på ruttet data.
4. **Vikter är hypoteser.** Utgångsvikterna nedan är förankrade i forskning/praxis men obevisade i just detta system. De ändras först när `track_record.json` har ≥ 20 utfall per berörd regel, och varje ändring loggas i `NOTES.md` med motivering. (Naiv likaviktning är svårslagen utan bevis — DeMiguel m.fl. 2009.)
5. **Varje rekommendation får maskinläsbara omprövningsvillkor** (befintligt `{text, test:{falt, op, varde}}`-mönster) och loggas i `track_record.json`. Rekommendationer utan omprövningsvillkor är ogiltiga.

## 0. Forskningsbas per signaltyp

| Signal/komponent | Stöd | Nyckelreferenser |
|---|---|---|
| Insynskluster (IN) | Starkt; opportunistiska köp och kluster av flera köpare är mest prediktiva | Cohen, Malloy & Pomorski 2012 (JF) "Decoding Inside Information"; Seyhun 1986/1998 |
| Estimatrevideringar (ES) | Starkt; drift efter revideringar, praxis i Zacks-rank | Chan, Jegadeesh & Lakonishok 1996; Gleason & Lee 2003 |
| Pris-/RS-momentum (TK) | Starkt; 3–12 mån momentum, trendfilter | Jegadeesh & Titman 1993; Carhart 1997; Faber 2007 (10-mån/40-v-snitt) |
| Värdering mot egen historik (VÄ) | Medel; bäst i kombination med momentum | Asness, Moskowitz & Pedersen 2013 "Value and Momentum Everywhere" |
| Fond-/sektorflöden (FL) | Medel; flödestryck driver avkastning på kort/medellång sikt, men trängsel vänder | Coval & Stafford 2007; Lou 2012 |
| Blankningstäckning (BL) | Svagast av de sju; hög blankning är i grunden negativ — endast hög OCH fallande räknas | Asquith, Pathak & Ritter 2005 |
| Makro-/cykelmedvind (MA) | Medel; sektorrotation över konjunktur-/ränteregimer | Conover, Jensen, Johnson & Mercer 2008; branschpraxis (NDR/Fidelity-cykelramverk) |
| Bransch-/temamomentum | Medel–starkt | Moskowitz & Grinblatt 1999 (industrimomentum) |
| Flaskhalsar/prissättningsmakt | Lönsamhet predicerar avkastning; backlogg/ledtider är etablerade konjunkturindikatorer | Novy-Marx 2013 (bruttolönsamhet); ISM-praxis (leverantörsledtider); book-to-bill-praxis |
| Signalräkning (konvergens) | Beprövad form: räkna binära signaler, kombinera svaga signaler brett | Piotroski 2000 (F-score); Grinold & Kahn (aktiva förvaltningens grundlag) |
| Regim-/volatilitetsgate | Volstyrd exponering förbättrar riskjusterat | Moreira & Muir 2017; Faber 2007 |

Varje regel nedan pekar på raderna ovan i stället för att upprepa dem.

## 1. Sektorer (11 st) — två linser *(befintlig, oförändrad — härmed förankrad)*

- **Tidig-cykel (0–100):** eftersläpning 30 % (rsRank inverterad) [värdering/momentum-kombination, Asness 2013], värdering 25 % (rabatt mot `fwdPE10y`) [Asness 2013], revideringar 35 % (`revQ2` + riktningsbonus) [CJL 1996], flödesvändning 10 % [Coval & Stafford 2007]. Makrojustering 2s10s→Finans +8 [Conover 2008].
- **Momentum (0–100):** RS-rank 40 % [J&T 1993], RRG-kvadrant 25 % [MG 1999], flödesandel 20 % [Lou 2012], insynskluster 15 % [CMP 2012].
- **Tabellordning:** default fallande på tidig-cykel (sajtens huvudlins); klick på kolumnrubrik sorterar om — men defaulten är regel.
- **Tie-breakers** (i ordning): högre `revQ2` → större PE-rabatt → lägre rsRank-siffra. **Null-regel:** komponenten viktas bort (befintligt).
- Handlingsregler (ÖKA/MINSKA/BEVAKA) enligt `DATA_SCHEMA.md` — oförändrade.

## 2. Aktier — screener och konvergensidéer

- **Primärnyckel: konvergens** = antal aktiva signaler av 7 (Piotroski-logik: räkna binära kriterier). **Sekundärnyckel: styrka 0–100** = viktad summa av delpoäng.
- **Utgångsvikter:** IN 0,25 · ES 0,20 · TK 0,15 · VÄ 0,15 · BL 0,10 · FL 0,10 · MA 0,05. Motivering: vikt ∝ forskningsstöd (tabell 0) — IN/ES har starkast evidens, BL svagast av de tunga och kräver dessutom fallande trend för att alls aktiveras, MA är grov sektorproxy.
- **Tie-breakers:** högre IN-delpoäng → högre ES-delpoäng → lägre värdering (VÄ-delpoäng). **Null-regel:** signal med källfel = null → viktas bort, konvergensnämnaren minskar ("4/6 spårade" visas då, inte 4/7).
- Kurerade konvergensidéer sorteras med exakt samma nyckel som screenerlistan.

## 3. Megatrender — rad- och temapoäng

Datakrav: varje cell bär `n` (numeriskt värde) + `enhet` (`pct_aa`, `man`, `pp`) där ett tal ärligt finns; kvalitativa celler har `n: null`. Poängen beräknas i frontenden (samma princip som linserna — inget förberäknat i filen).

**Cellpoäng (0–100):**

| Kolumnroll | Formel | Förankring |
|---|---|---|
| Kö/backlogg | `pct_aa`: clamp(n/60)·100 · `man`: clamp(n/48)·100 | Backlogg-/book-to-bill-praxis |
| Ledtid | clamp((n−3)/45)·100 | ISM-leverantörsledtider som prisindikator |
| Marginal-Δ | clamp(n/4)·100 | Novy-Marx 2013 — lönsamhetsexpansion |

Pil-modifierare på cellen: `upp` +10, `ned` −10 (klampat 0–100) — riktningen är del av evidensen (lättande kö ska kosta poäng, se GLP-1).

**Radpoäng** = viktat medel av tillgängliga cellpoäng med vikter **40 % kö / 30 % ledtid / 20 % marginal** (renormeras vid null) × **verifieringsfaktor** `0,85 + 0,15 · (andel celler utan "approx")` × **täckningsfaktor** `0,70 + 0,30 · (andel kolumner med n)`. Täckningsfaktorn finns för att gles data inte ska slå tät: en rad som bara har ledtidssiffra får inte utklassa en rad med kö + ledtid + marginal belagda (svagare evidensbredd → lägre poäng; Grinold & Kahn-logiken igen). Rad helt utan `n` → poäng "—", rankas sist. Rader sorteras fallande inom temat; översta raden är "trängst" och namnges i slutsatsraden.

**Temapoäng** = medel av topp-3 radpoäng **+ breddbonus** 5 × (antal rader ≥ 60, utöver den första; max +15) [Grinold & Kahn: bredd] **− trängselavdrag (crowdingavdrag) 10** om temats crowdingflagga är satt (v1: temat `ai` flaggas när `regim.aiCapex` > 1,3 — capexcykeln är sajtens befintliga överhettningsmått) [Lou 2012]. Teman rankas 1–4; flikordningen i Trångsektor-vyn ÄR rankingen, med poäng och komponentbidrag i tooltip/metodpanel.

## 4. Risk- och marknadspaneler

- **Tripwires:** sortera på procentuellt avstånd till tröskeln, närmast brott först (risk-dashboard-praxis: det som är närmast att smälla står överst). Null-avstånd (saknad tröskel) sist.
- **Cross-asset-kvoter:** sortera på |index − 100| fallande (störst avvikelse från 13 v-snittet = starkast besked), grupperat risk-på-par före risk-av-par vid lika. Pil-inkonsekvens (avvikelse åt ena hållet, 4 v-riktning åt andra) flyttar ned ett steg.
- **Metaller:** status (röd → gul → grön är INTE ordningen — ordna på handlingsrelevans: `oka` → `bevaka` → övrigt), därefter |Δ4v|.
- **Flödespanelen:** sektorstaplar på |flöde M| fallande; regionala noter på |belopp| fallande.
- **Insynsråflödet:** SE: poster rankas på belopp (volym × pris) med klusterboost (≥ 2 poster samma emittent 30 d → gruppera och lyft) [CMP 2012]; US: kronologiskt tills beloppsparsning ur Form 4-XML finns (backlog — märk listan "okviktad").
- **Riskregler ("främsta risker"):** närhet till tröskel × konsekvensvikt (1–3, kurerad per regel). Närmast och tyngst först.

## 5. Track record och TP-acc

- **TP-acc:** rank på `accBp` fallande (det ÄR modulens poäng); tie-breaker uppsida. "Samlar X/3 d"-rader sist, omärkta av rank.
- **Track record:** summeringen rankar linser/regler på träffsäkerhet × √antal, och en regel visas som procent först vid **n ≥ 10** utfall — innan dess visas "x av y" (små-n-disciplin; låt aldrig 2/2 se ut som 100 %).

## 6. Konvergenskedjan → KÖPREK

Kedjan är fyra grindar i ordning. Varje nivå är ett filter — konvergens betyder att SAMMA bolag överlever alla.

1. **Regimgrind:** riskaptit ≥ 50 → köprek tillåtna; 40–49 → endast "BEVAKA-kandidat"; < 40 → inga nya rek, befintliga omprövas [Moreira & Muir 2017; Faber 2007].
2. **Sektorgrind:** bolagets `sektorId` är i ÖKA, eller topp 4 på tidig-cykel-linsen [Conover 2008].
3. **Aktiegrind:** konvergens ≥ 4 av spårade signaler OCH styrka ≥ 60 OCH minst en av IN/ES aktiv (kräv en fundamental drivare — ren teknik räcker inte) [CMP 2012; CJL 1996].
4. **Temabonus (ej grind):** koppling (`aktier[].tema`) till megatrend rankad 1–2 ger +10 konviktionspoäng [MG 1999].

**Konviktionspoäng 0–100** = 0,35 · aktiestyrka + 0,25 · sektorns tidig-cykel-poäng + 0,20 · (konvergens/spårade · 100) + 0,10 · temabonus + 0,10 · regimmarginal (riskaptit − 50, klampad 0–50, ×2). Etiketter: ≥ 70 **KÖPREK · HÖG** · 55–69 **KÖPREK** · uppfyller grindarna men < 55 **KANDIDAT**.

**Spärrar:** max 5 samtidiga KÖPREK (koncentrationsdisciplin; fler = signalen är urvattnad); ny rek ersätter lägst konviktion först; varje rek skapas med omprövningsvillkor (minst: signalbortfall under grind 3, sektor lämnar ÖKA, regim < 40) och en post i `track_record.json` med ingångsvärden (samma schema som i dag: `ingang{rsRank, fwdPE, revQ2}` + konviktion). Utfall mäts relativt S&P 500 på 1/4/12 v — precis som befintliga loggen.

**Valideringsloopen:** track record → träffsäkerhet per signal och grind → viktjustering (princip 4). Systemet blir självkorrigerande i stället för åsiktsstyrt.

## 7. Ändringsdisciplin

- Viktändringar, nya trösklar och nya rankningsnycklar kräver: sprintpost i `NOTES.md` + motivering + track record-underlag (eller explicit "hypotes utan underlag ännu").
- "Aldrig hittepå"-regeln gäller poängen: en rank som bygger på approx-celler ärver märkningen i tooltip.
- Metodpanelen ("Så räknas…") ska alltid länka/sammanfatta detta dokument — UI:t får inte visa en rank som regelverket inte kan förklara.

---

*Regelverket är beslutsstöd för en privat cockpit — systematiska kandidater med öppna vikter och loggade utfall, inte finansiell rådgivning. Referenserna är utgångspunkter för vikthypoteserna, inte bevis för framtida avkastning.*
