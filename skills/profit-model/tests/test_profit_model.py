"""Known-answer tests for profit-model (offline, stdlib unittest).

Run from the repository root:
    python tools/run_tests.py
    python -m unittest discover -s skills/profit-model/tests

Fixtures are synthetic; fee rates are written into the fixtures, so a fee
table refresh does not change the expected answers (hand-computed with
decimal arithmetic, see the spec).
"""

import contextlib
import datetime
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
sys.path.insert(0, SCRIPTS)

import check_report  # noqa: E402
import pm_calc  # noqa: E402
import pm_fees  # noqa: E402
import pm_fx  # noqa: E402
import pm_inputs  # noqa: E402
import profit  # noqa: E402
import skill_config as cfg  # noqa: E402
from _shared import checks  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
HEADER = "商品,项目,基准,保守,乐观,单位,来源说明\n"


def fixture(name):
    return os.path.join(FIX, name, "inputs.csv")


def quiet(fn, *args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args, **kwargs)
    return rc, out.getvalue() + err.getvalue()


class TmpMixin(object):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="profit-model-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def csv(self, body, name="inputs.csv"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(HEADER + body)
        return path

    def run_pipeline(self, csv_path, lang="zh"):
        root = tempfile.mkdtemp(prefix="run-", dir=self.tmp)
        rc, out = quiet(
            profit.main,
            [
                "run",
                csv_path,
                "--lang",
                lang,
                "--out-root",
                root,
                "--no-open",
                "--html-only",
                "--no-fx-fetch",
            ],
        )
        self.assertEqual(rc, 0, out)
        runs = [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
        self.assertEqual(len(runs), 1)
        run = os.path.join(root, runs[0])
        raw = os.path.join(run, "原始数据" if lang == "zh" else "data")
        with open(os.path.join(raw, "report.md"), encoding="utf-8") as fh:
            md = fh.read()
        with open(os.path.join(raw, "result.json"), encoding="utf-8") as fh:
            result = json.load(fh)
        return run, raw, md, result


def compute(name_or_path):
    path = name_or_path if os.path.isfile(name_or_path) else fixture(name_or_path)
    return pm_calc.compute(pm_inputs.normalize(path))


def base(result, i=0):
    return result["skus"][i]["scenarios"]["base"]


class KnownAnswers(unittest.TestCase):
    def test_healthy_base(self):
        r = compute("healthy")
        b = base(r)
        self.assertEqual(r["status"], "complete")
        self.assertEqual(b["G"], "39.99")
        self.assertEqual(b["L"], "10.00")
        self.assertEqual(b["pay"], "1.46")
        self.assertEqual(b["cm0"], "16.55")
        self.assertEqual(b["cm0_pct_g"], "41.4")
        self.assertEqual(b["roas_be"], "2.42")
        self.assertEqual(b["max_cpa"], "16.55")
        self.assertEqual(b["cm1"], "6.55")
        be = r["store"]["breakeven"][0]
        self.assertEqual((be["tier"], be["units"]), ("user", 11))
        self.assertEqual(r["store"]["fixed"]["total"], "69.00")

    def test_price_solve(self):
        p = compute("healthy")["skus"][0]["price"]
        self.assertEqual(p["floor"], "32.64")
        self.assertEqual(p["target"], "39.25")
        self.assertEqual(p["target_display"], "39.99")
        self.assertTrue(
            Decimal(p["floor_raw"]).quantize(Decimal("0.0001")) == Decimal("32.6375")
        )

    def test_sensitivity_rank(self):
        s = compute("healthy")["skus"][0]["sensitivity"]
        top = [(r["key"], r["delta_up"], r["delta_down"]) for r in s[:3]]
        self.assertEqual(
            top,
            [
                ("price", "3.56", "-3.56"),
                ("fx_cny_per_unit", "0.87", "-1.07"),
                ("product_cost", "-0.75", "0.75"),
            ],
        )
        self.assertTrue(s[0]["lever"])
        self.assertNotIn("cpa", [r["key"] for r in s])

    def test_negative(self):
        r = compute("negative")
        b = base(r)
        self.assertEqual(b["L"], "14.06")
        self.assertEqual(b["cm0"], "-10.98")
        self.assertIsNone(b["roas_be"])
        self.assertEqual(r["skus"][0]["price"]["floor"], "49.23")
        self.assertEqual(r["status"], "complete")
        self.assertIsNone(r["store"]["breakeven"][0]["units"])

    def test_missing_invalid(self):
        r = compute("missing_invalid")
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["exit_code"], 2)
        codes = [(i["level"], i["code"], i["key"]) for i in r["issues"]]
        self.assertIn(("error", "pct_out_of_range", "discount_pct"), codes)
        a = dict((x["key"], x) for x in r["assumptions"])
        self.assertEqual(a["discount_pct"]["code"], "zero_default")
        self.assertEqual(a["return_rate_pct"]["code"], "return_trial")
        self.assertEqual(a["return_rate_pct"]["badge"], "inferred")
        self.assertEqual(a["duty_rate_pct"]["badge"], "not_found")
        sc = r["skus"][0]["scenarios_raw"]
        self.assertTrue(r["skus"][0]["duty_line"]["duty_missing"])
        self.assertEqual(r["skus"][0]["duty_line"]["loss"], "0.67")
        # trial return rates 20 / 10 / 5 make the scenarios differ
        self.assertLess(Decimal(sc["cons"]["cm0"]), Decimal(sc["base"]["cm0"]))
        self.assertLess(Decimal(sc["base"]["cm0"]), Decimal(sc["opt"]["cm0"]))
        self.assertIn("duty_missing", r["flags"])

    def test_english_inputs_same_answers(self):
        r = compute("english")
        self.assertEqual(r["status"], "complete")
        self.assertEqual(base(r)["cm0"], "16.55")
        self.assertEqual(profit.detect_lang(fixture("english")), "en")
        self.assertEqual(profit.detect_lang(fixture("healthy")), "zh")

    def test_roas_given_sets_cpa(self):
        path = fixture("healthy")
        with open(path, encoding="utf-8") as fh:
            text = fh.read().replace(
                "测试梳,获客成本,10,,,USD,", "测试梳,广告 ROAS,4,,,,"
            )
        tmp = tempfile.mkdtemp(prefix="profit-model-test-")
        try:
            p = os.path.join(tmp, "inputs.csv")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
            b = base(compute(p))
            self.assertEqual(b["cpa"], "10.00")  # 39.99 / 4 = 9.9975
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Rounding(unittest.TestCase):
    def test_rounding_direction(self):
        d = Decimal("2.41001")
        self.assertEqual(pm_calc.up(d), "2.42")
        self.assertEqual(pm_calc.down(Decimal("16.559")), "16.55")
        self.assertEqual(pm_calc.ceil_int(Decimal("10.01")), 11)
        self.assertEqual(pm_calc.money(Decimal("1.455")), "1.46")
        self.assertEqual(str(pm_calc.charm_price(Decimal("39.25"))), "39.99")
        self.assertEqual(str(pm_calc.charm_price(Decimal("39.995"))), "40.99")

    def test_no_float(self):
        for name in ("pm_calc.py", "pm_inputs.py"):
            with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
                self.assertNotIn("float(", fh.read(), name)


