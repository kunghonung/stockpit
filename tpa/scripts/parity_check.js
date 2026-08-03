// ============================================================================
// parity_check.js — diffar acc-ur-RÅDATA (vyn tp_acceleration_current) mot
// acc-ur-SNAPSHOTS (klassiska formeln på consensus_snapshots) per ticker.
// Kör under parallellperioden innan panelen byter källa (TPA_KALLA).
//
// Kör: node scripts/parity_check.js   (från tpa/, med .env satt)
// Skillnader är VÄNTADE och bra: snapshot-acc rör sig när stale-hus faller ur
// FMP:s fönster (noll ny info); rådata-acc gör inte det. Se tpa/README.md.
// ============================================================================
import "dotenv/config";
import { createClient } from "@supabase/supabase-js";

const URL = (process.env.SUPABASE_URL || "").trim().replace(/["']/g, "");
const KEY = (process.env.SUPABASE_SERVICE_ROLE_KEY || "").trim();
const TICKERS = (process.env.TICKERS || "AAPL,MSFT,NVDA").split(",").map((t) => t.trim()).filter(Boolean);
const DAG = 86400000, FONSTER = 30;

// Klassiska snapshot-accelerationen (samma formel, indata = consensus_snapshots).
function snapshotAcc(punkter) {
  if (punkter.length < 3) return null;
  const v = [];
  for (let i = 1; i < punkter.length; i++) {
    const dt = (punkter[i].t - punkter[i - 1].t) / DAG;
    if (dt > 0) v.push({ t: punkter[i].t, dt, v: (punkter[i].tp - punkter[i - 1].tp) / dt });
  }
  let s = 0, w = 0; const nu = Date.now();
  for (let i = 1; i < v.length; i++) {
    const snitt = (v[i].dt + v[i - 1].dt) / 2; if (snitt <= 0) continue;
    const a = (v[i].v - v[i - 1].v) / snitt;
    const vikt = Math.max(FONSTER - (nu - v[i].t) / DAG, 1);
    s += a * vikt; w += vikt;
  }
  return w > 0 ? s / w : null;
}

async function main() {
  const sb = createClient(URL, KEY, { auth: { persistSession: false } });
  const { data: raw, error } = await sb.from("tp_acceleration_current").select("ticker, acceleration, current_consensus, n_houses, n_revisions");
  if (error) { console.error("Vyn saknas — kör migrationen först. " + error.message); process.exit(1); }
  const rawKarta = Object.fromEntries((raw || []).map((r) => [r.ticker, r]));

  const grans = new Date(Date.now() - FONSTER * DAG).toISOString().slice(0, 10);
  console.log("ticker   rådata-acc   snapshot-acc   diff        n_hus/n_rev");
  console.log("-------  ----------   ------------   ---------   -----------");
  for (const ticker of TICKERS) {
    const { data: snaps } = await sb.from("consensus_snapshots")
      .select("as_of_date, target_consensus")
      .eq("ticker", ticker).gte("as_of_date", grans).order("as_of_date");
    const punkter = (snaps || []).filter((s) => s.target_consensus != null)
      .map((s) => ({ t: Date.parse(s.as_of_date), tp: +s.target_consensus }));
    const sAcc = snapshotAcc(punkter);
    const r = rawKarta[ticker] || {};
    const rAcc = r.acceleration != null ? +r.acceleration : null;
    const diff = (rAcc != null && sAcc != null) ? (rAcc - sAcc) : null;
    const f = (x) => x == null ? "    —    " : (x >= 0 ? "+" : "") + x.toFixed(4);
    console.log(
      ticker.padEnd(7), " ", f(rAcc).padStart(9), "  ", f(sAcc).padStart(9), "  ",
      f(diff).padStart(9), "  ", (r.n_houses ?? "—") + "/" + (r.n_revisions ?? "—"));
  }
  console.log("\nSkillnader förväntade — granska trenden över ~30 d innan du flippar TPA_KALLA till 'raw'.");
}
main();
