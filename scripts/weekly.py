# -*- coding: utf-8 -*-
"""Veckojobbet (S9, fredagar efter US-stängning) — de långsamma fälten:

  C1  sektorer[].revQ2/revRiktning ur FMP-rotationens riktkurscache (INGA nya
      anrop): andel upprevideringar + medelriktning per sektor, likaviktat.
      PROXY märkt i kalla — analyst-estimates ger 402 på gratisplanen och
      cap-vikter kräver betaldata. FactSet EI förblir kalibreringsreferens.
  C2  pe_historik.json byggs ur samma cache (trailing-P/E-aggregat per sektor).
      sektorer[].fwdPE skrivs INTE över — trailing i ett forward-fält vore
      hittepå; fältet förblir manuell ö tills forward-källa finns (KADENS).
  C3  makro.sentiment ur AAII:s publika sida (defensiv parsning, behåll vid fel).
  C4  regim.crowding ur CFTC COT: E-mini S&P 500 noncommercial nettoposition
      som percentil mot 3 års historik (cot_historik.json byggs ur årszippar
      första gången, därefter veckans deafut-fil).
  C5  regim.aiCapex ur FMP cash-flow (AI_CAPEX_KORG, ~4 anrop) — numeriskt fält
      (1 + å/å-tillväxt) så §3-crowdingflaggan blir fältstyrd på riktigt;
      detaljerna i regim.aiCapexDetalj. Skrivs bara när nytt kvartal syns.
  C6  track_record-utfall: relativavkastning mot SPY i procentenheter,
      lokal valuta (dokumenterat i DATA_SCHEMA), när posterna nått mätålder.

FMP-kvot: hård budget som konstant; loggrad per körning. Varje block i egen
try/except; data.json skrivs enligt fältägarskapsprincipen (bara egna fält).
"""
import io
import json
import os
import re
import sys
import time
import zipfile
import urllib.request
import urllib.error
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROT = Path(__file__).resolve().parent.parent
DATAFIL = ROT / "data" / "data.json"
STATEFIL = ROT / "data" / "state.json"
SCREENSTATE = ROT / "data" / "screen_state.json"
UNIVERSUM = ROT / "data" / "universum.json"
TRACKFIL = ROT / "data" / "track_record.json"
PE_HISTORIK = ROT / "data" / "pe_historik.json"
COT_HISTORIK = ROT / "data" / "cot_historik.json"
UA = "Stockpit/1.0 (privat marknadspanel; kontakt: erik.hjalmarson@gmail.com)"
FMP_NYCKEL = os.environ.get("FMP_API_KEY", "")
FMP_BUDGET = 30          # veckojobbets tak — C5 använder ~4, resten är marginal
AI_CAPEX_KORG = ["MSFT", "GOOGL", "AMZN", "META"]  # fältbaserad, redigerbar
fmp_anvant = 0


