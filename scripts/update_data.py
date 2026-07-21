# -*- coding: utf-8 -*-
"""
STOCKPIT — datauppdaterare (körs var 30:e minut av GitHub Actions)

Strategi: MERGE, inte återskapande. Skriptet läser föregående data/data.json
(bootstrap: data/sample_data.json), uppdaterar de fält som har fria källor och
lämnar kurerade fält orörda med sina vintage-märken. Schemat i DATA_SCHEMA.md
ändras aldrig här — frontenden är omedveten om var siffrorna kommer ifrån.

Källor (alla utan API-nyckel):
  FRED  fredgraph.csv — räntekurvan (DTB3/DGS2/DGS5/DGS10/DGS30),
        HY-spread (BAMLH0A0HYM2), realränta (DFII10)
  Yahoo chart-API — VIX, MOVE, guld/silver/koppar/brent, kvotparen,
        sektor-ETF:ernas veckoserier mot SPY (radar + linser)
  SEC EDGAR full-text search (JSON) — senaste Form 4-poster (US-insyn)
  FI marknadssok — insynsregistrets INBYGGDA CSV-EXPORT (aldrig HTML-skrap):
        exakt EN export per körning, User-Agent med kontakt, backoff via state
        (efter 3 fel i rad görs nya försök högst varannan timme).

Feltålighet: varje källblock är try/except — misslyckas ett behålls föregående
värden och körningen fortsätter. Exit 1 endast om SAMTLIGA källor fallerar.
"""

import csv
import io
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROT = Path(__file__).resolve().parent.parent
DATAFIL = ROT / "data" / "data.json"
SAMPLEFIL = ROT / "data" / "sample_data.json"
STATEFIL = ROT / "data" / "state.json"

UA = {"User-Agent": "Stockpit/1.0 (privat marknadspanel; kontakt: erik.hjalmarson@gmail.com)"}
TIDSGRANS = 30

FRED_SERIER = {"m3": "DTB3", "y2": "DGS2", "y5": "DGS5", "y10": "DGS10", "y30": "DGS30",
               "hy": "BAMLH0A0HYM2", "real": "DFII10"}

SEKTOR_ETF = {"XLK": "XLK", "XLI": "XLI", "XLC": "XLC", "XLB": "XLB", "XLP": "XLP", "XLV": "XLV",
              "XLU": "XLU", "XLRE": "XLRE", "XLE": "XLE", "XLF": "XLF", "XLY": "XLY"}

KVOTPAR = {  # id → (täljare, nämnare, stiger-är-grönt)
    "bcomspx": ("^BCOM", "SPY", False),
    "hgxau":   ("HG=F", "GC=F", True),
    "btcxau":  ("BTC-USD", "GC=F", True),
    "xlyxlp":  ("XLY", "XLP", True),
    "hygtlt":  ("HYG", "TLT", True),
    "soxspx":  ("^SOX", "SPY", True),
    "qqqspy":  ("QQQ", "SPY", True),
}


# ---------------------------------------------------------------- hjälpare
def sv(tal, dec=1, tecken=False):
    """Svenskt talformat: komma, ev. plustecken/minustecken (U+2212)."""
    s = f"{tal:+.{dec}f}" if tecken else f"{tal:.{dec}f}"
    return s.replace("-", "−").replace(".", ",")


def iso_vecka(d):
    ar, v, _ = d.isocalendar()
    return f"{ar}-W{v:02d}"


def senaste_vardag(d):
    while d.weekday() > 4:
        d -= timedelta(days=1)
    return d


def hamta(url, **kw):
    return requests.get(url, headers=UA, timeout=TIDSGRANS, **kw)


def las_json(p, reserv):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return reserv


