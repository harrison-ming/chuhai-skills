"""End-to-end tests on synthetic stores (local HTTP server, no internet).

F1 clean Shopify, F2 conflicts and missing pages, F3 password page plus
paste mode, F4 non-Shopify, F5 blocked. Then check_report and deliver in
both languages.
Run: python tools/run_tests.py   (or unittest discover -s this folder)
Python 3.8+, standard library only.
"""

import contextlib
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(SKILL, "scripts"), HERE]

import audit  # noqa: E402
import audit_collect  # noqa: E402
import check_report  # noqa: E402
import deliver  # noqa: E402
import fixture_sites  # noqa: E402
import paste_ingest  # noqa: E402
import server  # noqa: E402
from _shared import fetch  # noqa: E402

PASTE = os.path.join(HERE, "fixtures", "F3-paste")


def quiet(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = fn(*args)
    return rc, out.getvalue()


class E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="launch-readiness-e2e-")
        cls.runs = {}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_site(self, name, markets=None, lang="zh", patch=None):
        key = (name, markets, lang)
        if key in self.runs:
            return self.runs[key]
        run = os.path.join(self.tmp, "%s-%s" % (name, lang))
        data = os.path.join(run, "原始数据" if lang == "zh" else "data")
        with server.Store(fixture_sites.SITES[name]()) as st:
            fetcher = fetch.Fetcher(delay=0, retries=0, timeout=5)
            quiet(
                audit_collect.collect,
                st.base,
                os.path.join(data, "raw"),
                fetcher,
                markets,
                True,
            )
            paths = list(st.paths)
        if patch:
            p = os.path.join(data, "raw", "_status.json")
            with open(p, encoding="utf-8") as fh:
                status = json.load(fh)
            status.update(patch)
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(status, fh)
        rc, out = quiet(audit.main, [data, "--lang", lang])
        self.assertEqual(rc, 0, out)
        with open(os.path.join(data, "audit.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        res = (run, data, doc, dict((f["id"], f) for f in doc["findings"]), paths)
        self.runs[key] = res
        return res

    def assert_results(self, f, expected):
        for rid, result in expected.items():
            self.assertEqual(f[rid]["result"], result, "%s: %s" % (rid, f[rid]))

    def test_f1_clean_shopify(self):
        # The local server has no TLS, so the http->https probe is patched.
        _r, _d, doc, f, _p = self.run_site(
            "F1", "US,UK,DE", patch={"https_redirect": True}
        )
        self.assertEqual(doc["status"], "complete")
        bad = [
            x["id"]
            for x in doc["findings"]
            if x["result"] == "fail" and x["level"] in ("P0", "P1")
        ]
        self.assertEqual(bad, [])
        self.assertEqual(doc["verdict"], "go")
        self.assert_results(
            f,
            {
                "LOC-03": "pass",
                "PRV-04": "unknown",
                "POL-06": "pass",
                "LOC-02": "pass",
                "LOC-05": "pass",
                "EU-01": "pass",
                "PRV-03": "pass",
                "CNS-01": "pass",
            },
        )
        self.assertEqual(f["PRV-04"]["badge"], "not_found")

    def test_f2_conflicts_and_missing(self):
        _r, _d, doc, f, _p = self.run_site("F2", "US,UK,DE")
        self.assertEqual((f["POL-02"]["result"], f["POL-02"]["level"]), ("fail", "P0"))
        self.assertEqual(f["POL-02"]["badge"], "verified")
        for rid in ("CNS-01", "CNS-03"):
            self.assertEqual(f[rid]["result"], "fail")
            self.assertEqual(f[rid]["badge"], "conflicting")
            self.assertEqual(len(f[rid]["evidence"]), 2)
        for rid in ("CON-02", "CON-06", "TRU-01"):
            self.assertEqual((f[rid]["result"], f[rid]["level"]), ("fail", "P0"), rid)
        self.assert_results(
            f,
            {
                "LOC-03": "fail",
                "LOC-05": "fail",
                "LOC-06": "fail",
                "EU-05": "fail",
                "TRU-03": "fail",
                "CON-05": "fail",
            },
        )
        self.assertEqual(f["EU-01"]["badge"], "inferred")
        # privacy policy is a stub linking to /pages/privacy: followed, so complete
        self.assertEqual(f["PRV-01"]["result"], "pass", f["PRV-01"])
        self.assertNotIn("privacy-policy", " ".join(f["POL-07"]["evidence"]))
        self.assertEqual(doc["verdict"], "hold")
        kinds = {p["kind"] for p in f["LOC-03"]["data"]["problems"]}
        self.assertIn("invalid_code", kinds)
        self.assertIn("no_x_default", kinds)

    def test_f3_password_stops(self):
        _r, _d, doc, f, paths = self.run_site("F3")
        self.assertEqual(paths, ["/", "/password"])  # nothing else requested
        self.assertEqual(doc["status"], "partial")
        self.assertIsNone(doc["verdict"])
        self.assertEqual(f["TRU-05"]["result"], "manual")
        self.assertEqual(f["POL-01"]["result"], "unknown")
        self.assertIn("password_protected", doc["warnings"])

    def test_f3_paste_mode(self):
        run = os.path.join(self.tmp, "paste")
        data = os.path.join(run, "原始数据")
        rc, out = quiet(
            paste_ingest.main,
            [
                PASTE,
                "--name",
                "sunrise-home.example",
                "--markets",
                "EU,AU",
                "--out",
                data,
            ],
        )
        self.assertEqual(rc, 0, out)
        with open(os.path.join(data, "audit.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        f = dict((x["id"], x) for x in doc["findings"])
        self.assertEqual(doc["status"], "partial")
        self.assertEqual(f["CON-02"]["badge"], "self_claim")
        self.assertEqual(f["CNS-01"]["result"], "fail")
        self.assertEqual(f["AU-01"]["result"], "fail")
        self.assertEqual(f["GMC-01"]["result"], "unknown")  # on-page check
        self.assertEqual(f["POL-03"]["result"], "manual")  # absent from text given

    def test_f3_paste_saved_by_excel_and_notepad_ansi(self):
        # Chinese Windows: Excel re-saves the export and Notepad "ANSI" saves
        # the texts in code page 936. 2026-10-27 VM finding: names were read
        # with errors="replace" and came out as U+FFFD.
        src = os.path.join(self.tmp, "粘贴 GBK")
        os.makedirs(src)
        for name in os.listdir(PASTE):
            with open(os.path.join(PASTE, name), encoding="utf-8") as fh:
                text = fh.read()
            with open(
                os.path.join(src, name), "w", encoding="gbk", newline="\r\n"
            ) as fh:
                fh.write(text)
        data = os.path.join(self.tmp, "paste-gbk", "原始数据")
        rc, out = quiet(
            paste_ingest.main,
            [
                src,
                "--name",
                "sunrise-home.example",
                "--markets",
                "EU,AU",
                "--out",
                data,
            ],
        )
        self.assertEqual(rc, 0, out)
        with open(os.path.join(data, "raw", "products.json"), encoding="utf-8") as fh:
            products = fh.read()
        self.assertIn("纯亚麻", products)
        self.assertNotIn("\ufffd", products)
        with open(os.path.join(data, "audit.json"), encoding="utf-8") as fh:
            f = dict((x["id"], x) for x in json.load(fh)["findings"])
        self.assertEqual(f["CNS-01"]["result"], "fail")
        self.assertEqual(f["AU-01"]["result"], "fail")

    def test_f4_non_shopify(self):
        _r, _d, doc, f, paths = self.run_site("F4")
        self.assertEqual(doc["platform"], "other")
        self.assertEqual(doc["status"], "partial")
        self.assertEqual(f["LOC-02"]["result"], "skip")
        self.assertEqual(f["POL-01"]["result"], "pass")  # /returns/ found by crawler
        self.assertEqual(f["POL-02"]["result"], "unknown")  # JS-only page
        self.assertNotIn("/products.json", paths)

    def test_f5_blocked(self):
        _r, data, doc, f, _p = self.run_site("F5")
        self.assertEqual(doc["status"], "failed")
        self.assertTrue(all(x["result"] == "unknown" for x in doc["findings"]))
        with open(os.path.join(data, "report.draft.md"), encoding="utf-8") as fh:
            self.assertNotIn("找不到", fh.read().split("## 附录")[0])

    def test_draft_passes_check_and_deliver(self):
        for name, lang in (("F2", "zh"), ("F2", "en"), ("F5", "zh"), ("F1", "en")):
            run, data, doc, _f, _p = self.run_site(name, "US,UK,DE", lang=lang)
            shutil.copy(
                os.path.join(data, "report.draft.md"), os.path.join(data, "report.md")
            )
            rc, out = quiet(check_report.main, [run, "--lang", lang])
            self.assertEqual(rc, 0, out)
            self.assertNotIn("疑似标高", out)
            rc, out = quiet(
                deliver.main, [run, "--lang", lang, "--html-only", "--no-open"]
            )
            self.assertEqual(rc, 0, out)
            with open(os.path.join(data, "delivery.json"), encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual(rec["status"], doc["status"])
            self.assertTrue(rec["check"]["ok"])
            if doc["status"] == "failed":
                continue
            csv_name = "整改清单.csv" if lang == "zh" else "fix-list.csv"
            self.assertEqual(rec["csv"], [csv_name])
            with open(os.path.join(run, csv_name), encoding="utf-8-sig") as fh:
                rows = list(csv.reader(fh))
            head = rows[0]
            self.assertEqual(head[0], "优先级" if lang == "zh" else "Priority")
            self.assertEqual(len(head), 12)
            prios = [r[0] for r in rows[1:]]
            self.assertEqual(prios, sorted(prios))


if __name__ == "__main__":
    unittest.main()