def hamta(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as svar:
        return svar.read()


def las_json(p, fallback):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return fallback


def skriv_json(p, obj, indent=2):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")


def fmp(endpoint, params):
    global fmp_anvant
    if fmp_anvant >= FMP_BUDGET:
        raise RuntimeError("FMP-budgeten (%d) förbrukad" % FMP_BUDGET)
    fmp_anvant += 1
    url = ("https://financialmodelingprep.com/stable/%s?%s&apikey=%s"
           % (endpoint, urllib.parse.urlencode(params), FMP_NYCKEL))
    return json.loads(hamta(url).decode("utf-8", errors="replace"))


# ---------------------------------------------------------------- C1: estimatproxy
def estimat_aggregat(data, state, idag):
    ss = las_json(SCREENSTATE, {})
    uni = las_json(UNIVERSUM, {"bolag": []})
    sektor_av = {b["ticker"]: b.get("sektorId") for b in uni["bolag"]}
    per_sektor = {}
    for tick, es in (ss.get("es") or {}).items():
        r = es.get("riktning")
        sid = sektor_av.get(tick)
        if r is None or not sid:
            continue
        per_sektor.setdefault(sid, []).append(r)
    if not per_sektor:
        print("C1: ingen riktkurshistorik i rotationscachen ännu — hoppar (ES byggs organiskt).")
        return
    forra = state.get("revAggregat", {})
    nya_agg = {}
    for s in data["sektorer"]:
        rikt = per_sektor.get(s["id"])
        if not rikt or len(rikt) < 3:
            continue  # < 3 bolag → för glest, rör inte fältet
        medel = sum(rikt) / len(rikt)
        nya_agg[s["id"]] = round(medel, 2)
        gammalt = forra.get(s["id"])
        if gammalt is None:
            riktning = "stiger" if medel > 0.5 else "faller" if medel < -0.5 else "flat"
        else:
            d = medel - gammalt
            riktning = ("accelererar" if d > 0.5 and medel > 0 else
                        "stiger" if medel > 0.5 else
                        "vänder" if d > 0.5 else
                        "faller" if medel < -0.5 else "flat")
        s["revQ2"] = {"v": round(medel, 1),
                      "kalla": "FMP-riktkursaggregat 90 d, %d bolag likaviktat (proxy för FactSet EI)" % len(rikt),
                      "vintage": idag.isoformat()}
        s["revRiktning"] = riktning
    state["revAggregat"] = nya_agg
    print("C1: revQ2-proxy för %d sektorer." % len(nya_agg))


# ---------------------------------------------------------------- C2: PE-historik
def pe_historik(data, idag):
    ss = las_json(SCREENSTATE, {})
    uni = las_json(UNIVERSUM, {"bolag": []})
    sektor_av = {b["ticker"]: b.get("sektorId") for b in uni["bolag"]}
    per_sektor = {}
    for tick, va in (ss.get("va") or {}).items():
        pe = va.get("pe")
        sid = sektor_av.get(tick)
        if pe and 0 < pe < 200 and sid:
            per_sektor.setdefault(sid, []).append(pe)
    if not per_sektor:
        print("C2: ingen P/E-cache ännu.")
        return
    hist = las_json(PE_HISTORIK, {"kommentar": "Veckovisa trailing-P/E-aggregat per sektor "
                                  "(likaviktade ur FMP-rotationen) — grund för framtida egenberäknat "
                                  "10-årssnitt. fwdPE i data.json förblir kurerat tills forward-källa finns.",
                                  "veckor": {}})
    hist["veckor"][idag.isoformat()] = {
        sid: round(sum(v) / len(v), 1) for sid, v in per_sektor.items() if len(v) >= 3}
    skriv_json(PE_HISTORIK, hist)
    print("C2: pe_historik +%s (%d sektorer)." % (idag.isoformat(), len(hist["veckor"][idag.isoformat()])))


# ---------------------------------------------------------------- C3: AAII
def sentiment_aaii(data, idag):
    try:
        html = hamta("https://www.aaii.com/sentimentsurvey").decode("utf-8", errors="replace")
    except urllib.error.HTTPError as fel:
        if fel.code != 403:
            raise
        # AAII 403:ar identifierande UA:n; en (1) artig retry med browserprofil per vecka
        req = urllib.request.Request(
            "https://www.aaii.com/sentimentsurvey",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                     "Accept": "text/html,application/xhtml+xml",
                     "Accept-Language": "en-US,en;q=0.8"})
        with urllib.request.urlopen(req, timeout=45) as svar:
            html = svar.read().decode("utf-8", errors="replace")
    # veckostaplarna: <div class="bar bullish" style="width:44.9%">44.9%</div>
    # — första förekomsten per klass är senaste veckan; datumet står strax före.
    tal = {}
    for namn in ("bullish", "neutral", "bearish"):
        m = re.search(r'class="bar %s"\s+style="width:(\d{1,2}(?:\.\d)?)%%"' % namn, html)
        if m:
            tal[namn] = float(m.group(1))
    vecka = re.search(r'class="date">\s*(\d{1,2})/(\d{1,2})/(\d{4})', html)
    if len(tal) != 3 or not (80 <= sum(tal.values()) <= 120):
        raise ValueError("AAII-parsningen gav orimligt resultat: %s — behåller föregående." % tal)
    sn = data.setdefault("makro", {}).setdefault("sentiment", {})
    sn["bull"] = tal["bullish"]
    sn["neutral"] = tal["neutral"]
    sn["bear"] = tal["bearish"]
    sn["spread"] = round(tal["bullish"] - tal["bearish"], 1)
    sn["kalla"] = "AAII Sentiment Survey (publika sidan, veckostaplarna)"
    sn["vintage"] = ("%s-%02d-%02d" % (vecka.group(3), int(vecka.group(1)), int(vecka.group(2)))
                     if vecka else idag.isoformat())
    print("C3: AAII bull %s / neutral %s / bear %s (v. %s)."
          % (tal["bullish"], tal["neutral"], tal["bearish"], sn["vintage"]))