def ue_model_contribution(
    price, units, goods, last_mile, pay_pct, pay_fixed, return_pct
):
    """Port of category-research ue_model.model() (reserve view, no duty,
    no 3PL items), for the parity check."""
    dutiable = units * goods
    logistics = last_mile
    payment = price * pay_pct / 100 + pay_fixed
    returns = return_pct / 100 * (dutiable + last_mile)
    return price - (dutiable + logistics + payment + returns)


class Parity(TmpMixin, unittest.TestCase):
    def test_replace_mode_parity(self):
        p = self.csv(
            "全店,销售市场,US,,,,\n全店,汇率,7.2,,,CNY,\n全店,收款费率,3,,,%,\n"
            "全店,收款每笔固定费,0.3,,,USD,\nA,售价,30,,,USD,\nA,采购单价,72,,,CNY,\n"
            "A,尾程运费,6,,,USD,\nA,退货率,10,,,%,\nA,每次退货运费,0,,,USD,\n"
            "A,退货处理方式,补发,,,,\nA,关税合计税率,0,,,%,\n"
        )
        mine = Decimal(compute(p)["skus"][0]["scenarios_raw"]["base"]["cm0"])
        ref = ue_model_contribution(
            Decimal(30),
            1,
            Decimal(72) / Decimal("7.2"),
            Decimal(6),
            Decimal(3),
            Decimal("0.3"),
            Decimal(10),
        )
        self.assertLess(abs(mine - ref), Decimal("0.01"))


