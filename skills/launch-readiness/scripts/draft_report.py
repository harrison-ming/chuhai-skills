"""Report draft (report.draft.md) built from audit results, in zh or en.

The agent reviews it (conflicts first), edits, and saves it as report.md;
the fixed ending is copied verbatim from references/report-template.<lang>.md.
Python 3.8+, standard library only.
"""

import datetime
import os
import re

import hreflang
import markets
import texts
from _shared import evidence

HERE = os.path.dirname(os.path.abspath(__file__))
T = {
    "zh": {
        "title": "# %s 上线体检报告",
        "sub": "<sub>%s · 检查日期 %s · %s</sub>",
        "mode": {
            "online": "读取公开页面",
            "paste": "读取你提供的文本",
            "manual": "读取手动保存的页面",
        },
        "h": [
            "## 一页看懂",
            "## 先改这几条 (P0)",
            "## 政策页",
            "## 前后说法对不上",
            "## 投谷歌购物广告前",
            "## 各市场要求",
            "## 多市场与翻译",
            "## 看起来像没做完的地方",
            "## 还有哪些没查清",
            "## 附录: 证据与数据来源",
        ],
        "markets": "- 按以下市场检查: %s (%s)",
        "mk_src": {
            "user": "你指定的",
            "site": "页面上能看到的市场加美国, 不对可以告诉我重跑",
        },
        "counts": "- 要改的: P0 %d 条, P1 %d 条, P2 %d 条, P3 %d 条 (含需要你自己核对的 %d 条)",
        "verdict": "- 结论: **%s** [推测] 依据: 上线前必须改的 P0 有 %d 条%s.",
        "no_verdict": "- 结论: 无法判断 [查不到], 店铺页面没有读到.",
        "top": "**最该先改的 3 条**",
        "legal": "- 这份体检对照的是官方公开要求, **不是法律意见**; 法律相关的判断都标为 '推测'.",
        "none": "- 没有发现这一类问题.",
        "where": "  - 在哪: %s",
        "quote": '  - 原文: "%s"',
        "fix": "  - 怎么改: %s",
        "note": "  - 说明: %s",
        "basis": " 依据: %s",
        "res": {
            "fail": "有问题",
            "manual": "需要你核对",
            "pass": "没发现问题",
            "unknown": "查不到",
            "skip": "不适用",
            "same": "见 %s",
            "info": "提示",
        },
        "see_p0": "- **%s** %s %s (见上面 P0 一节)",
        "conflict_hint": "以下是脚本初筛出的前后不一致, 两段原文都列出来了, 请确认确实说的是同一件事.",
        "no_ads": "你说暂不投谷歌购物广告, 这一节略过.",
        "gap": "%s: 与官方要求不一致",
        "not_checked_fixed": [
            "结账页能否完成付款, 支付方式是否开通: 自己下一单测试订单.",
            "谷歌商家中心里的退货, 运费设置: 登录商家中心对照本报告的退货天数.",
        ],
        "unknown": '- %s 没能确认: "%s" %s%s',
        "unknown_hint": "下面这些没查清, 拿不到不等于不存在. 自己怎么查: 打开对应页面看一眼, 或把页面文字交给我用粘贴模式再查.",
        "failed": "店铺页面没有读到 (打不开或被拦截), 所有检查项都没做. 自己怎么查: 用浏览器打开首页确认能访问, 稍后重跑; 或把政策文字交给我用粘贴模式检查.",
        "status": "- 完成状态: %s",
        "detail": "| 编号 | 结果 | 级别 | 标签 |",
        "sources": "**依据清单** (检索日期 %s)",
        "tech": "**技术细节**",
    },
    "en": {
        "title": "# %s launch readiness report",
        "sub": "<sub>%s · checked %s · %s</sub>",
        "mode": {
            "online": "public pages",
            "paste": "text you provided",
            "manual": "pages saved by hand",
        },
        "h": [
            "## At a glance",
            "## Fix first",
            "## Policy pages",
            "## Conflicting statements",
            "## Before Google Shopping ads",
            "## Market-by-market",
            "## Multi-market & translation",
            "## Unfinished-looking items",
            "## Not verified",
            "## Appendix: evidence and sources",
        ],
        "markets": "- Markets checked: %s (%s)",
        "mk_src": {
            "user": "as you specified",
            "site": "markets visible on the site plus the US; tell me if wrong",
        },
        "counts": "- To fix: P0 %d, P1 %d, P2 %d, P3 %d (incl. %d for you to check yourself)",
        "verdict": "- Verdict: **%s** [Inferred] Basis: %d must-fix P0 items%s.",
        "no_verdict": "- Verdict: cannot tell [Not found]; the store pages could not be read.",
        "top": "**Top 3 fixes**",
        "legal": "- This check compares your pages with official guidance and **is not legal advice**; legal judgements are marked 'Inferred'.",
        "none": "- No issues of this kind found.",
        "where": "  - Where: %s",
        "quote": '  - Text: "%s"',
        "fix": "  - Fix: %s",
        "note": "  - Note: %s",
        "basis": " Basis: %s",
        "res": {
            "fail": "issue",
            "manual": "check yourself",
            "pass": "no issue found",
            "unknown": "not found",
            "skip": "not applicable",
            "same": "see %s",
            "info": "hint",
        },
        "see_p0": "- **%s** %s %s (see Fix first)",
        "conflict_hint": "Candidate conflicts found by the script; both texts are quoted, please confirm they talk about the same thing.",
        "no_ads": "You do not plan Google Shopping ads yet, so this section is skipped.",
        "gap": "%s: differs from the official requirement",
        "not_checked_fixed": [
            "Whether checkout and payments work: place a test order.",
            "Merchant Center return and shipping settings: compare them with this report.",
        ],
        "unknown": '- %s could not confirm: "%s" %s%s',
        "unknown_hint": "Not verified does not mean absent. How to check: open the page yourself, or give me the text for paste mode.",
        "failed": "The store pages could not be read (unreachable or blocked), so no item was checked. How to check: open the homepage in a browser, retry later, or give me the policy texts for paste mode.",
        "status": "- Status: %s",
        "detail": "| ID | Result | Priority | Badge |",
        "sources": "**Sources** (retrieved %s)",
        "tech": "**Technical details**",
    },
}
SECTIONS = {
    2: ("POL-", "CON-", "PRV-"),
    3: ("CNS-",),
    4: ("GMC-",),
    5: ("EU-", "DE-", "UK-", "CA-", "AU-", "US-"),
    6: ("LOC-",),
    7: ("TRU-", "SEO-", "BIZ-"),
}
MARKET_ORDER = [
    ("EU", "EU-"),
    ("DE", "DE-"),
    ("UK", "UK-"),
    ("CA", "CA-"),
    ("AU", "AU-"),
    ("US", "US-"),
]


