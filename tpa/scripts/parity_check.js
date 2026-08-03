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
import { analytikerBasandring } from "./tp_acceleration.js";

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
  const { data: raw, error } = await sb.from("tp_acceleration_current")
    .select("ticker, acceleration, current_consensus, n_houses, n_revisions, n_reinit, net_delta_pct, source_stale_days");
  if (error) {
    // Skilj rättighetsfel (grants saknas) från saknad vy (migration ej körd).
    if (error.code === "42501" || /permission denied/i.test(error.message))
      console.error("Rättighetsfel: service_role saknar SELECT på vyn — kör migration 0002_grants_up.sql.\n  (" + error.message + ")");
    else if (error.code === "42P01" || error.code === "PGRST205" || /does not exist|could not find/i.test(error.message))
      console.error("Vyn saknas: kör migration 0001 (och 0002) i SQL-editorn först.\n  (" + error.message + ")");
    else
      console.error("Kunde inte läsa tp_acceleration_current: " + error.message + (error.code ? " [" + error.code + "]" : ""));
    process.exit(1);
  }
  const rawKarta = Object.fromEntries((raw || []).map((r) => [r.ticker, r]));

  const grans = new Date(Date.now() - FONSTER * DAG).toISOString().slice(0, 10);
  const dm = (iso) => iso ? (+iso.slice(8, 10)) + "/" + (+iso.slice(5, 7)) : "?";  // 2026-07-31 → 31/7
  console.log("ticker   rådata-acc   snapshot-acc   diff        net_delta   hus/rev/reinit   källa       snapshot-bas");
  console.log("-------  ----------   ------------   ---------   ---------   --------------   ---------   ------------");
  for (const ticker of TICKERS) {
    const { data: snaps } = await sb.from("consensus_snapshots")
      .select("as_of_date, target_consensus, analyst_count")
      .eq("ticker", ticker).gte("as_of_date", grans).order("as_of_date");
    const punkter = (snaps || []).filter((s) => s.target_consensus != null)
      .map((s) => ({ t: Date.parse(s.as_of_date), tp: +s.target_consensus }));
    const sAcc = snapshotAcc(punkter);
    const r = rawKarta[ticker] || {};
    const rAcc = r.acceleration != null ? +r.acceleration : null;
    const diff = (rAcc != null && sAcc != null) ? (rAcc - sAcc) : null;
    const f = (x) => x == null ? "    —    " : (x >= 0 ? "+" : "") + x.toFixed(4);
    const nd = r.net_delta_pct == null ? "    —    " : (r.net_delta_pct >= 0 ? "+" : "") + (+r.net_delta_pct).toFixed(1) + "%";
    // källfärskhet: dagar sedan senaste revision; markera släp när 0 rev + gammal källa
    const stale = r.source_stale_days == null ? "—" : r.source_stale_days + "d";
    const slap = (r.n_revisions === 0 && r.source_stale_days != null && r.source_stale_days > 45) ? " släpar" : "";
    const hrr = (r.n_houses ?? "—") + "/" + (r.n_revisions ?? "—") + "/" + (r.n_reinit ?? "—");
    // SNAPSHOT-ARTEFAKTVARNING: ändrades analytikerbasen i fönstret? (kompositionsskifte → opålitlig snapshot-acc)
    const bas = analytikerBasandring((snaps || []).map((s) => ({ antal: s.analyst_count, datum: s.as_of_date })), 0);
    const basTxt = bas ? "⚠ " + bas.from + "→" + bas.to + " (" + dm(bas.datum) + ")" : "—";
    console.log(
      ticker.padEnd(7), " ", f(rAcc).padStart(9), "  ", f(sAcc).padStart(9), "  ",
      f(diff).padStart(9), "  ", nd.padStart(9), "  ", hrr.padStart(14), "  ", (stale + slap).padEnd(11), "  ", basTxt);
  }
  console.log("\nSkillnader förväntade — granska trenden över ~30 d innan du flippar TPA_KALLA till 'raw'.");
  console.log("net_delta = riktning (Σdelta% färska 30 d), acc = böjning. reinit = revisioner mot flerårig prior (>365 d),");
  console.log("exkluderade ur net_delta/n_rev (täckningsåterinitiering, ej riktning). 'släpar' = 0 rev men gammal källa, ej tystnad.");
  console.log("snapshot-bas ⚠ = analytikerantalet ändrades i fönstret → snapshot-acc är ett kompositionsskifte, inte en riktkursrörelse (jfr META 3→12).");
}
main();