class Validation(TmpMixin, unittest.TestCase):
    def test_blocked(self):
        r = compute("blocked")
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["exit_code"], 3)
        self.assertIn(
            ("block", "not_number", "price"),
            [(i["level"], i["code"], i["key"]) for i in r["issues"]],
        )
        run, raw, md, _r = self.run_pipeline(fixture("blocked"))
        self.assertIn("需要你补的", md)
        self.assertIn("售价", md.split("**需要你补的**")[1])
        self.assertIn("完成状态: failed", md)
        rc, _out = quiet(pm_calc.main, [run])
        self.assertEqual(rc, 3)

    def test_percent_warning(self):
        with open(fixture("healthy"), encoding="utf-8") as fh:
            body = fh.read().split("\n", 1)[1]
        p = self.csv(body.replace("测试梳,退货率,8,", "测试梳,退货率,0.08,"))
        r = compute(p)
        w = [i for i in r["issues"] if i["code"] == "fraction_suspect"]
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["params"]["pct"], "8")
        e = pm_inputs.normalize(p)["skus"][0]["fields"]["return_rate_pct"]
        self.assertEqual(e["base"], "0.08")
        _run, _raw, md, _r = self.run_pipeline(p)
        self.assertIn("疑似把 8% 写成 0.08", md)

    def test_eu_needs_vat_and_currency(self):
        p = self.csv(
            "全店,销售市场,德国,,,,\n全店,汇率,7.8,,,CNY,\n"
            "全店,收款方式,Stripe,,,,\nA,售价,29.99,,,EUR,\n"
            "A,采购单价,40,,,,\nA,尾程运费,4,,,EUR,\n"
        )
        r = compute(p)
        codes = set((i["code"], i["key"]) for i in r["issues"] if i["level"] == "block")
        self.assertIn(("vat_missing", "vat_pct"), codes)
        self.assertIn(("currency_missing", "product_cost"), codes)

    def test_fee_table_default_and_third_party(self):
        p = self.csv(
            "全店,销售市场,US,,,,\n全店,汇率,7.2,,,CNY,\n"
            "全店,收款方式,PayPal 中国账户,,,,\nA,售价,20,,,USD,\n"
            "A,采购单价,30,,,CNY,\nA,尾程运费,5,,,USD,\n"
        )
        n = pm_inputs.normalize(p)
        f = n["skus"][0]["fields"]
        self.assertEqual(
            (f["payment_pct"]["base"], f["payment_pct"]["source"]), ("4.4", "table")
        )
        self.assertEqual(f["third_party_txn_pct"]["base"], "2")
        self.assertEqual(f["third_party_txn_pct"]["badge"], "inferred")  # plan assumed
        self.assertEqual(n["store"]["plan_fee_month"]["base"], "39")

    def test_two_forms_and_limit(self):
        p = self.csv(
            "全店,销售市场,US,,,,\n全店,汇率,7.2,,,CNY,\n"
            "全店,收款费率,3,,,%,\n全店,尾程运费,5,,,USD,\n"
            "全店,采购单价,30,,,CNY,\nA,售价,20,,,USD,\n"
            "A-两件装,售价,36,,,USD,\nA-两件装,每单件数,2,,,件,\n"
            "C,售价,50,,,USD,\n"
        )
        r = compute(p)
        self.assertEqual([s["name"] for s in r["skus"]], ["A", "A-两件装"])
        self.assertIn("too_many_skus", [i["code"] for i in r["issues"]])
        self.assertEqual(base(r, 1)["G"], "72.00")

    def test_template_rows_are_blank(self):
        n = pm_inputs.normalize(
            os.path.join(SKILL, "templates", "inputs-template.zh.csv")
        )
        self.assertEqual(n["status"], "failed")
        n = pm_inputs.normalize(
            os.path.join(SKILL, "templates", "inputs-template.en.csv")
        )
        self.assertEqual(n["status"], "failed")