# ---------------------------------------------------------------- FRED
def fred_serie(serie_id):
    """Returnerar lista av (datum, värde) för senaste ~3 mån."""
    start = (date.today() - timedelta(days=100)).isoformat()
    r = hamta(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={serie_id}&cosd={start}")
    r.raise_for_status()
    ut = []
    for rad in csv.reader(io.StringIO(r.text)):
        if len(rad) == 2 and rad[1] not in (".", "") and rad[0][:2] == "20":
            try:
                ut.append((rad[0], float(rad[1])))
            except ValueError:
                pass
    if not ut:
        raise ValueError(f"FRED {serie_id}: tom serie")
    return ut


def fred_nu_och_bak(serie, handelsdagar_bak):
    nu = serie[-1][1]
    bak = serie[max(0, len(serie) - 1 - handelsdagar_bak)][1]
    return nu, bak


# ---------------------------------------------------------------- Yahoo
def yahoo_chart(symbol, rng="3mo", interval="1d"):
    """Returnerar (tidsstämplar, stängningar) utan None-hål."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol)}"
           f"?range={rng}&interval={interval}")
    r = hamta(url)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    tider = res.get("timestamp") or []
    stang = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    par = [(t, c) for t, c in zip(tider, stang) if c is not None]
    if not par:
        raise ValueError(f"Yahoo {symbol}: tom serie")
    return [p[0] for p in par], [p[1] for p in par]


def yahoo_sista(symbol, rng="1mo"):
    _, c = yahoo_chart(symbol, rng)
    return c[-1]


def yahoo_delta(symbol, dagar_bak=5, rng="3mo"):
    _, c = yahoo_chart(symbol, rng)
    nu = c[-1]
    bak = c[max(0, len(c) - 1 - dagar_bak)]
    return nu, nu - bak


# ---------------------------------------------------------------- källblock
def uppdatera_rantor(data, idag):
    serier = {k: fred_serie(v) for k, v in FRED_SERIER.items()}
    kurva_nu, kurva_forr = [], []
    for k in ("m3", "y2", "y5", "y10", "y30"):
        nu, bak = fred_nu_och_bak(serier[k], 21)
        kurva_nu.append(round(nu, 2))
        kurva_forr.append(round(bak, 2))
    rk = data["makro"]["rantekurva"]
    rk["nu"], rk["forr"] = kurva_nu, kurva_forr
    rk["vintage"] = serier["y10"][-1][0]
    bp = round(((kurva_nu[3] - kurva_nu[1]) - (kurva_forr[3] - kurva_forr[1])) * 100)
    rk["not"] = (f"Brantande 2s10s: {sv(bp, 0, True)} bp/mån." if bp > 0
                 else f"Flackande 2s10s: {sv(bp, 0, True)} bp/mån." if bp < 0
                 else "Oförändrad 2s10s senaste månaden.")

    hy_nu, hy_v = fred_nu_och_bak(serier["hy"], 5)
    for t in data["tripwires"]:
        if t["id"] == "hy":
            t["varde"] = round(hy_nu, 2)
            t["delta"] = f"{sv((hy_nu - hy_v) * 100, 0, True)} bp · 1 v"
            t["vintage"] = serier["hy"][-1][0]
    for r in data["riskregler"]:
        if r["id"] == "hy":
            r["varde"] = round(hy_nu, 2)

    real_nu, real_bak = fred_nu_och_bak(serier["real"], 21)
    data["makro"]["realranta"] = {
        "varde": sv(real_nu, 2) + " %", "pil": "ned" if real_nu < real_bak else "upp",
        "delta": f"{sv((real_nu - real_bak) * 100, 0, True)} bp · 1 mån",
        "kalla": "FRED 10y TIPS (DFII10)", "vintage": serier["real"][-1][0],
    }


def uppdatera_vol(data, idag):
    for tid, symbol, dec in (("vix", "^VIX", 1), ("move", "^MOVE", 0)):
        nu, d = yahoo_delta(symbol)
        for t in data["tripwires"]:
            if t["id"] == tid:
                t["varde"] = round(nu, dec)
                t["delta"] = f"{sv(d, dec, True)} · 1 v"
                t["kalla"] = f"Yahoo {symbol}"
                t["vintage"] = idag.isoformat()
        for r in data["riskregler"]:
            if r["id"] == tid:
                r["varde"] = round(nu, dec)


def uppdatera_metaller(data, idag):
    mk = data["makro"]
    guld_nu, guld_d = yahoo_delta("GC=F", 20)
    silver_nu, silver_d = yahoo_delta("SI=F", 20)
    _, koppar_c = yahoo_chart("HG=F", "1y")
    koppar_nu = koppar_c[-1]
    koppar_d = koppar_nu - koppar_c[max(0, len(koppar_c) - 21)]
    sma200 = sum(koppar_c[-200:]) / min(200, len(koppar_c))

    real_ned = mk.get("realranta", {}).get("pil") == "ned"
    mk["guld"].update({"varde": sv(guld_nu, 0), "pil": "upp" if guld_d >= 0 else "ned",
                       "delta4v": sv(guld_d / (guld_nu - guld_d) * 100, 1, True) + " %",
                       "status": "gron" if real_ned else "gul",
                       "atgard": "oka" if real_ned and guld_d >= 0 else "bevaka",
                       "kalla": "Yahoo GC=F", "vintage": idag.isoformat()})
    mk["silver"].update({"varde": sv(silver_nu, 2), "pil": "upp" if silver_d >= 0 else "ned",
                         "delta4v": sv(silver_d / (silver_nu - silver_d) * 100, 1, True) + " %",
                         "status": "gul", "atgard": "bevaka",
                         "kalla": "Yahoo SI=F", "vintage": idag.isoformat()})
    over200 = koppar_nu > sma200
    mk["koppar"].update({"varde": sv(koppar_nu, 2), "pil": "upp" if koppar_d >= 0 else "ned",
                         "delta4v": sv(koppar_d / (koppar_nu - koppar_d) * 100, 1, True) + " %",
                         "over200d": over200, "status": "gul" if over200 else "rod",
                         "atgard": "bevaka",
                         "kalla": "Yahoo HG=F (USD/lb)", "vintage": idag.isoformat()})
    mk["olja"] = {"v": round(yahoo_sista("BZ=F"), 2), "kalla": "Yahoo BZ=F (Brent, USD/fat)",
                  "vintage": idag.isoformat()}


def uppdatera_kvoter(data, idag):
    cache = {}

    def veckoserie(symbol):
        if symbol not in cache:
            _, c = yahoo_chart(symbol, "8mo", "1wk")
            cache[symbol] = c
        return cache[symbol]

    for kvot in data["regim"]["kvoter"]:
        par = KVOTPAR.get(kvot["id"])
        if not par:
            continue
        a, b, upp_gront = par
        ta, tb = veckoserie(a), veckoserie(b)
        n = min(len(ta), len(tb))
        serie = [ta[len(ta) - n + i] / tb[len(tb) - n + i] for i in range(n)]
        if len(serie) < 14:
            continue
        snitt13 = sum(serie[-13:]) / 13
        index = round(serie[-1] / snitt13 * 100)
        riktning4v = serie[-1] - serie[-5] if len(serie) >= 5 else 0.0
        pil = "upp" if riktning4v > 0 else "ned" if riktning4v < 0 else "hoger"
        medvind = (riktning4v > 0) == upp_gront
        avvikelse = abs(index - 100)
        kvot.update({"index": index, "pil": pil,
                     "status": "gron" if medvind and avvikelse <= 15
                               else "gul" if avvikelse <= 15 or medvind else "rod"})
    data["regim"]["kvoterLineage"] = {"kalla": "Yahoo, veckodata (index mot 13 v-snitt)",
                                      "vintage": idag.isoformat()}


def uppdatera_sektorserier(data, idag, forra):
    """Veckovisa relativserier mot SPY + rsRank ur samma formel som frontenden."""
    _, spy = yahoo_chart("SPY", "8mo", "1wk")
    beraknade = {}
    for sid, etf in SEKTOR_ETF.items():
        _, c = yahoo_chart(etf, "8mo", "1wk")
        n = min(len(c), len(spy))
        rel = [c[len(c) - n + i] / spy[len(spy) - n + i] for i in range(n)]
        if len(rel) < 19:
            raise ValueError(f"{etf}: för kort veckoserie ({len(rel)})")
        rel19 = rel[-19:]
        bas = rel19[0]
        beraknade[sid] = [round(v / bas * 100, 2) for v in rel19]
        time.sleep(0.4)

    # rsRank = ordning på avstånd från 12 v-SMA (samma definition som frontenden)
    def x_avstand(serie):
        sma12 = sum(serie[-12:]) / 12
        return serie[-1] / sma12 - 1

    ordning = sorted(beraknade, key=lambda s: x_avstand(beraknade[s]), reverse=True)
    rank = {sid: i + 1 for i, sid in enumerate(ordning)}

    ny_vecka = data.get("asOfWeek") != iso_vecka(idag)
    for s in data["sektorer"]:
        if s["id"] in beraknade:
            gammal_rank = s.get("rsRank")
            s["relSerie19"] = beraknade[s["id"]]
            if ny_vecka and gammal_rank:
                s["rsRankForra"] = gammal_rank
            s["rsRank"] = rank[s["id"]]


def uppdatera_edgar(data, idag):
    igar = (idag - timedelta(days=3)).isoformat()
    r = hamta("https://efts.sec.gov/LATEST/search-index"
              f"?q=%22%22&forms=4&dateRange=custom&startdt={igar}&enddt={idag.isoformat()}")
    r.raise_for_status()
    poster, sedda = [], set()
    for hit in r.json().get("hits", {}).get("hits", [])[:40]:
        kalla = hit.get("_source", {})
        namn = "; ".join(kalla.get("display_names", [])[:2])
        adsh = kalla.get("adsh", "")
        if not namn or adsh in sedda:
            continue
        sedda.add(adsh)
        cik = (kalla.get("ciks") or [""])[0].lstrip("0")
        lank = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}"
                if cik and adsh else "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4")
        poster.append({"datum": kalla.get("file_date", ""), "namn": namn, "lank": lank})
        if len(poster) >= 10:
            break
    data.setdefault("insynFlode", {})["us"] = {
        "poster": poster, "kalla": "SEC EDGAR full-text search (Form 4)",
        "vintage": datetime.now(timezone.utc).isoformat(timespec="minutes"),
    }


def uppdatera_fi(data, state, idag):
    """En (1) export per körning mot FI:s inbyggda CSV-export. Aldrig HTML-skrap."""
    fi_state = state.setdefault("fi", {"senaste_publicering": "", "fel_i_rad": 0, "senaste_forsok": ""})
    if fi_state["fel_i_rad"] >= 3 and fi_state["senaste_forsok"]:
        senast = datetime.fromisoformat(fi_state["senaste_forsok"])
        if datetime.now(timezone.utc) - senast < timedelta(hours=2):
            print("  FI: backoff aktiv (>=3 fel i rad) — hoppar över denna körning")
            return
    fi_state["senaste_forsok"] = datetime.now(timezone.utc).isoformat(timespec="minutes")

    fran = (fi_state["senaste_publicering"][:10]
            if fi_state["senaste_publicering"] else (idag - timedelta(days=2)).isoformat())
    url = ("https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Search"
           f"?SearchFunctionType=Insyn&Publiceringsdatum.From={fran}&button=export")
    try:
        r = hamta(url)
        r.raise_for_status()
        text = r.content.decode("utf-16")
    except Exception:
        fi_state["fel_i_rad"] += 1
        raise
    fi_state["fel_i_rad"] = 0

    rader = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    granns = fi_state["senaste_publicering"]
    nya = []
    for rad in rader:
        pub = (rad.get("Publiceringsdatum") or "").strip()
        if rad.get("Status", "").strip() != "Aktuell" or not pub:
            continue
        if granns and pub <= granns:
            continue
        nya.append({
            "publicerad": pub,
            "emittent": (rad.get("Emittent") or "").strip(),
            "person": (rad.get("Person i ledande ställning") or "").strip(),
            "befattning": (rad.get("Befattning") or "").strip(),
            "karaktar": (rad.get("Karaktär") or "").strip(),
            "volym": (rad.get("Volym") or "").strip(),
            "enhet": (rad.get("Volymsenhet") or "").strip(),
            "pris": (rad.get("Pris") or "").strip(),
            "valuta": (rad.get("Valuta") or "").strip(),
        })
    alla_pub = [("%s" % (rad.get("Publiceringsdatum") or "")).strip() for rad in rader]
    if alla_pub:
        fi_state["senaste_publicering"] = max([granns] + alla_pub)

    flode = data.setdefault("insynFlode", {})
    tidigare = flode.get("se", {}).get("poster", [])
    flode["se"] = {
        "poster": (nya + tidigare)[:50],
        "nyaSenasteKorning": len(nya),
        "kalla": "FI insynsregistret, officiell CSV-export (1 export/körning)",
        "vintage": datetime.now(timezone.utc).isoformat(timespec="minutes"),
    }
    print(f"  FI: {len(nya)} nya poster sedan {granns or fran}")


# ---------------------------------------------------------------- huvudflöde
def main():
    data = las_json(DATAFIL, None) or las_json(SAMPLEFIL, None)
    if not data:
        print("Varken data.json eller sample_data.json gick att läsa — avbryter.")
        sys.exit(1)
    forra = json.loads(json.dumps(data))
    state = las_json(STATEFIL, {})
    idag = senaste_vardag(date.today())

    lyckade, fallerade = [], []
    for namn, fn in (("räntor/HY (FRED)", lambda: uppdatera_rantor(data, idag)),
                     ("vol (Yahoo)", lambda: uppdatera_vol(data, idag)),
                     ("metaller/olja (Yahoo)", lambda: uppdatera_metaller(data, idag)),
                     ("kvoter (Yahoo)", lambda: uppdatera_kvoter(data, idag)),
                     ("sektorserier (Yahoo)", lambda: uppdatera_sektorserier(data, idag, forra)),
                     ("US-insyn (EDGAR)", lambda: uppdatera_edgar(data, idag)),
                     ("SE-insyn (FI-export)", lambda: uppdatera_fi(data, state, idag))):
        try:
            fn()
            lyckade.append(namn)
            print(f"OK: {namn}")
        except Exception as fel:
            fallerade.append(namn)
            print(f"MISSLYCKADES: {namn} — {fel}")

    data["lage"] = "LIVE"
    data["genererad"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="minutes")
    data["asOfWeek"] = iso_vecka(idag)
    data["asOfDatum"] = idag.isoformat()
    data["illustrativ"] = False
    data["kommentar"] = ("LIVE-data från fria källor (FRED, Yahoo, SEC EDGAR, FI:s CSV-export), "
                         "uppdaterad var 30:e minut. Kurerade fält (flöden, estimat, megatrend, "
                         "aktielista) behåller sina vintage-märken tills de uppdateras manuellt.")

    DATAFIL.parent.mkdir(exist_ok=True)
    DATAFIL.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STATEFIL.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nKlart: {len(lyckade)} källblock OK, {len(fallerade)} fallerade -> data/data.json")

    if not lyckade:
        sys.exit(1)


if __name__ == "__main__":
    main()
