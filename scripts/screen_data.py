# -*- coding: utf-8 -*-
"""Konvergensscreenern — daglig poängsättning av hela universum (data/universum.json)
mot de sju signalerna i parlnyckel. Output: data/screen.json (+ data/screen_state.json).

REGELVERK.md §2 styr vikter och sortering. Null-regeln genomsyrar allt:
källfel/saknat underlag => signalen null => viktas bort, konvergensnämnaren minskar.

Käll- och budgetbeslut (v1 — dokumenterade avvikelser, inte tysta):
  TK  Yahoo veckoserier range=1y (briefens 8 mo räcker inte för 40 v-snittet).
      RS mot SPY (US) resp. ^OMX (SE). Aktiv: pris > SMA40 OCH relpris > SMA12.
  IN  US: EDGAR submissions + Form 4-XML (transaktionskod P), cache per accession
      i screen_state.json så bara nya dokument hämtas. SE: FI:s insyns-CSV,
      EN export per körning (samma mönster som update_data.py), "Förvärv".
  ES  FMP price-target-consensus i 5-dagarsrotation (gratisplanens ~250 anrop/dag
      räcker inte för 543 bolag dagligen). Riktkursriktningen byggs organiskt ur
      state-historiken — ES är null tills ≥2 observationer ≥14 dagar isär finns.
  VÄ  FMP quote (P/E) mot sektorns fwdPE10y ur data/data.json — SEKTORPROXY:
      "egen 5-årsmedian" kräver betald historik; byts när sådan finns. Märkt i skal.
  FL  Sektorns flodeM ur data/data.json (sektorproxy per brief).
  MA  Sektor i topp 4 på revQ2-rank ELLER RS-rank — proxy för linserna tills
      sektorpoängen exponeras i datat (full portering = dubblerad logik utan vakt).
  BL  SE: FI:s blankningsregister (ODS, stdlib-parsad); trenden (fallande 4 v)
      byggs organiskt i state — BL är null tills ~4 veckors historik finns.
      US: FMP-gratis saknar short interest => null.

Skrivregel: screen.json skrivs ENDAST om >=90 % av universum kunde poängsättas
(TK-täckning som bas); annars behålls gårdagens fil och orsaken loggas.
"""
import csv
import io
import json
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
UNIVERSUM = ROT / "data" / "universum.json"
DATAFIL = ROT / "data" / "data.json"
UTFIL = ROT / "data" / "screen.json"
STATEFIL = ROT / "data" / "screen_state.json"
UA = "Stockpit/1.0 (privat marknadspanel; kontakt: erik.hjalmarson@gmail.com)"

import os
FMP_NYCKEL = os.environ.get("FMP_API_KEY", "")

VIKTER = {"IN": 0.25, "ES": 0.20, "TK": 0.15, "VÄ": 0.15, "BL": 0.10, "FL": 0.10, "MA": 0.05}
ROTATIONSDELAR = 5          # ES/VÄ-rotation: full FMP-täckning var 5:e vardag
ES_FARSK_DAGAR = 7          # cachade FMP-delpoäng räknas färska så länge
IN_FONSTER_DAGAR = 30
IN_DIPP_PROCENT = 15
BL_TROSKEL = 2.0            # aggregerad blankning > 2 %
TACKNINGSKRAV = 0.90
YAHOO_PAUS = 0.4
EDGAR_PAUS = 0.13           # ≤ 8 req/s med marginal


def hamta(url, timeout=30, headers=None):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as svar:
        return svar.read()


def hamta_json(url, timeout=30):
    return json.loads(hamta(url, timeout).decode("utf-8", errors="replace"))


def las_json(p, fallback):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return fallback


def klamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- Yahoo (TK + bredd)
def yahoo_dagserie(symbol):
    """1 år dagliga stängningar — EN hämtning per ticker ger både veckoserien
    (TK-signalen) och SMA50/200 (breddmåttet, S9-B1)."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s"
           "?range=1y&interval=1d&events=div%%2Csplit" % urllib.parse.quote(symbol))
    d = hamta_json(url)
    res = d["chart"]["result"][0]
    return [v for v in res["indicators"]["quote"][0]["close"] if v is not None]


def veckoserie_ur_dagar(dagar):
    """Var 5:e handelsdag räknat bakifrån — senaste punkten alltid med."""
    return dagar[::-1][::5][::-1]


def sma(serie, n):
    if len(serie) < n:
        return None
    return sum(serie[-n:]) / n


def tk_signal(stang, indexserie):
    """Aktiv: pris > SMA40 och relpris över sitt SMA12 (sektorlinsens matte)."""
    if len(stang) < 41 or len(indexserie) < 41:
        return None
    n = min(len(stang), len(indexserie))
    rel = [stang[-n + i] / indexserie[-n + i] for i in range(n)]
    s40 = sma(stang, 40)
    r12 = sma(rel, 12)
    if s40 is None or r12 is None:
        return None
    avst12 = (rel[-1] / r12 - 1) * 100
    aktiv = stang[-1] > s40 and avst12 > 0
    delpoang = klamp(50 + avst12 * 5)
    topp52 = max(stang)
    dipp = (topp52 - stang[-1]) / topp52 * 100 if topp52 else 0
    return {"aktiv": aktiv, "delpoang": round(delpoang), "avst12": round(avst12, 1),
            "over40v": stang[-1] > s40, "dipp": round(dipp, 1)}


# ---------------------------------------------------------------- EDGAR (IN, US)
def edgar_in_us(bolag, state, idag):
    """Form 4-köp (kod P) 30 dagar bakåt. XML-tolkningar cachas per accession."""
    cik = bolag["cik"]
    d = hamta_json("https://data.sec.gov/submissions/CIK%s.json" % cik)
    time.sleep(EDGAR_PAUS)
    recent = d.get("filings", {}).get("recent", {})
    former = recent.get("form", [])
    datumL = recent.get("filingDate", [])
    acc = recent.get("accessionNumber", [])
    dok = recent.get("primaryDocument", [])
    grans = (idag - timedelta(days=IN_FONSTER_DAGAR)).isoformat()
    kop = []
    cache = state.setdefault("edgar", {})
    for i, form in enumerate(former):
        if form != "4" or datumL[i] < grans:
            continue
        a = acc[i]
        if a not in cache:
            try:
                url = ("https://www.sec.gov/Archives/edgar/data/%s/%s/%s"
                       % (cik.lstrip("0"), a.replace("-", ""), dok[i]))
                xml = hamta(url).decode("utf-8", errors="replace")
                time.sleep(EDGAR_PAUS)
                ar_kop = ("<transactionCode>P</transactionCode>" in xml)
                agare = re.search(r"<rptOwnerName>([^<]+)</rptOwnerName>", xml)
                cache[a] = {"kop": ar_kop, "agare": agare.group(1).strip() if agare else "?",
                            "datum": datumL[i]}
            except Exception:
                cache[a] = {"kop": False, "agare": "?", "datum": datumL[i], "fel": True}
        if cache[a].get("kop"):
            kop.append(cache[a])
    agare = sorted(set(k["agare"] for k in kop))
    return {"antalKop": len(kop), "kopare": agare}


# ---------------------------------------------------------------- FI (IN + BL, SE)
def fi_insyn_se(idag):
    """EN export per körning: alla Förvärv 30 dagar bakåt, grupperat per emittent."""
    fran = (idag - timedelta(days=IN_FONSTER_DAGAR)).isoformat()
    url = ("https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Search"
           "?SearchFunctionType=Insyn&Publiceringsdatum.From=%s&button=export" % fran)
    text = hamta(url, timeout=60).decode("utf-16")
    rader = csv.DictReader(io.StringIO(text), delimiter=";")
    per_emittent = {}
    for rad in rader:
        if (rad.get("Status", "").strip() != "Aktuell"
                or "förvärv" not in (rad.get("Karaktär") or "").lower()):
            continue
        emittent = (rad.get("Emittent") or "").strip()
        person = (rad.get("Person i ledande ställning") or "").strip()
        if emittent and person:
            per_emittent.setdefault(normalisera(emittent), set()).add(person)
    return {k: sorted(v) for k, v in per_emittent.items()}


def normalisera(s):
    s = s.lower()
    for t in (" ab", " abp", " (publ)", ",", "."):
        s = s.replace(t, "")
    return s.strip()


def fi_blankning(state, idag):
    """Aggregerad blankning per emittent ur ODS-registret; trend byggs i state."""
    ra = hamta("https://www.fi.se/sv/vara-register/blankningsregistret/GetAktuellFile/",
               timeout=60)
    z = zipfile.ZipFile(io.BytesIO(ra))
    x = z.read("content.xml").decode("utf-8")
    agg = {}
    for radxml in re.findall(r"<table:table-row[^>]*>(.*?)</table:table-row>", x, re.S):
        celler = [re.sub(r"<[^>]+>", "", c) for c in
                  re.findall(r"<table:table-cell[^>]*>(.*?)</table:table-cell>", radxml, re.S)]
        celler = [c.strip() for c in celler if c.strip()]
        if len(celler) >= 3:
            # format: innehavare · emittent · ISIN · position % · datum
            m = re.match(r"^([0-9]+[.,][0-9]+)$", celler[3] if len(celler) > 3 else "")
            if m:
                try:
                    agg[normalisera(celler[1])] = agg.get(normalisera(celler[1]), 0.0) + \
                        float(m.group(1).replace(",", "."))
                except Exception:
                    pass
    hist = state.setdefault("blankning", {})
    hist[idag.isoformat()] = {k: round(v, 2) for k, v in agg.items() if v >= 0.5}
    # städa: behåll 45 dagar
    for k in sorted(hist.keys())[:-45]:
        del hist[k]
    return agg, hist


# ---------------------------------------------------------------- FMP (ES + VÄ)
def fmp(endpoint, symbol):
    url = ("https://financialmodelingprep.com/stable/%s?symbol=%s&apikey=%s"
           % (endpoint, urllib.parse.quote(symbol), FMP_NYCKEL))
    try:
        return hamta_json(url, timeout=20)
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None


# ---------------------------------------------------------------- huvudflöde
def main():
    idag = date.today()
    uni = las_json(UNIVERSUM, None)
    snapshot = las_json(DATAFIL, None)
    state = las_json(STATEFIL, {})
    gammal = las_json(UTFIL, None)
    if not uni:
        print("universum.json saknas — kör scripts/bygg_universum.py först.")
        sys.exit(1)
    bolag = uni["bolag"]

    sektor_data = {}
    if snapshot:
        for s in snapshot.get("sektorer", []):
            sektor_data[s["id"]] = s

    # MA-proxy: topp 4 på revQ2-rank eller RS-rank
    rev_rank = [s["id"] for s in sorted(sektor_data.values(),
                key=lambda s: -(s.get("revQ2", {}) or {}).get("v", -999))]
    rs_rank = [s["id"] for s in sorted(sektor_data.values(),
               key=lambda s: (s.get("rsRank") or 99))]
    ma_topp = set(rev_rank[:4]) | set(rs_rank[:4])

    # index-serier för RS (dagliga → veckovisa)
    index_serier = {}
    for marknad, symbol in (("US", "SPY"), ("SE", "^OMX")):
        try:
            index_serier[marknad] = veckoserie_ur_dagar(yahoo_dagserie(symbol))
            time.sleep(YAHOO_PAUS)
        except Exception as fel:
            print("indexserie %s: FEL %s" % (symbol, fel))
            index_serier[marknad] = None
    bredd = {"over50": 0, "over200": 0, "antal": 0}

    # SE-insyn + blankning (en hämtning var, inte per bolag)
    try:
        se_insyn = fi_insyn_se(idag)
    except Exception as fel:
        print("FI-insyn: FEL %s" % fel)
        se_insyn = None
    try:
        _, bl_hist = fi_blankning(state, idag)
    except Exception as fel:
        print("FI-blankning: FEL %s" % fel)
        bl_hist = None

    rotdag = idag.toordinal() % ROTATIONSDELAR
    es_cache = state.setdefault("es", {})
    va_cache = state.setdefault("va", {})

    lista = []
    tk_ok = 0
    fmp_budget = 230  # marginal under gratisplanens tak
    for idx, b in enumerate(bolag):
        tick = b["ticker"]
        signaler = {}

        # --- TK (+ breddunderlag ur samma dagserie)
        tk = None
        try:
            dagar = yahoo_dagserie(b["yahooSymbol"])
            time.sleep(YAHOO_PAUS)
            stang = veckoserie_ur_dagar(dagar)
            ixs = index_serier.get(b["marknad"])
            if ixs:
                tk = tk_signal(stang, ixs)
            if b["marknad"] == "US" and len(dagar) >= 200:
                bredd["antal"] += 1
                if dagar[-1] > sum(dagar[-50:]) / 50: bredd["over50"] += 1
                if dagar[-1] > sum(dagar[-200:]) / 200: bredd["over200"] += 1
        except Exception:
            tk = None
        if tk:
            tk_ok += 1
        signaler["TK"] = tk

        # --- IN
        in_sig = None
        try:
            if b["marknad"] == "US" and b.get("cik"):
                r = edgar_in_us(b, state, idag)
                dippkop = tk and tk["dipp"] >= IN_DIPP_PROCENT and r["antalKop"] >= 1
                aktiv = len(r["kopare"]) >= 2 or dippkop
                in_sig = {"aktiv": aktiv,
                          "delpoang": round(klamp(50 + 25 * max(len(r["kopare"]) - 1, 0) +
                                                  (15 if dippkop else 0))),
                          "kopare": len(r["kopare"])}
            elif b["marknad"] == "SE" and se_insyn is not None and b.get("fiEmittent"):
                nyckel = normalisera(b["fiEmittent"])
                pers = []
                for emit, personer in se_insyn.items():
                    if nyckel in emit:
                        pers = personer
                        break
                dippkop = tk and tk["dipp"] >= IN_DIPP_PROCENT and len(pers) >= 1
                aktiv = len(pers) >= 2 or dippkop
                in_sig = {"aktiv": aktiv,
                          "delpoang": round(klamp(50 + 25 * max(len(pers) - 1, 0) +
                                                  (15 if dippkop else 0))),
                          "kopare": len(pers)}
        except Exception:
            in_sig = None
        signaler["IN"] = in_sig

        # --- ES + VÄ (FMP-rotation + färskhetscache)
        es = es_cache.get(tick)
        va = va_cache.get(tick)
        min_rotdag = hash(tick) % ROTATIONSDELAR
        if min_rotdag == rotdag and FMP_NYCKEL and fmp_budget >= 2:
            fmp_budget -= 2
            symbol = b["yahooSymbol"] if b["marknad"] == "US" else b["yahooSymbol"]
            ptc = fmp("price-target-consensus", symbol)
            kvot = fmp("quote", symbol)
            nu = None
            if isinstance(ptc, list) and ptc and ptc[0].get("targetConsensus"):
                nu = float(ptc[0]["targetConsensus"])
            historik = (es or {}).get("historik", [])
            if nu is not None:
                historik = (historik + [{"d": idag.isoformat(), "tp": nu}])[-12:]
            riktning = None
            for aldre in reversed(historik[:-1]):
                if (idag - date.fromisoformat(aldre["d"])).days >= 14:
                    riktning = (historik[-1]["tp"] / aldre["tp"] - 1) * 100 if aldre["tp"] else None
                    break
            es = {"datum": idag.isoformat(), "historik": historik, "riktning": riktning}
            es_cache[tick] = es
            pe = None
            if isinstance(kvot, list) and kvot and kvot[0].get("pe"):
                pe = float(kvot[0]["pe"])
            va = {"datum": idag.isoformat(), "pe": pe}
            va_cache[tick] = va

        es_sig = None
        if es and es.get("riktning") is not None:
            farsk = (idag - date.fromisoformat(es["datum"])).days <= ES_FARSK_DAGAR
            if farsk or True:  # riktningen bygger på historik — vintage redovisas i skal
                r = es["riktning"]
                es_sig = {"aktiv": r >= 0, "delpoang": round(klamp(55 + r * 8)),
                          "riktning": round(r, 1), "vintage": es["datum"]}
        signaler["ES"] = es_sig

        va_sig = None
        sekt = sektor_data.get(b.get("sektorId") or "")
        if va and va.get("pe") and sekt and (sekt.get("fwdPE10y") or {}).get("v"):
            ref = sekt["fwdPE10y"]["v"]
            rabatt = (ref - va["pe"]) / ref * 100
            va_sig = {"aktiv": rabatt > 0, "delpoang": round(klamp(50 + rabatt * 2)),
                      "rabatt": round(rabatt, 1), "vintage": va["datum"]}
        signaler["VÄ"] = va_sig

        # --- FL (sektorproxy)
        fl = None
        if sekt is not None:
            flode = sekt.get("flodeM")
            if flode and flode.get("v") is not None:
                fl = {"aktiv": flode["v"] > 0,
                      "delpoang": round(klamp(50 + (flode.get("andelInflode") or 0) / 2))}
        signaler["FL"] = fl

        # --- MA (proxy: sektor topp 4 på någon rank)
        ma = None
        if b.get("sektorId") and sektor_data:
            ma = {"aktiv": b["sektorId"] in ma_topp, "delpoang": 60 if b["sektorId"] in ma_topp else 30}
        signaler["MA"] = ma

        # --- BL
        bl = None
        if b["marknad"] == "SE" and bl_hist is not None and b.get("fiEmittent"):
            nyckel = normalisera(b["fiEmittent"])
            dagar = sorted(bl_hist.keys())
            def niva(dag):
                for emit, v in bl_hist.get(dag, {}).items():
                    if nyckel in emit:
                        return v
                return 0.0
            nu_bl = niva(dagar[-1]) if dagar else 0.0
            gammal_dag = None
            for dsträng in reversed(dagar):
                if (idag - date.fromisoformat(dsträng)).days >= 26:
                    gammal_dag = dsträng
                    break
            if gammal_dag is not None:
                fallande = nu_bl < niva(gammal_dag)
                bl = {"aktiv": nu_bl > BL_TROSKEL and fallande,
                      "delpoang": round(klamp(40 + nu_bl * 10 + (20 if fallande else 0))),
                      "niva": round(nu_bl, 2)}
            # < 4 veckors historik → null (trend okänd — aldrig hittepå)
        signaler["BL"] = bl

        # --- poäng
        aktiva = [s for s, v in signaler.items() if v and v["aktiv"]]
        sparade = [s for s, v in signaler.items() if v is not None]
        viktsumma = sum(VIKTER[s] for s in sparade) or 1.0
        styrka = sum(VIKTER[s] * signaler[s]["delpoang"] for s in aktiva) / viktsumma
        delpoang = {s: signaler[s]["delpoang"] for s in aktiva}

        skal_delar = []
        if "IN" in aktiva:
            skal_delar.append("Insynskluster (%d köpare 30 d)" % signaler["IN"]["kopare"])
        if "ES" in aktiva:
            skal_delar.append(("riktkurs %+.1f %% (%s)" % (signaler["ES"]["riktning"], signaler["ES"]["vintage"])).replace(".", ","))
        if "TK" in aktiva:
            skal_delar.append(("RS +%.1f %% mot SMA12, över 40 v-snittet" % signaler["TK"]["avst12"]).replace(".", ","))
        if "VÄ" in aktiva:
            skal_delar.append("P/E-rabatt %.0f %% mot sektorns 10-årssnitt (proxy)" % signaler["VÄ"]["rabatt"])
        if "FL" in aktiva:
            skal_delar.append("sektorinflöde (proxy)")
        if "MA" in aktiva:
            skal_delar.append("sektor i topp 4")
        if "BL" in aktiva:
            skal_delar.append(("blankning %.1f %% och fallande" % signaler["BL"]["niva"]).replace(".", ","))

        if b["marknad"] == "US":
            url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%s&type=4" % b["cik"]
        else:
            url = ("https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Search"
                   "?SearchFunctionType=Insyn&Utgivare=%s" % urllib.parse.quote(b.get("fiEmittent") or b["namn"]))

        lista.append({
            "ticker": tick, "namn": b["namn"], "marknad": b["marknad"],
            "sektorId": b.get("sektorId"),
            "parlor": aktiva, "sparade": len(sparade),
            "delpoang": delpoang,
            "styrka": round(styrka),
            "skal": " + ".join(skal_delar) if skal_delar else "inga aktiva signaler",
            "url": url,
        })
        if (idx + 1) % 50 == 0:
            print("  %d/%d bolag …" % (idx + 1, len(bolag)), flush=True)
            # checkpoint: EDGAR-/FMP-cachen överlever avbrott och timeouts
            STATEFIL.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")

    tackning = tk_ok / len(bolag) if bolag else 0
    STATEFIL.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
    if tackning < TACKNINGSKRAV:
        print("TÄCKNING %.0f %% < %d %% — behåller gårdagens screen.json (%s)."
              % (tackning * 100, TACKNINGSKRAV * 100,
                 (gammal or {}).get("vintage", "ingen tidigare fil")))
        sys.exit(0)

    # REGELVERK §2: konvergens → styrka → IN → ES → VÄ
    def nyckel(p):
        dp = p["delpoang"]
        return (-len(p["parlor"]), -p["styrka"], -dp.get("IN", 0), -dp.get("ES", 0), -dp.get("VÄ", 0))
    lista.sort(key=nyckel)

    ut = {
        "schemaVersion": "1.0",
        "vintage": idag.isoformat(),
        "genererad": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "universum": {"antal": len(bolag), "kalla": "S&P 500 + Nasdaq Sthlm Large Cap (data/universum.json)"},
        "vikter": VIKTER,
        "tackning": round(tackning, 3),
        "rotation": {"delar": ROTATIONSDELAR, "dagensDel": rotdag,
                     "kommentar": "ES/VÄ via FMP i rotation — gratisplanens anropstak"},
        "lista": lista,
    }
    UTFIL.write_text(json.dumps(ut, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("screen.json: %d bolag · täckning %.0f %% · topp: %s"
          % (len(lista), tackning * 100,
             ", ".join("%s %d/%d·%d" % (p["ticker"], len(p["parlor"]), p["sparade"], p["styrka"])
                       for p in lista[:5])))


if __name__ == "__main__":
    main()