class FeeTable(unittest.TestCase):
    def test_stale_table(self):
        data = pm_fees.load()
        retrieved = datetime.date(2026, 10, 27)
        t100 = pm_fees.FeeTable(data, retrieved + datetime.timedelta(days=100))
        t200 = pm_fees.FeeTable(data, retrieved + datetime.timedelta(days=200))
        e = t100.get("shopify-plan-us-basic")
        self.assertEqual(t100.stale_level(e), "warn")
        self.assertEqual(t100.badge(e), "verified")
        self.assertEqual(t200.stale_level(e), "downgrade")
        self.assertEqual(t200.badge(e), "inferred")
        n = pm_inputs.Normalizer(pm_inputs.read_rows(fixture("negative")), t200).run()
        self.assertIn("stale_fee", n["flags"])
        self.assertEqual(n["status"], "partial")

    def test_unverified_never_default(self):
        t = pm_fees.FeeTable(pm_fees.load())
        self.assertFalse(t.usable_default(t.get("tiktok-us-new-seller")))
        self.assertEqual(t.badge(t.get("tiktok-us-referral")), "conflicting")

    def test_ecb_parse_and_fallback(self):
        xml = (
            "<Cube time='2026-10-26'><Cube currency='USD' rate='1.1000'/>"
            "<Cube currency='CNY' rate='7.9200'/></Cube>"
        )
        data = pm_fx.parse(xml)
        self.assertEqual(data["rates"]["USD"], "7.2000")
        rows = pm_inputs.read_rows(fixture("healthy"))
        rows = [r for r in rows if r["item"] != "汇率"]
        n = pm_inputs.Normalizer(rows, pm_fees.FeeTable(pm_fees.load()), data).run()
        self.assertEqual((n["fx"]["source"], n["fx"]["badge"]), ("ecb", "verified"))
        self.assertEqual(n["status"], "partial")
        n = pm_inputs.Normalizer(rows, pm_fees.FeeTable(pm_fees.load())).run()
        self.assertEqual(n["status"], "failed")


def table_shape_errors(md):
    """Lines of markdown tables whose cell count differs from the header."""
    bad, width = [], None
    for line in md.splitlines():
        if not line.startswith("|"):
            width = None
            continue
        n = line.strip().strip("|").count("|") + 1
        if width is None:
            width = n
        elif n != width:
            bad.append(line)
    return bad


