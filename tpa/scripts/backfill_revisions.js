// ============================================================================
// backfill_revisions.js — ENGÅNGSKÖRNING: fyller tp_revisions med hela den
// per-hus-historik FMP price-target-news ger (~20 mån). Idempotent (samma unika
// nyckel som daglig ingest), så den kan köras om utan dubbletter.
//
// Kör: node scripts/backfill_revisions.js   (från tpa/, med .env satt)
// Kräver: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, FMP_API_KEY, TICKERS.
// ============================================================================
import "dotenv/config";
import { createClient } from "@supabase/supabase-js";
import { normalizeHouse } from "./tp_acceleration.js";

const SUPABASE_URL = (process.env.SUPABASE_URL || "").trim().replace(/^SUPABASE_URL=/, "").replace(/["']/g, "");
const SUPABASE_KEY = (process.env.SUPABASE_SERVICE_ROLE_KEY || "").trim();
const FMP_API_KEY = (process.env.FMP_API_KEY || "").trim();
const TICKERS = (process.env.TICKERS || "AAPL,MSFT,NVDA").split(",").map((t) => t.trim()).filter(Boolean);
const LIMIT = Number(process.env.BACKFILL_LIMIT || 1000);   // full historik

const paus = (ms) => new Promise((r) => setTimeout(r, ms));

async function fmpJson(vag) {
  const url = "https://financialmodelingprep.com/stable/" + vag + "&apikey=" + FMP_API_KEY;
  const svar = await fetch(url, { headers: { "User-Agent": "Stockpit/1.0 (kontakt: erik.hjalmarson@gmail.com)" } });
  if (!svar.ok) throw new Error("HTTP " + svar.status);
  return svar.json();
}

async function main() {
  if (!SUPABASE_URL || !SUPABASE_KEY || !FMP_API_KEY) {
    console.error("Saknar SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / FMP_API_KEY."); process.exit(1);
  }
  const supabase = createClient(SUPABASE_URL, SUPABASE_KEY, { auth: { persistSession: false } });
  const { data: aliasRader } = await supabase.from("analyst_house_alias").select("alias_key, canonical");
  const aliasKarta = {};
  for (const r of aliasRader || []) aliasKarta[r.alias_key] = r.canonical;

  let totalt = 0, hus = new Set();
  for (const ticker of TICKERS) {
    try {
      const nyheter = await fmpJson("price-target-news?symbol=" + encodeURIComponent(ticker) + "&limit=" + LIMIT);
      if (!Array.isArray(nyheter) || !nyheter.length) { console.log(ticker + ": inga poster"); await paus(400); continue; }
      const rader = [];
      for (const p of nyheter) {
        const target = p.adjPriceTarget ?? p.priceTarget;
        const h = normalizeHouse(p.analystCompany, aliasKarta);
        if (target == null || !h || !p.publishedDate) continue;
        hus.add(h);
        rader.push({
          ticker, analyst_house: h, analyst_name: p.analystName ?? null,
          revision_date: p.publishedDate.slice(0, 10), published_at: p.publishedDate,
          new_target: target, fmp_prior_target: p.priceTargetPrior ?? null,
          price_when_posted: p.priceWhenPosted ?? null, currency: "USD", source: "FMP",
        });
      }
      // dedup i minnet på unika nyckeln innan upsert (news kan ha exakta dubbletter)
      const seen = new Map();
      for (const r of rader) seen.set([r.ticker, r.analyst_house, r.revision_date, r.new_target].join("|"), r);
      const unika = [...seen.values()];
      const { error } = await supabase.from("tp_revisions")
        .upsert(unika, { onConflict: "ticker,analyst_house,revision_date,new_target", ignoreDuplicates: true });
      if (error) throw new Error(error.message);
      const datum = unika.map((r) => r.revision_date).sort();
      console.log(ticker + ": " + unika.length + " revisioner (" + datum[0] + " → " + datum[datum.length - 1] + ")");
      totalt += unika.length;
    } catch (fel) {
      console.warn(ticker + " MISSLYCKADES: " + fel.message);
    }
    await paus(400);
  }
  console.log("\nBackfill klar: " + totalt + " revisioner, " + hus.size + " unika hus.");
}

main();