def fixed_ending(lang):
    path = os.path.join(
        os.path.dirname(HERE), "references", "report-template.%s.md" % lang
    )
    with open(path, encoding="utf-8") as fh:
        blocks = re.findall(r"```markdown\n(.*?)\n```", fh.read(), re.S)
    return blocks[-1]


def badge_text(key, lang):
    return "[%s]" % evidence.label(key, lang) if key else ""


def basis(rule, ctx, lang):
    names = [
        "%s %s" % (s, ctx.sources.get(s, {}).get("name", ""))
        for s in rule.get("src") or []
    ]
    return T[lang]["basis"] % (
        "; ".join(names) if names else ("经验项" if lang == "zh" else "experience item")
    )


def item(f, ctx, lang, full=True):
    t = T[lang]
    rule = ctx.rules[f["id"]]
    b = badge_text(f.get("badge"), lang)
    head = "- **%s** %s %s" % (f["id"], rule[lang], b)
    if f.get("badge") == "inferred":
        head += basis(rule, ctx, lang)
    out = [head.rstrip()]
    if f.get("where"):
        out.append(t["where"] % texts.plain_where(f["where"], lang))
    if f["id"] == "LOC-03" and f.get("data"):
        lines = [hreflang.describe(p, lang) for p in f["data"].get("problems", [])[:3]]
    else:
        lines = [texts.plain_evidence(e, lang) for e in f.get("evidence") or []]
    for e in lines:
        out.append(t["quote"] % e.replace('"', "'"))
    note = texts.note_text(f.get("note"), lang)
    if note:
        out.append(t["note"] % note)
    if full:
        out.append(t["fix"] % rule["fix_" + lang])
    return out


def section(findings, prefixes, ctx, lang):
    rows = [
        f
        for f in findings
        if f["id"].startswith(prefixes) and f["result"] in ("fail", "manual")
    ]
    rows.sort(key=lambda f: f["level"])
    out = []
    for f in rows:
        if f["result"] == "fail" and f["level"] == "P0":
            out.append(
                T[lang]["see_p0"]
                % (f["id"], ctx.rules[f["id"]][lang], badge_text(f.get("badge"), lang))
            )
        else:
            out += item(f, ctx, lang)
    return out or [T[lang]["none"]]


