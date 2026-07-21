// ============================================================================
// DATAPANEL — daglig dataingest (v1)
//
// Hämtar:
//   1. Makrostängningar från Yahoo Finance (chart-API, ingen nyckel krävs):
//      HYG, TLT, ^SOX, SPY, HG=F (koppar), GC=F (guld) → kvoter till macro_regimes.
//   2. Analytikerkonsensus från Financial Modeling Prep (FMP_API_KEY):
//      riktkurser, antal riktkurser, ratingfördelning → consensus_snapshots.
//
// Skriver med UPSERT till Supabase (service role-nyckel, kringgår RLS).
// Nycklar läses ur .env lokalt (dotenv) och ur GitHub Secrets i CI.
//
// Körning:  node scripts/ingest-data.js
// Miljö:    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, FMP_API_KEY
//           TICKERS (valfri, kommaseparerad — annars demolistan nedan)
//
// Exitkoder: 0 = allt eller delvis lyckat (varningar loggas),
//            1 = både makro och samtliga tickers misslyckades.
// ============================================================================

import "dotenv/config";
import { createClient } from "@supabase/supabase-js";

// ---------- Konfiguration ----------
// Normalisera URL:en — förlåter de vanligaste inklistringsfelen i secrets:
// citattecken runt värdet, "SUPABASE_URL=" med i värdet, saknat https://, avslutande /.
function normaliseraUrl(ravarde) {
  let s = String(ravarde || "").trim()
    .replace(/^["']+|["']+$/g, "")
    .replace(/^SUPABASE_URL\s*=\s*/i, "")
    .replace(/\/+$/, "");
  if (s && !/^https?:\/\//i.test(s)) s = "https://" + s;
  return s;
}
const SUPABASE_URL = normaliseraUrl(process.env.SUPABASE_URL);
const SUPABASE_KEY = (process.env.SUPABASE_SERVICE_ROLE_KEY || "").trim();
const FMP_API_KEY = (process.env.FMP_API_KEY || "").trim();

// Demolista tills panelens universum är definierat — styr med env TICKERS.
const TICKERS = (process.env.TICKERS || "AAPL,MSFT,NVDA")
  .split(",")
  .map((t) => t.trim())
  .filter(Boolean);

const YAHOO_SYMBOLER = {
  hyg: "HYG",
  tlt: "TLT",
  sox: "^SOX",
  spy: "SPY",
  hg: "HG=F",
  gold: "GC=F",
};

const FMP_BAS = "https://financialmodelingprep.com";
const UA = "Mozilla/5.0 (datapanel-ingest/1.0)";

// ---------- Hjälpare ----------
function kravMiljo() {
  const saknas = [];
  if (!SUPABASE_URL) saknas.push("SUPABASE_URL");
  if (!SUPABASE_KEY) saknas.push("SUPABASE_SERVICE_ROLE_KEY");
  if (!FMP_API_KEY) saknas.push("FMP_API_KEY");
  if (saknas.length) {
    console.error("Saknade miljövariabler: " + saknas.join(", ") + " (se .env.example)");
    process.exit(1);
  }
  // Validera URL-formen och beskriv felet utan att skriva ut värdet
  // (Actions maskar ändå secrets i loggen — egenskaper säger mer än ***).
  try {
    new URL(SUPABASE_URL);
  } catch {
    const ra = String(process.env.SUPABASE_URL || "");
    console.error(
      "SUPABASE_URL är ogiltig även efter normalisering. Diagnostik: " +
      "längd=" + ra.length +
      " · börjar med https://=" + /^https:\/\//i.test(ra.trim()) +
      " · innehåller citattecken=" + /["']/.test(ra) +
      " · innehåller blanksteg=" + /\s/.test(ra.trim()) +
      " · innehåller '='=" + ra.includes("=") +
      ". Värdet ska vara exakt https://DITT-PROJEKT.supabase.co"
    );
    process.exit(1);
  }
}

function paus(ms) {
  return new Promise((los) => setTimeout(los, ms));
}

async function hamtaJson(url, beskrivning) {
  const svar = await fetch(url, { headers: { "User-Agent": UA, Accept: "application/json" } });
  if (!svar.ok) {
    throw new Error(beskrivning + " svarade " + svar.status);
  }
  return svar.json();
}

// ---------- Yahoo: senaste stängning per symbol ----------
async function hamtaYahooStangning(symbol) {
  const url =
    "https://query1.finance.yahoo.com/v8/finance/chart/" +
    encodeURIComponent(symbol) +
    "?range=10d&interval=1d";
  const json = await hamtaJson(url, "Yahoo " + symbol);
  const resultat = json?.chart?.result?.[0];
  if (!resultat) throw new Error("Yahoo " + symbol + ": tomt chart-resultat");

  const tider = resultat.timestamp || [];
  const stangningar = resultat.indicators?.quote?.[0]?.close || [];
  // sista icke-null-stängningen
  for (let i = stangningar.length - 1; i >= 0; i--) {
    if (stangningar[i] != null) {
      const datum = new Date(tider[i] * 1000).toISOString().slice(0, 10);
      return { datum, stangning: Number(stangningar[i]) };
    }
  }
  throw new Error("Yahoo " + symbol + ": ingen stängning i svaret");
}

async function ingestaMakro(supabase) {
  const priser = {};
  for (const [nyckel, symbol] of Object.entries(YAHOO_SYMBOLER)) {
    priser[nyckel] = await hamtaYahooStangning(symbol);
    await paus(250);
  }

  // Snapshotdatum = SPY:s senaste handelsdag; avvikande symboldatum loggas
  // (terminer och index kan ligga en dag före/efter ETF:erna).
  const asOfDate = priser.spy.datum;
  for (const [nyckel, p] of Object.entries(priser)) {
    if (p.datum !== asOfDate) {
      console.warn("  obs: " + nyckel + " har datum " + p.datum + " (snapshot " + asOfDate + ")");
    }
  }

  const kvot = (a, b) => (a != null && b != null && b !== 0 ? a / b : null);
  const rad = {
    as_of_date: asOfDate,
    hyg_tlt_ratio: kvot(priser.hyg.stangning, priser.tlt.stangning),
    sox_spy_ratio: kvot(priser.sox.stangning, priser.spy.stangning),
    hg1_xau_ratio: kvot(priser.hg.stangning, priser.gold.stangning),
    hyg_close: priser.hyg.stangning,
    tlt_close: priser.tlt.stangning,
    sox_close: priser.sox.stangning,
    spy_close: priser.spy.stangning,
    hg_close: priser.hg.stangning,
    gold_close: priser.gold.stangning,
    source: "yahoo",
  };

  const { error } = await supabase.from("macro_regimes").upsert(rad, { onConflict: "as_of_date" });
  if (error) throw new Error("Supabase macro_regimes: " + error.message);
  console.log(
    "makro OK (" + asOfDate + "): HYG/TLT " + rad.hyg_tlt_ratio?.toFixed(4) +
    " · SOX/SPY " + rad.sox_spy_ratio?.toFixed(4) +
    " · HG/GC " + rad.hg1_xau_ratio?.toFixed(6)
  );
}

// ---------- FMP: konsensus per ticker ----------
async function hamtaFmp(vag, beskrivning) {
  const skiljetecken = vag.includes("?") ? "&" : "?";
  return hamtaJson(FMP_BAS + vag + skiljetecken + "apikey=" + FMP_API_KEY, beskrivning);
}

async function hamtaKonsensus(ticker) {
  const rad = { ticker, as_of_date: new Date().toISOString().slice(0, 10), source: "fmp" };

  // Riktkurser (kärnan — misslyckas denna hoppar vi över tickern).
  // OBS: /stable/-basen — /api/v3–v4 är stängda för FMP-nycklar utfärdade
  // efter 2025-08-31 ("Legacy Endpoint"-403).
  const konsensus = await hamtaFmp(
    "/stable/price-target-consensus?symbol=" + encodeURIComponent(ticker),
    "FMP price-target-consensus " + ticker
  );
  const k = Array.isArray(konsensus) ? konsensus[0] : konsensus;
  if (!k || k.targetConsensus == null) throw new Error("FMP " + ticker + ": ingen riktkursdata");
  rad.target_consensus = k.targetConsensus;
  rad.target_median = k.targetMedian ?? null;
  rad.target_high = k.targetHigh ?? null;
  rad.target_low = k.targetLow ?? null;

  // Antal riktkurser (senaste kvartalet) — valfri, null vid plangräns
  try {
    const summering = await hamtaFmp(
      "/stable/price-target-summary?symbol=" + encodeURIComponent(ticker),
      "FMP price-target-summary " + ticker
    );
    const s = Array.isArray(summering) ? summering[0] : summering;
    rad.analyst_count = s?.lastQuarter ?? s?.lastQuarterCount ?? null;
  } catch (fel) {
    console.warn("  " + ticker + ": price-target-summary saknas (" + fel.message + ")");
    rad.analyst_count = null;
  }

  // Ratingfördelning — valfri, null vid plangräns
  try {
    const betyg = await hamtaFmp(
      "/stable/grades-consensus?symbol=" + encodeURIComponent(ticker),
      "FMP grades-consensus " + ticker
    );
    const b = Array.isArray(betyg) ? betyg[0] : betyg;
    rad.strong_buy = b?.strongBuy ?? null;
    rad.buy = b?.buy ?? null;
    rad.hold = b?.hold ?? null;
    rad.sell = b?.sell ?? null;
    rad.strong_sell = b?.strongSell ?? null;
  } catch (fel) {
    console.warn("  " + ticker + ": ratingfördelning saknas (" + fel.message + ")");
  }

  // Aktuell kurs — valfri
  try {
    const kurs = await hamtaFmp(
      "/stable/quote-short?symbol=" + encodeURIComponent(ticker),
      "FMP quote-short " + ticker
    );
    rad.price_at_snapshot = Array.isArray(kurs) ? kurs[0]?.price ?? null : null;
  } catch (fel) {
    console.warn("  " + ticker + ": kurs saknas (" + fel.message + ")");
  }

  return rad;
}

async function ingestaKonsensus(supabase) {
  const rader = [];
  for (const ticker of TICKERS) {
    try {
      rader.push(await hamtaKonsensus(ticker));
      console.log("konsensus OK: " + ticker);
    } catch (fel) {
      console.warn("konsensus MISSLYCKADES: " + ticker + " — " + fel.message);
    }
    await paus(350); // respektera FMP:s rate limit
  }
  if (!rader.length) return 0;

  const { error } = await supabase
    .from("consensus_snapshots")
    .upsert(rader, { onConflict: "ticker,as_of_date" });
  if (error) throw new Error("Supabase consensus_snapshots: " + error.message);
  return rader.length;
}

// ---------- Huvudflöde ----------
async function huvud() {
  kravMiljo();
  const supabase = createClient(SUPABASE_URL, SUPABASE_KEY, { auth: { persistSession: false } });

  let makroOk = false;
  let konsensusAntal = 0;

  try {
    await ingestaMakro(supabase);
    makroOk = true;
  } catch (fel) {
    console.error("makro MISSLYCKADES: " + fel.message);
  }

  try {
    konsensusAntal = await ingestaKonsensus(supabase);
    console.log("konsensus: " + konsensusAntal + " av " + TICKERS.length + " tickers sparade");
  } catch (fel) {
    console.error("konsensus MISSLYCKADES: " + fel.message);
  }

  if (!makroOk && konsensusAntal === 0) {
    console.error("Ingenting kunde hämtas — avbryter med felkod.");
    process.exit(1);
  }
  console.log("Ingest klar.");
}

huvud().catch((fel) => {
  console.error("Oväntat fel: " + (fel && fel.stack ? fel.stack : fel));
  process.exit(1);
});