# ---------------------------------------------------------------- C4: COT
def crowding_cot(data, idag):
    hist = las_json(COT_HISTORIK, {"kommentar": "CFTC COT legacy futures: E-mini S&P 500 "
                                   "noncommercial nettoposition/open interest per rapportvecka.",
                                   "poster": {}})
    def parsa_rader(text):
        ut = {}
        for rad in text.splitlines():
            if "E-MINI S&P 500" not in rad.upper() or "CONSOLIDATED" in rad.upper():
                continue
            f = rad.split(",")
            try:
                datum = f[2].strip().strip('"')
                oi = float(f[7])
                netto = (float(f[8]) - float(f[9])) / oi if oi else None
                if netto is not None and re.match(r"^\d{4}-\d{2}-\d{2}$", datum):
                    ut[datum] = round(netto, 4)
            except Exception:
                continue
        return ut
    if len(hist["poster"]) < 100:  # första körningen: bygg 3 års historik ur årszippar
        for ar in (idag.year - 2, idag.year - 1, idag.year):
            try:
                z = zipfile.ZipFile(io.BytesIO(hamta(
                    "https://www.cftc.gov/files/dea/history/deacot%d.zip" % ar, timeout=120)))
                text = z.read(z.namelist()[0]).decode("utf-8", errors="replace")
                hist["poster"].update(parsa_rader(text))
                time.sleep(1)
            except Exception as fel:
                print("C4: årsfil %d: %s" % (ar, fel))
    try:
        text = hamta("https://www.cftc.gov/dea/newcot/deafut.txt", timeout=60).decode("utf-8", errors="replace")
        hist["poster"].update(parsa_rader(text))
    except Exception as fel:
        print("C4: veckofil: %s" % fel)
    if len(hist["poster"]) < 50:
        raise ValueError("COT-historiken för tunn (%d poster) — rör inte crowding." % len(hist["poster"]))
    datum_sort = sorted(hist["poster"])
    grans = (idag - timedelta(days=3 * 365)).isoformat()
    fönster = [hist["poster"][d] for d in datum_sort if d >= grans]
    senaste = hist["poster"][datum_sort[-1]]
    percentil = round(sum(1 for v in fönster if v <= senaste) / len(fönster) * 100)
    cr = data.setdefault("regim", {}).setdefault("crowding", {})
    cr["score"] = percentil
    cr.setdefault("komfort", 60)
    cr["komponenter"] = [{"namn": "E-mini S&P 500 noncommercial netto",
                          "varde": "%.1f %% av OI · percentil %d/100 (3 år)" % (senaste * 100, percentil),
                          "kalla": "CFTC COT " + datum_sort[-1]}]
    cr["kalla"] = "CFTC COT (percentil 3 år). BAML-komponenten utgår ur autoflödet (stängd källa)."
    cr["vintage"] = idag.isoformat()
    skriv_json(COT_HISTORIK, hist)
    print("C4: crowding %d/100 (netto %.1f %%, %d veckor i fönstret)." % (percentil, senaste * 100, len(fönster)))


# ---------------------------------------------------------------- C5: AI-capex
def ai_capex(data, state, idag):
    if not FMP_NYCKEL:
        raise ValueError("FMP_API_KEY saknas")
    summa_nu, summa_fjol, senaste_kvartal = 0.0, 0.0, None
    for tick in AI_CAPEX_KORG:
        rader = fmp("cash-flow-statement", {"symbol": tick, "period": "quarter", "limit": 8})
        if not isinstance(rader, list) or len(rader) < 8:
            raise ValueError("%s: färre än 8 kvartal ur FMP" % tick)
        capex = [abs(float(r.get("capitalExpenditure") or 0)) for r in rader]
        summa_nu += sum(capex[:4])
        summa_fjol += sum(capex[4:8])
        kv = rader[0].get("date")
        if senaste_kvartal is None or (kv and kv > senaste_kvartal):
            senaste_kvartal = kv
        time.sleep(0.3)
    if not summa_fjol:
        raise ValueError("fjolårscapex 0 — orimligt")
    if state.get("aiCapexKvartal") == senaste_kvartal:
        print("C5: samma kvartal (%s) som senast — ingen ändring." % senaste_kvartal)
        return
    yoy = (summa_nu / summa_fjol - 1) * 100
    data.setdefault("regim", {})["aiCapex"] = round(1 + yoy / 100, 2)
    data["regim"]["aiCapexDetalj"] = {
        "capexYoY": round(yoy, 1), "korg": AI_CAPEX_KORG, "ttmMdUsd": round(summa_nu / 1e9),
        "kalla": "FMP cash-flow TTM å/å, korg " + "+".join(AI_CAPEX_KORG),
        "vintage": senaste_kvartal or idag.isoformat()}
    state["aiCapexKvartal"] = senaste_kvartal
    print("C5: aiCapex %.2f (capex %+.1f %% å/å, t.o.m. %s)." % (1 + yoy / 100, yoy, senaste_kvartal))