class Delivery(TmpMixin, unittest.TestCase):
    def ending(self, lang):
        path = os.path.join(SKILL, "references", "report-template.%s.md" % lang)
        with open(path, encoding="utf-8") as fh:
            return re.findall(r"```markdown\n(.*?)\n```", fh.read(), re.S)[-1]

    def test_reports_pass_self_check(self):
        for name in ("healthy", "negative", "missing_invalid", "blocked"):
            for lang in ("zh", "en"):
                shutil.rmtree(self.tmp, ignore_errors=True)
                os.makedirs(self.tmp)
                run, raw, md, _r = self.run_pipeline(fixture(name), lang)
                res = checks.check_text(md, cfg.profile(lang, raw))
                self.assertEqual(
                    [(i.kind, i.text) for i in res.errors], [], (name, lang)
                )
                self.assertEqual(
                    [(i.kind, i.text) for i in res.warnings], [], (name, lang)
                )
                self.assertIn(self.ending(lang).strip(), md)
                self.assertEqual(table_shape_errors(md), [], (name, lang))
                for word in ("ue_model", "pm_calc", "Decimal", "稳赚"):
                    self.assertNotIn(word, md.split("## 附录")[0])

    def test_csv_bom(self):
        run, _raw, _md, _r = self.run_pipeline(fixture("healthy"))
        path = os.path.join(run, "每单测算.csv")
        with open(path, "rb") as fh:
            head = fh.read(40)
        self.assertTrue(head.startswith(b"\xef\xbb\xbf"))
        self.assertIn("商品,情景,售价", head.decode("utf-8-sig") + "情景,售价")
        with open(os.path.join(run, "敏感度.csv"), encoding="utf-8-sig") as fh:
            self.assertEqual(len(fh.read().strip().splitlines()), 4)

    def test_report_statements(self):
        _run, _raw, md, _r = self.run_pipeline(fixture("negative"))
        self.assertIn("每卖一单亏 $10.98", md)
        self.assertIn("底线价 $49.23", md)
        _run, _raw, md, _r = self.run_pipeline(fixture("missing_invalid"))
        summary = md.split("## 1.")[0]
        self.assertIn("未含关税", summary)
        self.assertIn("[查不到]", summary)
        self.assertIn("平均折扣 填了 120", md)
        self.assertIn("完成状态: partial", md)

    def test_english_report(self):
        run, _raw, md, _r = self.run_pipeline(fixture("healthy"), "en")
        self.assertIn("## At a glance", md)
        self.assertIn("Break-even ROAS 2.42", md)
        self.assertTrue(os.path.isfile(os.path.join(run, "per-order.csv")))
        self.assertNotRegex(md.split("## Appendix")[0], "[一-鿿]{2,}利润")

    def test_init_then_run(self):
        rc, out = quiet(
            profit.main, ["init", "--product", "宠物梳", "--out-root", self.tmp]
        )
        self.assertEqual(rc, 0)
        csv_path = re.search(r"输入文件: (.*)", out).group(1).strip()
        with open(fixture("healthy"), encoding="utf-8") as fh:
            body = fh.read().replace("测试梳", "宠物梳")
        with open(csv_path, "w", encoding="utf-8") as fh:
            fh.write(body)
        run = os.path.dirname(os.path.dirname(csv_path))
        rc, out = quiet(
            profit.main, ["run", run, "--no-open", "--html-only", "--no-fx-fetch"]
        )
        self.assertEqual(rc, 0, out)
        self.assertIn("完成状态: complete", out)
        with open(
            os.path.join(run, "原始数据", "delivery.json"), encoding="utf-8"
        ) as fh:
            rec = json.load(fh)
        self.assertEqual(rec["status"], "complete")
        self.assertEqual(
            rec["check"]["errors"],
            [] if isinstance(rec["check"]["errors"], list) else 0,
        )


class CheckRules(TmpMixin, unittest.TestCase):
    def setUp(self):
        super(CheckRules, self).setUp()
        _run, self.raw, self.md, _r = self.run_pipeline(fixture("missing_invalid"))
        self.profile = cfg.profile("zh", self.raw)

    def kinds(self, md):
        return set(i.kind for i in checks.check_text(md, self.profile).errors)

    def test_clean(self):
        self.assertEqual(self.kinds(self.md), set())

    def test_untraced_number(self):
        md = self.md.replace(
            "## 1. 你的输入", "- 每单还能多赚 $3.33 [推测] 依据: 估计\n\n## 1. 你的输入"
        )
        self.assertIn("untraced_number", self.kinds(md))

    def test_result_needs_badge(self):
        md = self.md.replace("## 1. 你的输入", "- 每单贡献 $16.95\n\n## 1. 你的输入")
        self.assertIn("result_no_badge", self.kinds(md))

    def test_verified_needs_date(self):
        md = self.md.replace(
            "## 1. 你的输入", "- 收款费率没问题 [已核实]\n\n## 1. 你的输入"
        )
        self.assertIn("verified_no_date", self.kinds(md))

    def test_promise_forbidden(self):
        md = self.md.replace("## 1. 你的输入", "- 这个款稳赚\n\n## 1. 你的输入")
        self.assertIn("forbidden_term", self.kinds(md))

    def test_duty_note_required(self):
        md = self.md.replace("未含关税", "关税另算")
        self.assertIn("no_duty_note", self.kinds(md))

    def test_assumptions_required(self):
        md = self.md.replace("## 2. 假设清单", "## 2. 补充")
        self.assertIn("no_assumptions", self.kinds(md))

    def test_check_report_cli(self):
        rc, _out = quiet(check_report.main, [os.path.dirname(self.raw)])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
