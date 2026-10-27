#!/usr/bin/env python3
"""Step 3 (offline): result.json -> report.md + summary.json (CSV rows).

CLI:
    python pm_report.py "<run folder>" [--lang zh|en]

Every number in the report body comes from result.json or the inputs; the
self-check (check_report.py) verifies that. summary.json carries the status,
language, product name and the CSV rows deliver.py writes.
Python 3.8+, standard library only.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edition  # noqa: E402
import pm_fees  # noqa: E402
import pm_fields as F  # noqa: E402
import pm_text as X  # noqa: E402
from _shared import evidence, i18n, output  # noqa: E402

SCEN = ("cons", "base", "opt")
LEDGER_ROWS = (
    "G",
    "vat",
    "N",
    "refund",
    "goods",
    "ful",
    "last",
    "ret_ship",
    "replacement",
    "pay",
    "other",
    "cm0",
    "cm0_pct_n",
    "cm0_pct_g",
    "cpa",
    "cm1",
)


class Writer(object):
    def __init__(self, result, norm, lang, today=None, raw_dir=None):
        self.r = result
        self.n = norm
        self.lang = lang
        self.t = X.T[lang]
        self.h = X.H[lang]
        self.cur = result.get("currency") or ""
        self.sym = X.SYMBOL.get(self.cur, self.cur + " ")
        self.today = today or datetime.date.today().isoformat()
        self.lines = []
        self.raw_dir = raw_dir  # where extra files (charts) may be written
        self.assume_by_id = dict((a["id"], a) for a in result.get("assumptions", []))
        self.fees = pm_fees.FeeTable(pm_fees.load())

    # -- helpers ---------------------------------------------------------

    def add(self, *lines):
        self.lines.extend(lines)

    def m(self, value, sym=None):
        if value is None:
            return self.t["na"]
        sym = self.sym if sym is None else sym
        v = str(value)
        if v.startswith("-"):
            return "-%s%s" % (sym, v[1:])
        return "%s%s" % (sym, v)

    def name(self, key):
        return F.name(key, self.lang)

    def badge(self, key, ids=None, text=None):
        """Badge with its basis when it is [推测]."""
        tag = evidence.badge(key or "not_found", self.lang)
        if key == "inferred":
            if ids:
                tag += " " + self.t["basis"].format(ids=compress_ids(ids))
            else:
                tag += " " + self.t["basis_plain"].format(text=text or "-")
        return tag

    def mb(self, metric):
        return self.badge(metric["badge"], metric.get("basis"))

    def provider(self, value):
        return X.PROVIDER[self.lang].get(value, value)

    def choice(self, value):
        return X.CHOICE[self.lang].get(value, value)

    # -- report ----------------------------------------------------------

    def build(self):
        skus = self.r["skus"]
        computed = [s for s in skus if not s["blocked"]]
        title = computed[0]["name"] if computed else (skus[0]["name"] if skus else "")
        self.add(self.h["title"].format(name=title), "")
        self.meta()
        self.summary(skus)
        if computed:
            self.inputs()
            self.assumptions()
            for s in computed:
                self.ledger(s, len(computed) > 1)
            for s in computed:
                self.ads(s, len(computed) > 1)
            self.store()
            for s in computed:
                self.pricing(s, len(computed) > 1)
            for s in computed:
                self.sensitivity(s, len(computed) > 1)
            self.startup()
            pro = self.pro_sections()
            self.duty(computed[0])
            self.add(*pro)
        else:
            self.inputs()
        self.check()
        self.appendix()
        self.add(
            X.ENDING[self.lang].format(
                upsell=edition.UPSELL[self.lang], generator=edition.GENERATOR
            )
        )
        return "\n".join(self.lines)

    def pro_sections(self):
        try:
            import pm_pro
        except ImportError:
            return []
        if "pro" not in self.r:
            return []
        return pm_pro.report_lines(self, self.r["pro"])

    def meta(self):
        fx = self.r["fx"]
        if fx.get("base") is None:
            return
        if fx["source"] == "ecb":
            info = self.h["fx_ecb"].format(date=fx.get("date"))
        elif fx.get("date") or fx.get("source_text"):
            info = self.h["fx_user"].format(
                date=fx.get("date") or self.t["unknown"],
                src=fx.get("source_text") or self.t["unknown"],
            )
        else:
            info = self.h["fx_user_nodate"]
        self.add(
            self.h["meta"].format(
                date=self.today,
                market=self.r.get("market") or "-",
                currency=self.cur,
                fx=fx["base"],
                fxinfo=info,
                fxbadge=evidence.badge(fx["badge"], self.lang),
            ),
            "",
        )

    def summary(self, skus):
        t = self.t
        self.add(self.h["summary"], "")
        multi = len(skus) > 1
        for s in skus:
            if multi:
                self.add(self.h["sku"].format(name=s["name"]), "")
            if s["blocked"]:
                self.add(t["s_blocked"].format(name=s["name"]))
                continue
            mt = s["metrics"]
            cm0 = mt["cm0"]
            if cm0["value"].startswith("-") or cm0["value"] == "0.00":
                self.add(
                    t["s_loss"].format(
                        loss=self.m(cm0["value"].lstrip("-")), b=self.mb(cm0)
                    )
                )
            else:
                self.add(
                    t["s_profit"].format(
                        cm0=self.m(cm0["value"]),
                        pct_g=mt["cm0_pct_g"]["value"],
                        b=self.mb(cm0),
                    )
                )
            if s["has_ads"]:
                cm1 = mt["cm1"]
                if cm1["value"].startswith("-"):
                    self.add(
                        t["s_cm1_loss"].format(
                            cpa=self.m(mt["cpa"]["value"]),
                            loss=self.m(cm1["value"][1:]),
                            b=self.mb(cm1),
                        )
                    )
                else:
                    self.add(
                        t["s_cm1"].format(
                            cpa=self.m(mt["cpa"]["value"]),
                            cm1=self.m(cm1["value"]),
                            b=self.mb(cm1),
                        )
                    )
            else:
                self.add(t["s_noads"])
            if mt["roas_be"]["value"] is None:
                self.add(t["s_roas_na"].format(b=self.mb(cm0)))
            else:
                self.add(
                    t["s_roas"].format(
                        roas=mt["roas_be"]["value"],
                        cpa=self.m(mt["max_cpa"]["value"]),
                        b=self.mb(mt["roas_be"]),
                    )
                )
            p = s["price"]
            pb = self.badge(p["badge"], p["basis"])
            if p["floor"] is None or p["target"] is None:
                self.add(t["s_price_na"].format(b=pb))
            else:
                self.add(
                    t["s_price"].format(
                        floor=self.m(p["floor"]),
                        ads=t["ads_incl"] if p["with_ads"] else t["ads_excl"],
                        target=self.m(p["target"]),
                        m=p["margin_pct"],
                        disp=self.m(p["target_display"]),
                        cur=self.m(p["current"]),
                        b=pb,
                    )
                )
            sc = s["scenarios"]
            self.add(
                t["s_scen"].format(
                    cons=self.m(sc["cons"]["cm0"]),
                    base=self.m(sc["base"]["cm0"]),
                    opt=self.m(sc["opt"]["cm0"]),
                    b=self.mb(cm0),
                )
            )
            top = [self.name(r["key"]) for r in s["sensitivity"][:3]]
            if top:
                self.add(t["s_top"].format(names=", ".join(top), b=self.mb(mt["cm1"])))
            if s["duty_line"]["duty_missing"]:
                self.add(
                    t["s_duty_missing"].format(
                        loss=self.m(s["duty_line"]["loss"]), b=self.mb(cm0)
                    )
                )
            roas = mt["roas_be"]["value"]
            if roas is None:
                self.add(t["s_judge_neg"])
            elif float_le(roas, "2"):
                self.add(t["s_judge_wide"].format(roas=roas))
            elif float_le(roas, "3"):
                self.add(t["s_judge_mid"].format(roas=roas))
            else:
                self.add(t["s_judge_narrow"].format(roas=roas))
        st = self.r.get("store") or {}
        if st:
            self.summary_store(st)
        flags = self.partial_reasons()
        if self.r["status"] == "partial":
            self.add(
                t["s_partial_note"].format(
                    why="; ".join(X.PARTIAL_WHY[self.lang][f] for f in flags)
                )
            )
        self.add(t["s_cond"], "")

    def summary_store(self, st):
        t = self.t
        fix = st["fixed"]
        be = st["breakeven"]
        bb = self.badge(st["breakeven_badge"], st["breakeven_basis"])
        first = be[0]
        if first["units"] is None:
            self.add(t["s_fixed_na"].format(fix=self.m(fix["total"]), b=bb))
        elif first["tier"] == "user":
            self.add(
                t["s_fixed"].format(
                    fix=self.m(fix["total"]),
                    cpa=self.m(first["cpa"]),
                    units=first["units"],
                    b=bb,
                )
            )
        else:
            self.add(
                t["s_fixed_zero"].format(
                    fix=self.m(fix["total"]), units=first["units"], b=bb
                )
            )
        main = st["startup"]["main"]
        missing = ""
        if main["missing"]:
            missing = t["s_startup_missing"].format(n=len(main["missing"]))
        self.add(
            t["s_startup"].format(
                total=self.m(main["total_known"]),
                cny=main["total_known_cny"],
                missing=missing,
                b=""
                if main["missing"] and main["badge"] == "not_found"
                else self.badge(
                    main["badge"] or "not_found", None, self.t["see_startup"]
                ),
            )
        )

    def partial_reasons(self):
        flags = list(self.r.get("flags") or [])
        if any(s["blocked"] for s in self.r["skus"]) and any(
            not s["blocked"] for s in self.r["skus"]
        ):
            flags.append("blocked_sku")
        return [f for f in flags if f in X.PARTIAL_WHY[self.lang]]

    # -- sections --------------------------------------------------------

    def inputs(self):
        t = self.t
        self.add(self.h["inputs"], "")
        rows = []
        fx = self.r["fx"]
        if fx.get("source") == "user":
            rows.append((t["store_name"], "fx_cny_per_unit", fx, "CNY"))
        for s in self.n["skus"]:
            for key in F.ORDER:
                e = s["fields"].get(key)
                if e and e.get("source") == "user":
                    rows.append((s["name"], key, e, e.get("currency")))
        for key in F.ORDER:
            e = self.n["store"].get(key)
            if e and e.get("source") == "user":
                rows.append((t["store_name"], key, e, e.get("currency")))
        if not rows:
            self.add(t["inputs_none"], "")
            return
        self.add(t["inputs_head"], "|---|---|---|---|---|---|---|---|")
        for prod, key, e, cur in rows:
            base = e.get("base") or self.choice(e.get("value")) or e.get("raw") or ""
            if key == "payment_provider":
                base = self.provider(e.get("value"))
            unit = cur or ("%" if F.FIELDS[key]["kind"] in ("pct", "share") else "")
            cons = e.get("cons") if e.get("ranged") else "-"
            opt = e.get("opt") if e.get("ranged") else "-"
            note = (e.get("note") or e.get("source_text") or "").replace("|", "/")
            self.add(
                "| %s | %s | %s | %s | %s | %s | %s | %s |"
                % (
                    prod,
                    self.name(key),
                    base,
                    cons or "-",
                    opt or "-",
                    unit,
                    note,
                    evidence.badge("user_input", self.lang),
                )
            )
        self.add("", t["inputs_note"], "")

    def assumption_row(self, a):
        tx = X.ASSUME[self.lang]
        p = a.get("params") or {}
        code = a["code"]
        key = a["key"]
        v = p.get("value")
        if key and v is not None and F.FIELDS[key]["kind"] == "money":
            e = self.find_entry(a)
            v = self.m(v, X.SYMBOL.get((e or {}).get("currency") or self.cur, ""))
        elif key and v is not None and F.FIELDS[key]["kind"] in ("pct", "share"):
            v = "%s%%" % v
        if code == "table_value" and p.get("assumed"):
            code = "table_value_assumed"
        val_t, note_t = tx[code]
        hints = ""
        if p.get("hints"):
            items = []
            for hid in p["hints"]:
                entry = self.fees.get(hid)
                if entry:
                    items.append("%s %s" % (hid, pm_fees.value_text(entry)))
            hints = tx["zero_hint"].format(items=", ".join(items))
        assumed = (
            tx.get("assumed_%s" % p.get("assumed"), "") if p.get("assumed") else ""
        )
        fmt = dict(
            v=v if v is not None else "",
            tid=p.get("table_id", ""),
            retrieved=p.get("retrieved", ""),
            tv=p.get("table_value", ""),
            date=p.get("date", ""),
            hints=hints,
            assumed=assumed,
        )
        if code == "currency_from_market":
            fmt["v"] = p.get("currency", "")
        if code == "return_ship_default":
            e = self.find_entry(dict(a, key="last_mile_per_order"))
            if e:
                val_t = "%s (%s)" % (val_t, self.m(e["base"]))
        return val_t.format(**fmt), note_t.format(**fmt)

    def find_entry(self, a):
        if a["sku"] is None:
            e = self.n["store"].get(a["key"])
            if e:
                return e
            for s in self.n["skus"]:
                if a["key"] in s["fields"]:
                    return s["fields"][a["key"]]
            return None
        for s in self.n["skus"]:
            if s["name"] == a["sku"]:
                return s["fields"].get(a["key"])
        return None

    def assumptions(self):
        t = self.t
        self.add(self.h["assumptions"], "", t["assume_intro"], "")
        self.add(t["assume_head"], "|---|---|---|---|---|")
        blocked = set(s["name"] for s in self.r["skus"] if s["blocked"])
        for a in self.r["assumptions"]:
            if a["sku"] in blocked:
                continue
            value, note = self.assumption_row(a)
            item = self.name(a["key"]) if a["key"] else "-"
            if a["code"] in ("rate_matches_table", "rate_differs_table"):
                item = self.name("payment_pct")
            self.add(
                "| %s | %s | %s | %s | %s |"
                % (a["id"], a["sku"] or t["store_name"], item, value, note)
            )
        self.add("")

    def ledger(self, s, multi):
        t = self.t
        self.add(self.h["ledger"] + (" - %s" % s["name"] if multi else ""), "")
        self.add(t["ledger_head"], "|---|---:|---:|---:|---|")
        sc = s["scenarios"]
        replace = sc["base"]["replacement"] not in (None, "0.00")
        vat = sc["base"]["vat"] not in (None, "0.00")
        for row in LEDGER_ROWS:
            if row == "vat" and not vat:
                continue
            if row == "replacement" and not replace:
                continue
            if row == "refund" and replace:
                continue
            if row in ("cpa", "cm1") and not s["has_ads"]:
                continue
            vals = []
            for c in SCEN:
                v = sc[c][row]
                if row.endswith("_pct_n") or row.endswith("_pct_g"):
                    vals.append("%s%%" % v if v is not None else t["na"])
                else:
                    vals.append(self.m(v))
            self.add(
                "| %s | %s | %s | %s | %s |"
                % (
                    X.ROWS[self.lang][row],
                    vals[0],
                    vals[1],
                    vals[2],
                    self.mb(s["metrics"][row]),
                )
            )
        self.add("", t["diff_title"])
        diffs = s["diffs"]
        if not diffs["cons"] and not diffs["opt"]:
            self.add(t["diff_none"])
        for c in ("cons", "opt"):
            if not diffs[c]:
                continue
            items = [
                t["diff_item"].format(
                    name=self.name(d["key"]),
                    value=self.fmt_value(d["key"], d["value"], d),
                    base=self.fmt_value(d["key"], d["base"], d),
                )
                for d in diffs[c]
            ]
            self.add(
                t["diff_line"].format(scen=X.SCEN[self.lang][c], items="; ".join(items))
                + " "
                + self.diff_badge(s["name"], diffs[c])
            )
        self.add("")

    def diff_badge(self, sku, diffs):
        """Weakest label among the inputs that differ from the base."""
        fields = next(x for x in self.n["skus"] if x["name"] == sku)["fields"]
        badges, ids = [], []
        for d in diffs:
            e = self.r["fx"] if d["key"] == "fx_cny_per_unit" else fields.get(d["key"])
            e = e or self.n["store"].get(d["key"]) or {}
            if e.get("badge"):
                badges.append(e["badge"])
            if e.get("assumption"):
                ids.append(e["assumption"])
        return self.badge(evidence.weakest(badges) or "user_input", ids)

    def fmt_value(self, key, value, entry):
        kind = F.FIELDS[key]["kind"]
        if kind == "money":
            return self.m(value, X.SYMBOL.get(entry.get("currency") or self.cur, ""))
        if kind in ("pct", "share"):
            return "%s%%" % value
        return value

    def ads(self, s, multi):
        t = self.t
        mt = s["metrics"]
        self.add(self.h["ads"] + (" - %s" % s["name"] if multi else ""), "")
        if mt["roas_be"]["value"] is None:
            self.add(t["s_roas_na"].format(b=self.mb(mt["cm0"])))
        else:
            self.add(
                t["ads_roas"].format(
                    roas=mt["roas_be"]["value"], b=self.mb(mt["roas_be"])
                )
            )
            self.add(
                t["ads_cpa"].format(
                    cpa=self.m(mt["max_cpa"]["value"]), b=self.mb(mt["max_cpa"])
                )
            )
        mpct = s["price"]["margin_pct"]
        if mt["target_roas"]["value"] is None:
            self.add(t["ads_target_na"].format(m=mpct, b=self.mb(mt["target_cpa"])))
        else:
            self.add(
                t["ads_target"].format(
                    m=mpct,
                    tcpa=self.m(mt["target_cpa"]["value"]),
                    troas=mt["target_roas"]["value"],
                    b=self.mb(mt["target_cpa"]),
                )
            )
        self.add(
            t["ads_rates"].format(
                n=mt["cm0_pct_n"]["value"],
                g=mt["cm0_pct_g"]["value"],
                b=self.mb(mt["cm0_pct_g"]),
            ),
            "",
        )
        self.add(t["ads_scen_head"], "|---|---:|---:|---|")
        for c in SCEN:
            sc = s["scenarios"][c]
            self.add(
                "| %s | %s | %s | %s |"
                % (
                    X.SCEN[self.lang][c],
                    sc["roas_be"] or t["na"],
                    self.m(sc["max_cpa"]),
                    self.mb(mt["roas_be"]),
                )
            )
        self.add("")

    def store(self):
        t = self.t
        st = self.r["store"]
        self.add(self.h["store"], "")
        self.add(t["store_head"], "|---|---:|---|")
        for it in st["fixed"]["items"]:
            if it["source"] == "na":
                continue
            ids = [it["assumption"]] if it.get("assumption") else []
            b = self.badge(it["badge"] or "not_found", ids)
            if it["badge"] == "verified" and it.get("assumption"):
                a = self.assume_by_id.get(it["assumption"])
                b += " %s" % ((a or {}).get("params", {}).get("retrieved") or "")
            self.add(
                "| %s | %s | %s |" % (self.name(it["key"]), self.m(it["value"]), b)
            )
        self.add(
            t["store_total"].format(
                total=self.m(st["fixed"]["total"]),
                b=self.badge(st["fixed"]["badge"], st["fixed"]["basis"]),
            ),
            "",
        )
        self.add(t["be_head"], "|---|---:|---:|---|")
        bb = self.badge(st["breakeven_badge"], st["breakeven_basis"])
        for row in st["breakeven"]:
            label = t["be_%s" % row["tier"]].format(cpa=self.m(row["cpa"]))
            units = row["units"] if row["units"] is not None else t["be_na"]
            self.add("| %s | %s | %s | %s |" % (label, self.m(row["cm1"]), units, bb))
        self.add("", t["be_note"], "")

    def pricing(self, s, multi):
        t = self.t
        p = s["price"]
        pb = self.badge(p["badge"], p["basis"])
        self.add(self.h["pricing"] + (" - %s" % s["name"] if multi else ""), "")
        if p["floor"] is None or p["target"] is None:
            self.add(t["price_na"].format(b=pb), "")
            return
        self.add(
            t["price_floor"].format(
                floor=self.m(p["floor"]),
                ads=t["ads_incl"] if p["with_ads"] else t["ads_excl"],
                b=pb,
            )
        )
        self.add(
            t["price_target"].format(
                target=self.m(p["target"]),
                m=p["margin_pct"],
                disp=self.m(p["target_display"]),
                b=pb,
            ),
            "",
        )
        gap = p["gap_to_floor"]
        if gap.startswith("-"):
            self.add(
                t["price_r1_below"].format(
                    cur=self.m(p["current"]), gap=self.m(gap[1:]), b=pb
                )
            )
        else:
            self.add(
                t["price_r1_above"].format(
                    cur=self.m(p["current"]), gap=self.m(gap), b=pb
                )
            )
        if not float_lt(p["current"], p["target"]):
            self.add(t["price_r2_ok"].format(b=pb))
        else:
            risks = [r for r in s["sensitivity"] if not r["lever"]][:2]
            if len(risks) == 2:
                gains = [self.gain(r) for r in risks]
                self.add(
                    t["price_r2"].format(
                        target=self.m(p["target"]),
                        a=self.name(risks[0]["key"]),
                        c=self.name(risks[1]["key"]),
                        da=self.m(gains[0]),
                        dc=self.m(gains[1]),
                        b=pb,
                    )
                )
            else:
                self.add(t["price_r2_short"].format(target=self.m(p["target"]), b=pb))
        lo, hi = p.get("competitor_low"), p.get("competitor_high")
        if lo and hi:
            if float_lt(p["current"], lo):
                pos = t["pos_below"]
            elif float_lt(hi, p["current"]):
                pos = t["pos_above"]
            else:
                pos = t["pos_in"]
            self.add(t["price_r3"].format(lo=self.m(lo), hi=self.m(hi), pos=pos))
        else:
            self.add(t["price_r3_none"])
        self.add("")

    @staticmethod
    def gain(row):
        """Profit gained when the input improves by 10% (the better side)."""
        up, down = row["delta_up"], row["delta_down"]
        return up if not up.startswith("-") else down

    def sensitivity(self, s, multi, limit=3):
        t = self.t
        self.add(self.h["sens"] + (" - %s" % s["name"] if multi else ""), "")
        self.add(t["sens_intro"], "")
        self.add(t["sens_head"], "|---|---|---|---:|---:|---:|---|")
        b = self.mb(s["metrics"]["cm1"])
        for r in s["sensitivity"][:limit]:
            kind = t["sens_lever"] if r["lever"] else t["sens_risk"]
            self.add(
                "| %d | %s | %s | %s | %s | %s | %s |"
                % (
                    r["rank"],
                    self.name(r["key"]),
                    kind,
                    self.m(r["cm1_down"]),
                    self.m(r["cm1_up"]),
                    self.m(r["impact"]),
                    b,
                )
            )
        self.add(
            "",
            t["sens_duty"].format(
                loss=self.m(s["duty_line"]["loss"]), b=self.mb(s["metrics"]["cm0"])
            ),
            "",
        )

    def startup(self):
        t = self.t
        st = self.r["store"]["startup"]
        self.add(self.h["startup"], "")
        for route in ("main", "lean"):
            r = st[route]
            self.add("**%s**" % X.STARTUP[self.lang][route], "")
            self.add(t["startup_head"], "|---|---:|---:|---|---|")
            for it in r["items"]:
                note = ""
                if it["key"] == "goods" and r["qty"]:
                    note = t["startup_qty"].format(qty=r["qty"])
                elif it["key"] == "fixed_runway":
                    note = t["startup_runway"].format(months=it["note"])
                elif it["note"] == "duty_per_order":
                    note = t["startup_duty_order"]
                elif it["note"] == "duty_missing":
                    note = t["startup_duty_missing"]
                elif it["value"] is None:
                    note = t["startup_nf"]
                ids = [it["assumption"]] if it.get("assumption") else None
                badge = self.badge(
                    it["badge"] if it["value"] is not None else "not_found",
                    ids,
                    note or "-",
                )
                cny = "¥%s" % it["cny"] if it["cny"] is not None else "-"
                self.add(
                    "| %s | %s | %s | %s | %s |"
                    % (
                        X.STARTUP[self.lang][it["key"]],
                        self.m(it["value"]) if it["value"] is not None else "-",
                        cny,
                        badge,
                        note,
                    )
                )
            missing = (
                t["startup_missing"].format(n=len(r["missing"])) if r["missing"] else ""
            )
            self.add(
                t["startup_total"].format(
                    total=self.m(r["total_known"]),
                    cny=r["total_known_cny"]
                    if self.lang == "zh"
                    else "CNY %s" % r["total_known_cny"],
                    b=self.badge(r["badge"] or "not_found", None, missing or "-"),
                    missing=missing,
                ),
                "",
            )
        ccc = self.r["store"]["ccc"]
        self.add(t["ccc_title"])
        if ccc["ccc"] is not None:
            self.add(
                t["ccc_full"].format(
                    ccc=ccc["ccc"],
                    dio=ccc["dio"],
                    dso=ccc["dso"],
                    dpo=ccc["dpo"],
                    b=evidence.badge("user_input", self.lang),
                )
            )
        elif ccc["known"]:
            known = ", ".join(
                "%s %s" % (self.name(k), v) for k, v in sorted(ccc["known"].items())
            )
            self.add(t["ccc_part"].format(known=known))
        else:
            self.add(t["ccc_none"])
        self.add("")

    def duty(self, s):
        t = self.t
        self.add(self.h["duty"], "", t["duty_intro"], "")
        d = s["duty_line"]
        if d["duty_missing"]:
            self.add(
                t["duty_missing"].format(
                    loss=self.m(d["loss"]), b=self.mb(s["metrics"]["cm0"])
                )
            )
        else:
            e = next(x for x in self.n["skus"] if x["name"] == s["name"])
            rate = e["fields"]["duty_rate_pct"]["base"]
            self.add(t["duty_given"].format(rate=rate, basis=self.choice(d["basis"])))
        market = self.r.get("market") or ""
        date = self.fees.data.get("valid_as_of")
        if market == "US":
            pub = self.fees.get("us-de-minimis-nonpostal")["value"]["published"]
            self.add(t["duty_us"].format(date=date, pub=pub))
        elif market.startswith("EU-"):
            e = self.fees.get("eu-low-value-duty")
            v = e["value"]
            self.add(
                t["duty_eu"].format(
                    date=date,
                    thr=v["threshold_eur"],
                    start=v["from"],
                    amt=v["amount_eur"],
                    end=v["until"],
                    pub=e.get("valid_as_of"),
                )
            )
        self.add("")

    def issue_text(self, i):
        p = dict(i.get("params") or {})
        key = i.get("key")
        p.setdefault("name", self.name(key) if key in F.FIELDS else (key or ""))
        p.setdefault("sku", i.get("sku") or "")
        if "provider" in p:
            p["provider"] = self.provider(p["provider"])
        if "col" in p:
            p["col"] = X.SCEN[self.lang].get(p["col"], p["col"])
        if "dropped" in p:
            p["dropped"] = ", ".join(p["dropped"])
        text = X.ISSUE[self.lang][i["code"]]
        try:
            text = text.format(**p)
        except (KeyError, IndexError):
            pass
        prefix = (
            "%s: " % i["sku"]
            if i.get("sku") and "{sku}" not in X.ISSUE[self.lang][i["code"]]
            else ""
        )
        return "- " + prefix + text

    def check(self):
        t = self.t
        self.add(self.h["check"], "")
        groups = (
            ("block", t["needs"]),
            ("warning", t["warns"]),
            ("error", t["handled"]),
            ("info", t["info"]),
        )
        any_issue = False
        for level, head in groups:
            items = [i for i in self.r["issues"] if i["level"] == level]
            if not items:
                continue
            any_issue = True
            if head:
                self.add(head)
            for i in items:
                self.add(self.issue_text(i))
            self.add("")
        if not any_issue:
            self.add(t["check_none"], "")

    def appendix(self):
        t = self.t
        self.add(self.h["appendix"], "")
        self.add(t["status"].format(status=self.r["status"]))
        reasons = self.partial_reasons()
        if self.r["status"] == "partial" and reasons:
            self.add(
                t["status_why"].format(
                    why="; ".join(X.PARTIAL_WHY[self.lang][f] for f in reasons)
                )
            )
        fx = self.r["fx"]
        if fx.get("base"):
            src = (
                fx.get("url")
                or fx.get("source_text")
                or evidence.badge(fx["badge"], self.lang)
            )
            self.add(
                t["app_fx"].format(
                    currency=self.cur,
                    fx=fx["base"],
                    date=fx.get("date") or t["unknown"],
                    src=src,
                )
            )
        fresh = self.fees.data.get("valid_as_of")
        note = "%s %s." % ("数据截至" if self.lang == "zh" else "Data as of", fresh)
        self.add(
            t["app_fee"].format(
                note=note, warn=self.fees.warn_days, down=self.fees.downgrade_days
            ),
            "",
        )
        ids = list(self.r.get("fees_used") or []) + [
            i
            for i in (self.r.get("fee_hints") or [])
            if i not in (self.r.get("fees_used") or [])
        ]
        if ids:
            self.add(t["app_fee_head"], "|---|---|---|---|---|---|---|")
            for fid in ids:
                e = self.fees.get(fid)
                if not e:
                    continue
                self.add(
                    "| %s | %s | %s | %s | %s | %s | %s |"
                    % (
                        fid,
                        e.get("item"),
                        pm_fees.value_text(e),
                        e.get("retrieved"),
                        e.get("valid_as_of") or "-",
                        e.get("status"),
                        e.get("source_url"),
                    )
                )
            self.add("")
        self.add(t["app_refund"], t["app_round"], t["app_manual"], t["app_files"], "")
        self.add(t["app_formula"], "")
        for f in X.FORMULAS[self.lang]:
            self.add("- " + f)
        self.add("")


def compress_ids(ids):
    """["A2", "A5", "A6", "A7"] -> "A2, A5-A7"."""
    nums = sorted(set(int(i[1:]) for i in ids))
    parts, start, prev = [], None, None
    for n in nums + [None]:
        if start is None:
            start = prev = n
            continue
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append("A%d" % start if start == prev else "A%d-A%d" % (start, prev))
        start = prev = n
    return ", ".join(parts)


def float_le(a, b):
    from decimal import Decimal

    return Decimal(str(a)) <= Decimal(str(b))


def float_lt(a, b):
    from decimal import Decimal

    return Decimal(str(a)) < Decimal(str(b))


# ---------------------------------------------------------------------------
# CSV rows (summary.json)
# ---------------------------------------------------------------------------


def csv_rows(result, norm, lang, sens_limit=3):
    lab = lambda k: evidence.badge(k or "not_found", lang)  # noqa: E731
    per_order, breakdown, startup, sens = [], [], [], []
    cur = result.get("currency")
    for s in result["skus"]:
        if s["blocked"]:
            continue
        price = s["price"]["current"]
        for c in SCEN:
            sc = s["scenarios"][c]
            per_order.append(
                {
                    "product": s["name"],
                    "scenario": X.SCEN[lang][c],
                    "price": price,
                    "G": sc["G"],
                    "N": sc["N"],
                    "L": sc["L"],
                    "ful": sc["ful"],
                    "last": sc["last"],
                    "returns": sum_str(sc["refund"], sc["ret_ship"], sc["replacement"]),
                    "pay": sc["pay"],
                    "cm0": sc["cm0"],
                    "cm0_pct": sc["cm0_pct_g"],
                    "roas_be": sc["roas_be"],
                    "max_cpa": sc["max_cpa"],
                    "cm1": sc["cm1"] if s["has_ads"] else None,
                    "badge": lab(s["metrics"]["cm1"]["badge"]),
                }
            )
        fields = next(x for x in norm["skus"] if x["name"] == s["name"])["fields"]
        for c in SCEN:
            for key in F.ORDER:
                e = fields.get(key)
                if not e or e.get("source") in ("na", None) or e.get(c) is None:
                    continue
                kind = F.FIELDS[key]["kind"]
                if kind != "money":
                    continue
                breakdown.append(
                    {
                        "product": s["name"],
                        "scenario": X.SCEN[lang][c],
                        "item": F.name(key, lang),
                        "amount": converted(e, c, result["fx"]),
                        "currency": cur,
                        "raw": e[c],
                        "raw_currency": e.get("currency"),
                        "source": e.get("table_id") or e.get("source"),
                        "badge": lab(e.get("badge")),
                    }
                )
        for r in s["sensitivity"][:sens_limit]:
            sens.append(
                {
                    "product": s["name"],
                    "item": F.name(r["key"], lang),
                    "down": r["cm1_down"],
                    "up": r["cm1_up"],
                    "impact": r["impact"],
                    "rank": r["rank"],
                }
            )
    st = result.get("store") or {}
    for route in ("main", "lean"):
        for it in (st.get("startup") or {}).get(route, {}).get("items", []):
            startup.append(
                {
                    "route": X.STARTUP[lang][route],
                    "item": X.STARTUP[lang][it["key"]],
                    "amount": it["value"],
                    "currency": cur,
                    "cny": it["cny"],
                    "badge": lab(it["badge"] if it["value"] else "not_found"),
                }
            )
    return per_order, breakdown, startup, sens


def sum_str(*values):
    from decimal import Decimal

    total = sum((Decimal(v) for v in values if v is not None), Decimal("0"))
    return str(total.quantize(Decimal("0.01")))


def converted(entry, scen, fx):
    from decimal import ROUND_HALF_UP, Decimal

    v = Decimal(entry[scen])
    if entry.get("currency") == "CNY":
        v = v / Decimal(fx[scen] or fx["base"])
    return str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def render(run_raw, lang, today=None):
    with open(os.path.join(run_raw, "result.json"), encoding="utf-8") as fh:
        result = json.load(fh)
    with open(os.path.join(run_raw, "inputs.normalized.json"), encoding="utf-8") as fh:
        norm = json.load(fh)
    md = Writer(result, norm, lang, today, raw_dir=run_raw).build()
    limit = None if result.get("edition") == "pro" else 3
    per_order, breakdown, startup, sens = csv_rows(result, norm, lang, limit)
    skus = result["skus"]
    computed = [s for s in skus if not s["blocked"]]
    subject = (computed or skus or [{"name": "product"}])[0]["name"]
    summary = {
        "schema": "profit-model/summary/1",
        "lang": lang,
        "status": result["status"],
        "subject": subject,
        "per_order": per_order,
        "cost_breakdown": breakdown,
        "startup": startup,
        "sensitivity": sens,
    }
    if "pro" in result:
        try:
            import pm_pro

            summary.update(pm_pro.csv_tables(result["pro"], lang))
        except ImportError:
            pass
    with open(os.path.join(run_raw, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(os.path.join(run_raw, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return md, summary


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Write report.md from result.json.")
    ap.add_argument("path", help="run folder or its raw-data folder")
    ap.add_argument("--lang", default="zh", choices=i18n.LANGS)
    args = ap.parse_args(argv)
    _run, raw = output.locate_run(args.path, args.lang)
    if not os.path.isfile(os.path.join(raw, "result.json")):
        print("error: result.json not found, run pm_calc.py first", file=sys.stderr)
        return 1
    render(raw, args.lang)
    print("report: %s" % os.path.join(raw, "report.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
