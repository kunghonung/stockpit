// Tester för rådata-modellen. Kör: node --test (Node >=20). Inga beroenden.
import { test } from "node:test";
import assert from "node:assert/strict";
import { normalizeHouse, husNyckel, berikaRevisioner, getTpAcceleration } from "../scripts/tp_acceleration.js";

const ALIAS = { morganstanley: "Morgan Stanley", ms: "Morgan Stanley", bofasecurities: "BofA Securities", bankofamerica: "BofA Securities" };

test("husnormalisering: varianter → ett hus, okänt behålls", () => {
  assert.equal(normalizeHouse("Morgan Stanley", ALIAS), "Morgan Stanley");
  assert.equal(normalizeHouse("MS", ALIAS), "Morgan Stanley");
  assert.equal(normalizeHouse("morgan  stanley", ALIAS), "Morgan Stanley");
  assert.equal(normalizeHouse("Bank of America", ALIAS), "BofA Securities");
  assert.equal(normalizeHouse("KeyBanc", ALIAS), "KeyBanc");            // okänt: oförändrat
  assert.equal(normalizeHouse("Morgan Keegan", ALIAS), "Morgan Keegan"); // slås INTE ihop med Morgan Stanley
  assert.equal(husNyckel("J.P. Morgan"), "jpmorgan");
});

test("idempotent dedup på unik nyckel (ticker,house,date,new_target)", () => {
  const rows = [
    { ticker: "X", analyst_house: "A", revision_date: "2026-07-01", new_target: 100 },
    { ticker: "X", analyst_house: "A", revision_date: "2026-07-01", new_target: 100 }, // dubblett
    { ticker: "X", analyst_house: "A", revision_date: "2026-07-01", new_target: 110 }, // annan target = egen rad
  ];
  const nyckel = (r) => [r.ticker, r.analyst_house, r.revision_date, r.new_target].join("|");
  const unika = [...new Map(rows.map((r) => [nyckel(r), r])).values()];
  assert.equal(unika.length, 2);
});

test("berikning: härlett old_target/delta + proveniens, reiteration, första=null", () => {
  const e = berikaRevisioner([
    { id: 1, ticker: "X", analyst_house: "A", revision_date: "2026-06-01", new_target: 100 },
    { id: 2, ticker: "X", analyst_house: "A", revision_date: "2026-06-10", new_target: 120 },
    { id: 3, ticker: "X", analyst_house: "A", revision_date: "2026-06-20", new_target: 120 }, // reiteration
    { id: 4, ticker: "X", analyst_house: "B", revision_date: "2026-06-15", new_target: 90, fmp_prior_target: 80 },
  ]);
  const byId = Object.fromEntries(e.map((r) => [r.id, r]));
  assert.equal(byId[1].old_target, null);           // första posten
  assert.equal(byId[1].old_target_kalla, null);
  assert.equal(byId[1].delta_abs, null);
  assert.equal(byId[2].old_target, 100);
  assert.equal(byId[2].old_target_kalla, "harledd");
  assert.equal(byId[2].delta_abs, 20);
  assert.equal(byId[3].is_reiteration, true);        // samma mål igen
  assert.equal(byId[3].delta_abs, 0);
  assert.equal(byId[4].old_target, 80);              // FMP-prior finns
  assert.equal(byId[4].old_target_kalla, "kalla");
});

test("acc ur rådata mot HANDRÄKNAT facit (konstant hastighet → acc 0)", () => {
  // Ett hus, +10/dag jämnt T.O.M. idag (revision varje dag) ⇒ hastighet konstant ⇒ acc = 0.
  const idag = new Date("2026-07-10T00:00:00Z");
  const rows = [];
  for (let i = 0; i <= 5; i++) {
    const d = new Date(Date.parse("2026-07-05") + i * 86400000).toISOString().slice(0, 10); // 07-05..07-10
    rows.push({ id: i, ticker: "X", analyst_house: "A", revision_date: d, new_target: 100 + i * 10 });
  }
  const r = getTpAcceleration(rows, { pDays: 30, idag });
  assert.ok(Math.abs(r.acceleration) < 1e-6, "konstant hastighet ⇒ acc≈0, fick " + r.acceleration);
  assert.equal(r.n_houses, 1);
});

test("acc mot HANDRÄKNAT facit: tät serie 100,102,106,112 ⇒ acc = +2/dag²", () => {
  // Revision varje dag (ingen trappstegsplatt) ⇒ ren formelmatematik.
  // konsensus/dag: 100,102,106,112 → v: 2,4,6 → a: (4-2)/1, (6-4)/1 = 2,2 → viktat snitt = 2.
  const idag = new Date("2026-07-10T00:00:00Z");
  const mål = { "2026-07-07": 100, "2026-07-08": 102, "2026-07-09": 106, "2026-07-10": 112 };
  const rows = Object.entries(mål).map(([d, tp], i) =>
    ({ id: i, ticker: "X", analyst_house: "A", revision_date: d, new_target: tp }));
  const r = getTpAcceleration(rows, { pDays: 30, idag });
  assert.ok(Math.abs(r.acceleration - 2) < 1e-6, "handräknat +2, fick " + r.acceleration);
  assert.equal(r.n_revisions, 3); // tre delta<>0 (första posten räknas ej)
});

test("gles enskild revision ⇒ värde visas MEN flaggas (largest 100 %), inte tyst", () => {
  // Trappstegsartefakten från få hus är känd — den ska INTE gömmas utan flaggas.
  const idag = new Date("2026-07-20T00:00:00Z");
  const rows = [
    { id: 1, ticker: "X", analyst_house: "A", revision_date: "2026-07-05", new_target: 100 },
    { id: 2, ticker: "X", analyst_house: "A", revision_date: "2026-07-18", new_target: 125 },
  ];
  const r = getTpAcceleration(rows, { pDays: 30, idag });
  assert.equal(r.n_revisions, 1);
  assert.equal(r.largest_single_contribution_pct, 100, "en revision ⇒ ska flaggas som ej kluster");
});

