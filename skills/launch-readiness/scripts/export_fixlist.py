#!/usr/bin/env python3
"""audit.json -> fix list rows (整改清单.csv), sorted by priority then id.

audit.py stores the rows in summary.json["fixlist"]; deliver.py writes the
CSV from there (columns in skill_config.CSV_TABLES). This CLI rewrites the
CSV straight from audit.json, e.g. after editing results by hand.

CLI:
    python export_fixlist.py "<run folder>" [--lang zh|en]
Python 3.8+, standard library only.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import markets  # noqa: E402
import ruleset  # noqa: E402
import skill_config as cfg  # noqa: E402
import texts  # noqa: E402
from _shared import evidence, output, render  # noqa: E402

EFFORT = {
    "zh": {"5m": "5 分钟", "30m": "半小时", "4h": "半天"},
    "en": {"5m": "5 min", "30m": "30 min", "4h": "half a day"},
}
STATUS = {
    "zh": {"fail": "待改", "manual": "待核对"},
    "en": {"fail": "To fix", "manual": "To check"},
}
RECHECK = {
    "zh": {
        "fail": "改完重跑本体检, 看 %s 是否消失",
        "manual": '按"怎么改"自己核对 (后台或浏览器), 体检脚本看不到',
    },
    "en": {
        "fail": "Re-run the check after fixing and confirm %s is gone",
        "manual": "Check it yourself (admin or browser); the script cannot see it",
    },
}
MARKET_ALL = {"zh": "全部", "en": "All"}


def source_text(rule, sources):
    parts = []
    for sid in rule.get("src") or []:
        src = sources.get(sid) or {}
        parts.append("%s %s %s" % (sid, src.get("name", ""), src.get("url", "")))
    return "; ".join(parts) or "-"


def build_rows(findings, lang="zh"):
    data, rules = ruleset.load_rules()
    rows = []
    for f in findings:
        if f["result"] not in ruleset.IN_FIXLIST:
            continue
        rule = rules[f["id"]]
        mk = rule.get("mk") or []
        if lang == "zh":
            mk_text = ", ".join(
                markets.label("GB" if m == "UK" else m, "zh") for m in mk
            )
        else:
            mk_text = ", ".join(mk)
        badge = f.get("badge")
        rows.append(
            {
                "priority": f["level"],
                "id": f["id"],
                "issue": rule[lang],
                "markets": mk_text or MARKET_ALL[lang],
                "where": texts.plain_where(f.get("where"), lang) or "",
                "evidence": "\n".join(
                    texts.plain_evidence(e, lang) for e in f.get("evidence") or []
                )
                or (texts.note_text(f.get("note"), lang) or ""),
                "badge": "[%s]" % evidence.label(badge, lang) if badge else "",
                "fix": rule["fix_" + lang],
                "basis": source_text(rule, data["sources"]),
                "effort": EFFORT[lang].get(rule.get("eff"), ""),
                "status": STATUS[lang][f["result"]],
                "recheck": RECHECK[lang][f["result"]] % f["id"]
                if "%s" in RECHECK[lang][f["result"]]
                else RECHECK[lang][f["result"]],
            }
        )
    order = dict((lv, i) for i, lv in enumerate(ruleset.LEVELS))
    pos = dict((r["id"], i) for i, r in enumerate(data["rules"]))
    rows.sort(key=lambda r: (order.get(r["priority"], 9), pos.get(r["id"], 999)))
    return rows


def drop(audit, summary, ids, lang):
    """Mark findings as reviewed false positives and refresh counts/fixlist."""
    for f in audit["findings"]:
        if f["id"] in ids and f["result"] in ruleset.IN_FIXLIST:
            f["result"], f["note"] = "pass", "agent_reviewed"
    rows = build_rows(audit["findings"], lang)
    counts = dict((lv, 0) for lv in ruleset.LEVELS)
    for f in audit["findings"]:
        if f["result"] in ruleset.IN_FIXLIST:
            counts[f["level"]] += 1
    audit["counts"] = summary["counts"] = counts
    summary["fixlist"] = rows
    if ids:
        import audit as audit_mod  # late import: audit imports this module

        v = audit_mod.verdict(summary.get("status"), audit["findings"])
        audit["verdict"] = summary["verdict"] = v
    return rows


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Write the fix list CSV from audit.json.")
    ap.add_argument("run_dir")
    ap.add_argument("--lang")
    ap.add_argument(
        "--drop", default="", help="ids reviewed as false positives, e.g. CNS-02,GMC-08"
    )
    args = ap.parse_args(argv)
    run_dir, raw = None, None
    for code in ([args.lang] if args.lang else []) + ["zh", "en"]:
        run_dir, raw = output.locate_run(args.run_dir, code)
        if os.path.isfile(os.path.join(raw, "audit.json")):
            break
    with open(os.path.join(raw, "audit.json"), encoding="utf-8") as fh:
        audit = json.load(fh)
    with open(os.path.join(raw, "summary.json"), encoding="utf-8") as fh:
        summary = json.load(fh)
    lang = args.lang or summary.get("lang") or "zh"
    ids = set(x.strip().upper() for x in args.drop.split(",") if x.strip())
    rows = drop(audit, summary, ids, lang)
    for name, doc in (("audit.json", audit), ("summary.json", summary)):
        with open(os.path.join(raw, name), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    table = cfg.CSV_TABLES[0]
    header = [c[0] if lang == "zh" else c[1] for c in table["columns"]]
    path = os.path.join(run_dir, table["name"][lang])
    render.write_csv(
        path, header, [[r.get(c[2]) for c in table["columns"]] for r in rows]
    )
    print("%d rows: %s" % (len(rows), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
