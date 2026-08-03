// ============================================================================
// tp_acceleration.js — KANONISK referens (JS) av samma beräkning som SQL:en
// get_tp_acceleration i migration 0001. Används av testerna och paritetsjobbet.
// Håll i synk med SQL-funktionen — SQL:en är sanningskällan i drift, denna är
// den testbara spegeln (samma mönster som get_target_price_acceleration ↔ tpaAcceleration).
// ============================================================================

const DAG_MS = 86400000;

export function husNyckel(raw) {
  return String(raw || "").trim().toLowerCase().replace(/[^a-z0-9]/g, "");
}

// Konservativ husnormalisering: alias-träff → canonical, annars rånamnet trimmat.
export function normalizeHouse(raw, aliasKarta) {
  const k = husNyckel(raw);
  if (aliasKarta && aliasKarta[k]) return aliasKarta[k];
  const t = String(raw || "").trim();
  return t || null;
}

// Berikar revisioner med old_target/delta/proveniens via "lag" per hus.
// Indata: rader {ticker, analyst_house, revision_date (YYYY-MM-DD), published_at?, new_target, fmp_prior_target?}
export function berikaRevisioner(rader) {
  const perHus = new Map();
  for (const r of rader) {
    const nyckel = r.ticker + "|" + r.analyst_house;
    if (!perHus.has(nyckel)) perHus.set(nyckel, []);
    perHus.get(nyckel).push(r);
  }
  const ut = [];
  for (const grupp of perHus.values()) {
    grupp.sort((a, b) =>
      a.revision_date.localeCompare(b.revision_date) ||
      String(a.published_at || "").localeCompare(String(b.published_at || "")) ||
      (a.id || 0) - (b.id || 0));
    let forra = null;
    for (const r of grupp) {
      const old_target = r.fmp_prior_target != null ? r.fmp_prior_target : forra;
      const kalla = r.fmp_prior_target != null ? "kalla" : (forra != null ? "harledd" : null);
      const delta_abs = old_target != null ? r.new_target - old_target : null;
      const delta_pct = (old_target != null && old_target > 0)
        ? Math.round((r.new_target / old_target - 1) * 100 * 100) / 100 : null;
      ut.push({
        ...r, old_target, old_target_kalla: kalla, delta_abs, delta_pct,
        is_reiteration: old_target != null && r.new_target === old_target,
      });
      forra = r.new_target;
    }
  }
  return ut;
}

function datumStrang(d) { return d.toISOString().slice(0, 10); }

// Carry-forward-konsensus för en dag: medel över hus av husets senaste new_target
// med revision_date <= dag OCH revision_date > dag - staleDays.
function konsensusForDag(rader, dagStr, staleDays) {
  const grans = datumStrang(new Date(Date.parse(dagStr) - staleDays * DAG_MS));
  const senastePerHus = new Map();
  for (const r of rader) {
    if (r.revision_date <= dagStr && r.revision_date > grans) {
      const b = senastePerHus.get(r.analyst_house);
      const nyckel = r.revision_date + "|" + String(r.published_at || "") + "|" + String(r.id || 0);
      if (!b || nyckel > b.nyckel) senastePerHus.set(r.analyst_house, { tgt: r.new_target, nyckel });
    }
  }
  if (senastePerHus.size === 0) return { consensus: null, houses_live: 0 };
  let s = 0; for (const v of senastePerHus.values()) s += v.tgt;
  return { consensus: s / senastePerHus.size, houses_live: senastePerHus.size };
}

// Bevarar EXAKT get_target_price_acceleration-formeln på carry-forward-serien.
// idag = Date (default nu). Returnerar samma fält som SQL-funktionen.
export function getTpAcceleration(rawRader, { pDays = 30, pStaleDays = 180, idag = new Date() } = {}) {
  const idagStr = datumStrang(idag);
  const rader = rawRader.filter((r) => r.new_target != null);

  // daglig serie
  const dagar = [];
  for (let i = pDays; i >= 0; i--) dagar.push(datumStrang(new Date(Date.parse(idagStr) - i * DAG_MS)));
  const pts = [];
  let husLiveIdag = 0;
  for (const dagStr of dagar) {
    const { consensus, houses_live } = konsensusForDag(rader, dagStr, pStaleDays);
    if (dagStr === idagStr) husLiveIdag = houses_live;
    if (consensus != null) pts.push({ d: dagStr, consensus });
  }
  // d1, d2, tidsvikt (identiskt med SQL)
  const d1 = [];
  for (let i = 0; i < pts.length; i++) {
    if (i === 0) { d1.push({ d: pts[i].d, dt: null, v: null }); continue; }
    const dt = (Date.parse(pts[i].d) - Date.parse(pts[i - 1].d)) / DAG_MS;
    d1.push({ d: pts[i].d, dt, v: dt ? (pts[i].consensus - pts[i - 1].consensus) / dt : null });
  }
  const viktat = [];
  for (let i = 0; i < d1.length; i++) {
    if (d1[i].v == null || i === 0 || d1[i - 1].v == null) continue;
    const medel = (d1[i].dt + d1[i - 1].dt) / 2;
    if (!medel) continue;
    const a = (d1[i].v - d1[i - 1].v) / medel;
    const vikt = Math.max(pDays - (Date.parse(idagStr) - Date.parse(d1[i].d)) / DAG_MS, 1);
    viktat.push({ a, vikt });
  }

  // metadata ur berikade revisioner i fönstret (delta<>0)
  const berikade = berikaRevisioner(rader);
  const fonsterGrans = datumStrang(new Date(Date.parse(idagStr) - pDays * DAG_MS));
  const rev = berikade.filter((e) =>
    e.ticker != null && e.revision_date > fonsterGrans && e.delta_abs != null && e.delta_abs !== 0);
  const nrev = rev.length;
  const nhouses = new Set(rev.map((e) => e.analyst_house)).size;
  const totalAbs = rev.reduce((s, e) => s + Math.abs(e.delta_abs), 0);
  const largest = totalAbs > 0
    ? Math.round(Math.max(...rev.map((e) => Math.abs(e.delta_abs))) / totalAbs * 100 * 10) / 10 : null;

  // NOLLDATA-SPÄRR: 0 revisioner → acceleration null (aldrig 0)
  let summaVikt = 0, summaAV = 0;
  for (const x of viktat) { summaVikt += x.vikt; summaAV += x.a * x.vikt; }
  const acceleration = (nrev >= 1 && viktat.length >= 1 && summaVikt > 0)
    ? Math.round(summaAV / summaVikt * 1e6) / 1e6 : null;

  return {
    acceleration,
    current_consensus: pts.length && pts[pts.length - 1].d === idagStr ? pts[pts.length - 1].consensus : null,
    n_revisions: nrev,
    n_houses: nhouses,
    n_houses_live: husLiveIdag,
    largest_single_contribution_pct: largest,
    points: pts.length,
  };
}