test("net_delta_pct mot HANDRÄKNAT facit: två sänkningar −5 % och −3 % ⇒ −8 (acc oberoende)", () => {
  // Hus A: 100→95 (−5 %). Hus B: 200→194 (−3 %). Baslinjeposterna har delta=null
  // (första posten/hus) och räknas ej. net_delta = −5 + −3 = −8. Två skilda hus, så
  // ingen carry-forward-trappa krånglar till d² — net_delta beräknas oberoende av acc.
  const idag = new Date("2026-07-20T00:00:00Z");
  const rows = [
    { id: 1, ticker: "X", analyst_house: "A", revision_date: "2026-07-01", new_target: 100 },
    { id: 2, ticker: "X", analyst_house: "A", revision_date: "2026-07-10", new_target: 95 },
    { id: 3, ticker: "X", analyst_house: "B", revision_date: "2026-07-02", new_target: 200 },
    { id: 4, ticker: "X", analyst_house: "B", revision_date: "2026-07-12", new_target: 194 },
  ];
  const r = getTpAcceleration(rows, { pDays: 30, idag });
  assert.equal(r.net_delta_pct, -8, "handräknat −8, fick " + r.net_delta_pct);
  assert.equal(r.n_revisions, 2);       // två delta<>0 (baslinjerna räknas ej)
  assert.equal(r.n_houses, 2);
});

test("net_delta_pct: reiteration (delta=0) påverkar inte summan, null vid 0 revisioner", () => {
  const idag = new Date("2026-07-20T00:00:00Z");
  const medRei = getTpAcceleration([
    { id: 1, ticker: "X", analyst_house: "A", revision_date: "2026-07-01", new_target: 100 },
    { id: 2, ticker: "X", analyst_house: "A", revision_date: "2026-07-10", new_target: 90 },  // −10 %
    { id: 3, ticker: "X", analyst_house: "A", revision_date: "2026-07-15", new_target: 90 },  // reiteration, delta 0
  ], { pDays: 30, idag });
  assert.equal(medRei.net_delta_pct, -10, "reiterationen ska inte ändra summan");
  assert.equal(medRei.n_revisions, 1);

  // 0 revisioner i fönstret ⇒ net_delta null (aldrig 0)
  const tomt = getTpAcceleration(
    [{ id: 1, ticker: "X", analyst_house: "A", revision_date: "2026-06-01", new_target: 100 }],
    { pDays: 30, pStaleDays: 180, idag: new Date("2026-08-01T00:00:00Z") });
  assert.equal(tomt.net_delta_pct, null, "0 revisioner ⇒ net_delta null");
});

test("NOLLDATA-SPÄRR: inga revisioner i fönstret ⇒ acc null (aldrig 0)", () => {
  const idag = new Date("2026-08-01T00:00:00Z");
  // enda revision för länge sedan (inom stale men utanför 30d-fönstret)
  const rows = [{ id: 1, ticker: "X", analyst_house: "A", revision_date: "2026-06-01", new_target: 100 }];
  const r = getTpAcceleration(rows, { pDays: 30, pStaleDays: 180, idag });
  assert.equal(r.acceleration, null, "ska vara null, inte 0");
  assert.equal(r.n_revisions, 0);
});

test("STALE-FÖNSTER: dött mål bärs inte med i konsensusen", () => {
  const idag = new Date("2026-08-01T00:00:00Z");
  const rows = [
    { id: 1, ticker: "X", analyst_house: "GAMMAL", revision_date: "2025-01-01", new_target: 500 }, // >180d
    { id: 2, ticker: "X", analyst_house: "A", revision_date: "2026-07-20", new_target: 100 },
    { id: 3, ticker: "X", analyst_house: "A", revision_date: "2026-07-28", new_target: 110 },
  ];
  const r = getTpAcceleration(rows, { pDays: 30, pStaleDays: 180, idag });
  // GAMMAL (500) är utanför stale-fönstret ⇒ current_consensus = bara A:s 110
  assert.equal(r.current_consensus, 110, "dött mål ska inte dra upp snittet");
  assert.equal(r.n_houses_live, 1);
});

test("KLUSTER-FLAGGA: en dominerande revision ⇒ largest ~100 %", () => {
  const idag = new Date("2026-07-20T00:00:00Z");
  const rows = [
    { id: 1, ticker: "X", analyst_house: "A", revision_date: "2026-07-01", new_target: 100 },
    { id: 2, ticker: "X", analyst_house: "A", revision_date: "2026-07-15", new_target: 30 }, // −70, ensam
  ];
  const r = getTpAcceleration(rows, { pDays: 30, idag });
  assert.equal(r.largest_single_contribution_pct, 100, "ensam revision ⇒ 100 %");
  assert.equal(r.n_houses, 1);

  // brett kluster: fyra hus, jämnstora deltan ⇒ largest ~25 %
  const brett = [];
  for (let h = 0; h < 4; h++) {
    brett.push({ id: h * 2, ticker: "Y", analyst_house: "H" + h, revision_date: "2026-07-01", new_target: 100 });
    brett.push({ id: h * 2 + 1, ticker: "Y", analyst_house: "H" + h, revision_date: "2026-07-15", new_target: 110 });
  }
  const rb = getTpAcceleration(brett, { pDays: 30, idag });
  assert.equal(rb.largest_single_contribution_pct, 25, "fyra lika ⇒ 25 %");
  assert.equal(rb.n_houses, 4);
});
