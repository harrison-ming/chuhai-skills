#!/usr/bin/env python3
"""Optional (network): ECB euro reference rates -> CNY per unit of each
sale currency, written to ``<raw-data>/fx.json``.

CLI:
    python pm_fx.py "<run folder>" [--lang zh|en]

Only used when the user gave no exchange rate. The ECB publishes rates per
EUR; CNY per USD is the cross rate CNY/USD. The file keeps the date, URL
and the original values. Failure is not fatal: without a rate the products
are reported as blocked and the user is asked for one.
Python 3.8+, standard library only.
"""

import argparse
import json
import os
import re
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import fetch, i18n, output  # noqa: E402

URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
CURRENCIES = ("USD", "GBP", "CAD", "AUD")
TIME_RE = re.compile(r"time=['\"](\d{4}-\d{2}-\d{2})['\"]")
RATE_RE = re.compile(r"currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([0-9.]+)['\"]")


def parse(xml_text):
    """dict(date, eur_rates, rates) from the ECB daily XML, or None."""
    m = TIME_RE.search(xml_text or "")
    eur = dict((c, r) for c, r in RATE_RE.findall(xml_text or ""))
    if not m or "CNY" not in eur:
        return None
    cny = Decimal(eur["CNY"])
    rates = {"EUR": str(cny)}
    for cur in CURRENCIES:
        if cur in eur:
            rates[cur] = str((cny / Decimal(eur[cur])).quantize(Decimal("0.0001")))
    return {"date": m.group(1), "url": URL, "eur_rates": eur, "rates": rates}


def fetch_rates(fetcher=None):
    fetcher = fetcher or fetch.Fetcher(timeout=15, delay=0, retries=1)
    res = fetcher.get(URL, accept="application/xml,text/xml;q=0.9,*/*;q=0.5")
    if res.get("status") != 200 or not res.get("body"):
        return None, res.get("note") or "http %s" % res.get("status")
    return parse(res["body"].decode("utf-8", errors="replace")), None


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Fetch ECB reference rates.")
    ap.add_argument("path", help="run folder or its raw-data folder")
    ap.add_argument("--lang", default="zh", choices=i18n.LANGS)
    args = ap.parse_args(argv)
    _run, raw = output.locate_run(args.path, args.lang)
    data, err = fetch_rates()
    if not data:
        print("fx: could not fetch ECB rates (%s)" % (err or "parse error"))
        return 1
    with open(os.path.join(raw, "fx.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("fx: ECB %s, CNY per USD %s" % (data["date"], data["rates"].get("USD")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