# ---------------------------------------------------------------- C6: track-utfall
def track_utfall(idag):
    tr = las_json(TRACKFIL, None)
    uni = las_json(UNIVERSUM, {"bolag": []})
    symbol_av = {b["ticker"]: b["yahooSymbol"] for b in uni["bolag"]}
    if not tr:
        return
    ETF_SYMBOL = {}  # sektor-ETF:er är sina egna Yahoo-symboler
    andrade = 0
    spy_cache = {}
    def stangning_kring(symbol, datumstr):
        d = date.fromisoformat(datumstr)
        p1 = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d"
               % (urllib.parse.quote(symbol), p1 - 5 * 86400, p1 + 3 * 86400))
        r = json.loads(hamta(url).decode("utf-8"))["chart"]["result"][0]
        c = [v for v in r["indicators"]["quote"][0]["close"] if v is not None]
        return c[-1] if c else None
    def avkastning(symbol, fran, till_dagar):
        start = stangning_kring(symbol, fran)
        slut_datum = (date.fromisoformat(fran) + timedelta(days=till_dagar)).isoformat()
        slut = stangning_kring(symbol, slut_datum)
        time.sleep(0.4)
        if start and slut:
            return (slut / start - 1) * 100
        return None
    for p in tr.get("poster", []):
        symbol = symbol_av.get(p.get("etf")) or p.get("etf")
        for falt, dagar in (("v1", 7), ("v4", 28), ("v12", 84)):
            if p["utfall"].get(falt) is not None:
                continue
            if (idag - date.fromisoformat(p["datum"])).days < dagar + 1:
                continue
            try:
                a = avkastning(symbol, p["datum"], dagar)
                spy = avkastning("SPY", p["datum"], dagar)
                if a is not None and spy is not None:
                    p["utfall"][falt] = round(a - spy, 1)
                    andrade += 1
            except Exception as fel:
                print("C6: %s %s: %s" % (p.get("etf"), falt, fel))
    if andrade:
        skriv_json(TRACKFIL, tr)
    print("C6: %d utfall ifyllda." % andrade)


# ---------------------------------------------------------------- huvudflöde
def main():
    idag = date.today()
    data = las_json(DATAFIL, None)
    state = las_json(STATEFIL, {})
    if not data:
        print("data.json saknas")
        sys.exit(1)
    # Sidofilsprincipen (lärdom 2026-07-22): weekly skriver ALDRIG data.json —
    # ändringarna samlas här och mergas av 30-min-jobbet via data/vecko.json.
    fore = json.loads(json.dumps({"sektorer": [{"id": s["id"], "revQ2": s.get("revQ2"),
                                                "revRiktning": s.get("revRiktning")} for s in data["sektorer"]],
                                  "sentiment": data.get("makro", {}).get("sentiment"),
                                  "crowding": data.get("regim", {}).get("crowding"),
                                  "aiCapex": data.get("regim", {}).get("aiCapex"),
                                  "aiCapexDetalj": data.get("regim", {}).get("aiCapexDetalj")}))
    lyckade, fallerade = [], []
    for namn, fn in (("C1 estimatproxy", lambda: estimat_aggregat(data, state, idag)),
                     ("C2 pe-historik", lambda: pe_historik(data, idag)),
                     ("C3 AAII", lambda: sentiment_aaii(data, idag)),
                     ("C4 COT-crowding", lambda: crowding_cot(data, idag)),
                     ("C5 AI-capex", lambda: ai_capex(data, state, idag)),
                     ("C6 track-utfall", lambda: track_utfall(idag))):
        try:
            fn()
            lyckade.append(namn)
        except Exception as fel:
            fallerade.append(namn)
            print("MISSLYCKADES: %s — %s" % (namn, fel))
    vecko = {"genererad": datetime.now(timezone.utc).isoformat(timespec="minutes")}
    for s in data["sektorer"]:
        f = next(x for x in fore["sektorer"] if x["id"] == s["id"])
        if s.get("revQ2") != f["revQ2"] or s.get("revRiktning") != f["revRiktning"]:
            vecko.setdefault("sektorRev", {})[s["id"]] = {"revQ2": s["revQ2"], "revRiktning": s["revRiktning"]}
    if data.get("makro", {}).get("sentiment") != fore["sentiment"]:
        vecko["sentiment"] = data["makro"]["sentiment"]
    if data.get("regim", {}).get("crowding") != fore["crowding"]:
        vecko["crowding"] = data["regim"]["crowding"]
    if data.get("regim", {}).get("aiCapex") != fore["aiCapex"]:
        vecko["aiCapex"] = data["regim"]["aiCapex"]
        vecko["aiCapexDetalj"] = data["regim"].get("aiCapexDetalj")
    if len(vecko) > 1:
        skriv_json(ROT / "data" / "vecko.json", vecko)
        print("vecko.json: %s" % ", ".join(k for k in vecko if k != "genererad"))
    skriv_json(STATEFIL, state)
    print("FMP: %d/%d anrop. Klart: %d block OK, %d fallerade."
          % (fmp_anvant, FMP_BUDGET, len(lyckade), len(fallerade)))
    if not lyckade:
        sys.exit(1)


if __name__ == "__main__":
    main()