def render(summary, findings, ctx, lang="zh", today=None):
    t = T[lang]
    today = today or datetime.date.today().isoformat()
    name = summary.get("domain") or "store"
    out = [
        t["title"] % name,
        "",
        t["sub"]
        % (
            summary.get("base_url") or name,
            today,
            t["mode"].get(summary.get("mode"), ""),
        ),
        "",
    ]
    out += [t["h"][0], ""]
    mk = ", ".join(markets.label(c, lang) for c in summary["markets"][:12])
    if len(summary["markets"]) > 12:
        mk += " ... (%d)" % len(summary["markets"])
    out.append(t["markets"] % (mk, t["mk_src"][summary["markets_source"]]))
    c = summary["counts"]
    manual = len([f for f in findings if f["result"] == "manual"])
    out.append(t["counts"] % (c["P0"], c["P1"], c["P2"], c["P3"], manual))
    p0 = [f["id"] for f in findings if f["result"] == "fail" and f["level"] == "P0"]
    if summary.get("verdict"):
        ids = " (%s)" % ", ".join(p0) if p0 else ""
        out.append(
            t["verdict"] % (texts.VERDICT[lang][summary["verdict"]], len(p0), ids)
        )
    else:
        out.append(t["no_verdict"])
    out.append(t["legal"])
    out += ["", t["top"]]
    for i, r in enumerate(summary["fixlist"][:3], 1):
        out.append("%d. %s %s: %s" % (i, r["id"], r["issue"], r["fix"]))
    if not summary["fixlist"]:
        out.append("1. -")
    out += ["", t["h"][1], ""]
    p0f = [f for f in findings if f["result"] == "fail" and f["level"] == "P0"]
    for f in p0f:
        out += item(f, ctx, lang)
    if not p0f:
        out.append(t["none"])
    for idx in range(2, 8):
        out += ["", t["h"][idx], ""]
        if idx == 3:
            out += [t["conflict_hint"], ""]
        if idx == 4 and not summary.get("ads"):
            out.append(t["no_ads"])
            continue
        if idx == 5:
            out += markets_section(findings, ctx, lang)
            continue
        out += section(findings, SECTIONS[idx], ctx, lang)
    out += ["", t["h"][8], ""]
    if summary["status"] == "failed":
        out.append(t["failed"] + " " + badge_text("not_found", lang))
    else:
        out += [t["unknown_hint"], ""]
        for f in findings:
            if f["result"] == "unknown":
                note = texts.note_text(f.get("note"), lang)
                out.append(
                    t["unknown"]
                    % (
                        f["id"],
                        ctx.rules[f["id"]][lang],
                        badge_text("not_found", lang),
                        (" " + note) if note else "",
                    )
                )
    for line in t["not_checked_fixed"]:
        out.append("- %s %s" % (line, badge_text("not_found", lang)))
    out += ["", t["h"][9], "", t["status"] % summary["status"], ""]
    out += appendix(summary, findings, ctx, lang)
    out += ["", fixed_ending(lang), ""]
    return "\n".join(out)


def markets_section(findings, ctx, lang):
    t = T[lang]
    out = []
    for group, prefix in MARKET_ORDER:
        rows = [
            f
            for f in findings
            if f["id"].startswith(prefix) and f["result"] not in ("skip", "same")
        ]
        if not rows:
            continue
        out += ["### " + markets.label(group if group != "UK" else "GB", lang), ""]
        for f in rows:
            if f["result"] in ("fail", "manual"):
                rule = ctx.rules[f["id"]]
                lines = item(f, ctx, lang)
                if f.get("badge") == "inferred":
                    lines[0] = "- **%s** %s %s%s" % (
                        f["id"],
                        t["gap"] % rule[lang],
                        badge_text("inferred", lang),
                        basis(rule, ctx, lang),
                    )
                out += lines
            else:
                res = t["res"][f["result"]]
                out.append(
                    "- %s %s: %s %s"
                    % (
                        f["id"],
                        ctx.rules[f["id"]][lang],
                        res,
                        badge_text(f.get("badge"), lang),
                    )
                )
        out.append("")
    return out or [t["none"]]


def appendix(summary, findings, ctx, lang):
    t = T[lang]
    out = [t["detail"], "|---|---|---|---|"]
    for f in findings:
        res = t["res"][f["result"]]
        if f["result"] == "same":
            res = res % f.get("same_as")
        out.append(
            "| %s | %s | %s | %s |"
            % (
                f["id"],
                res,
                f["level"],
                evidence.label(f["badge"], lang) if f.get("badge") else "",
            )
        )
    used = sorted(
        set(
            s
            for f in findings
            if f["result"] in ("fail", "manual")
            for s in f["sources"]
        )
    )
    out += ["", t["sources"] % ctx.data["valid_as_of"], ""]
    for s in used:
        src = ctx.sources.get(s, {})
        out.append("- %s %s: %s" % (s, src.get("name"), src.get("url")))
    out += ["", t["tech"], ""]
    out.append(
        "- mode=%s, platform=%s, markets=%s"
        % (summary["mode"], summary["platform"], ",".join(summary["markets"][:30]))
    )
    out.append("- warnings: %s" % (", ".join(summary["warnings"]) or "-"))
    for key, ep in sorted(summary["endpoints"].items())[:60]:
        out.append("- %s: %s (%s)" % (key, ep.get("result"), ep.get("http_status")))
    lc = ctx.findings.get("LOC-03") or {}
    if lc.get("data"):
        out.append("- hreflang codes: %s" % ", ".join(lc["data"].get("codes", [])[:40]))
    return out
