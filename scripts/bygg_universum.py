# -*- coding: utf-8 -*-
"""Bygger data/universum.json — screenerns bolagsuniversum. Reproducerbart:

  US: S&P 500-konstituenter ur Wikipedias tabell "List of S&P 500 companies"
      (id="constituents") — ger ticker, namn, GICS-sektor och CIK i ett svep.
  SE: Nasdaq Stockholm Large Cap ur scripts/universum_se_seed.json (KURERAD —
      nasdaqomxnordic.com lades ned 2026 och nya nasdaq.com saknar öppet API;
      seed-filen är handplockad och redigeras för hand vid behov).

Körs manuellt (lokalt eller via universum.yml/workflow_dispatch); resultatet
committas. Lägga till/ta bort bolag för hand: redigera universum_se_seed.json
(SE) eller lägg en post i HAND_TILLAGDA/HAND_BORTTAGNA nedan (US m.fl.) och
kör om skriptet. Fältkontrakt per bolag:
  {ticker, namn, marknad, yahooSymbol, sektorId, cik, fiEmittent}
sektorId = sektor-ETF-id (XLK …) — kan den inte härledas säkert blir den null
och bolaget viktas bort i sektorkopplade signaler (aldrig hittepå).
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROT = Path(__file__).resolve().parent.parent
UT = ROT / "data" / "universum.json"
SEED_SE = Path(__file__).resolve().parent / "universum_se_seed.json"
UA = "Stockpit/1.0 (privat marknadspanel; kontakt: erik.hjalmarson@gmail.com)"

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# GICS-sektor → sektor-ETF-id (samma id:n som resten av appen)
GICS_TILL_ETF = {
    "Information Technology": "XLK",
    "Industrials": "XLI",
    "Communication Services": "XLC",
    "Materials": "XLB",
    "Consumer Staples": "XLP",
    "Health Care": "XLV",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Energy": "XLE",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
}

# Manuell kurering av US-listan efter generering (tomma som utgångsläge).
HAND_TILLAGDA = []      # poster med fullt fältkontrakt
HAND_BORTTAGNA = set()  # tickers som ska uteslutas


def hamta(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as svar:
        return svar.read().decode("utf-8", errors="replace")


def rensa(html):
    return re.sub(r"<[^>]+>", "", html).replace("&amp;", "&").strip()


def bygg_us():
    text = hamta(WIKI_URL)
    i = text.find('id="constituents"')
    if i < 0:
        raise RuntimeError("Wikipedia-tabellen 'constituents' hittades inte — kontrollera sidstrukturen.")
    tabell = text[i:text.find("</table>", i)]
    rader = re.findall(r"<tr[^>]*>(.*?)</tr>", tabell, re.S)
    huvud = [rensa(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", rader[0], re.S)]
    kol = {namn: idx for idx, namn in enumerate(huvud)}
    for kravd in ("Symbol", "Security", "GICS Sector", "CIK"):
        if kravd not in kol:
            raise RuntimeError("Kolumnen %r saknas i Wikipedia-tabellen (huvud: %s)" % (kravd, huvud))
    bolag = []
    for rad in rader[1:]:
        celler = [rensa(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", rad, re.S)]
        if len(celler) < len(huvud):
            continue
        ticker = celler[kol["Symbol"]]
        if not ticker or ticker in HAND_BORTTAGNA:
            continue
        gics = celler[kol["GICS Sector"]]
        cik = celler[kol["CIK"]].zfill(10) if celler[kol["CIK"]].isdigit() else None
        bolag.append({
            "ticker": ticker,
            "namn": celler[kol["Security"]],
            "marknad": "US",
            # Yahoo använder bindestreck där S&P-symboler har punkt (BRK.B → BRK-B)
            "yahooSymbol": ticker.replace(".", "-"),
            "sektorId": GICS_TILL_ETF.get(gics),  # okänd GICS → null, viktas bort
            "cik": cik,
            "fiEmittent": None,
        })
    if len(bolag) < 400:
        raise RuntimeError("Bara %d US-bolag parsade — orimligt för S&P 500, avbryter." % len(bolag))
    return bolag


def bygg_se():
    seed = json.loads(SEED_SE.read_text(encoding="utf-8"))
    bolag = []
    for rad in seed["bolag"]:
        bolag.append({
            "ticker": rad["ticker"],
            "namn": rad["namn"],
            "marknad": "SE",
            # Yahoo-symbol: mellanslag i tickern → bindestreck, + .ST
            "yahooSymbol": rad["ticker"].replace(" ", "-") + ".ST",
            "sektorId": rad.get("sektorId"),
            "cik": None,
            "fiEmittent": rad.get("fiEmittent"),
        })
    return bolag, seed.get("vintage", "")


def main():
    us = bygg_us()
    se, se_vintage = bygg_se()
    alla = us + se + list(HAND_TILLAGDA)
    sedda = set()
    unika = []
    for b in alla:
        nyckel = (b["marknad"], b["ticker"])
        if nyckel in sedda:
            continue
        sedda.add(nyckel)
        unika.append(b)
    ut = {
        "schemaVersion": "1.0",
        "genererad": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "kallor": {
            "US": "Wikipedia: List of S&P 500 companies (tabell 'constituents')",
            "SE": "kurerad seed %s (scripts/universum_se_seed.json — nasdaqomxnordic nedlagd, "
                  "öppet maskinläsbart alternativ saknas)" % se_vintage,
        },
        "antal": {"US": len(us), "SE": len(se), "totalt": len(unika)},
        "bolag": unika,
    }
    UT.write_text(json.dumps(ut, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    utan_sektor = [b["ticker"] for b in unika if not b["sektorId"]]
    print("universum.json: %d bolag (US %d · SE %d) · utan sektorId: %d %s"
          % (len(unika), len(us), len(se), len(utan_sektor), utan_sektor[:5]))


if __name__ == "__main__":
    try:
        main()
    except Exception as fel:
        print("FEL:", fel)
        sys.exit(1)
