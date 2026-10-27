"""Build report-draft.md from findings and fees (zh / en).

The draft follows references/report-template.<lang>.md section by section
and passes check_report.py as generated. The agent copies it to report.md,
then reviews and edits (wording, severity downgrades with a reason, context
from the conversation). Technical details stay in the appendix.
Python 3.8+, standard library only.
"""

import datetime
import os
import re

import pr_checks
from _shared import evidence

HERE = os.path.dirname(os.path.abspath(__file__))
SECTIONS = {
    "zh": {
        "title": "# %s 收款体检报告",
        "sub": "<sub>%s · 检查日期 %s%s</sub>",
        "visit": " · 店面内容为从 %s 访问看到的版本",
        "glance": "## 一页看懂",
        "first": "## 一. 先改这几项",
        "E": "## 二. 收款主体与通道",
        "S": "## 三. 结账入口与支付方式",
        "P": "## 四. 政策页与一致性",
        "M": "## 五. 币种与多市场",
        "T": "## 六. 联系方式与信任信息",
        "F": "## 七. 手续费测算",
        "unclear": "## 八. 还有哪些没查清",
        "appA": "## 附录 A: 证据表",
        "appB": "## 附录 B: 费率来源",
        "appC": "## 附录 C: 技术细节",
        "appD": "## 附录 D: 你填写的问卷",
    },
    "en": {
        "title": "# %s payments readiness report",
        "sub": "<sub>%s · checked on %s%s</sub>",
        "visit": " · storefront as seen from %s",
        "glance": "## At a glance",
        "first": "## 1. Fix these first",
        "E": "## 2. Payee company and providers",
        "S": "## 3. Checkout entry and payment methods",
        "P": "## 4. Policies and consistency",
        "M": "## 5. Currencies and markets",
        "T": "## 6. Contact and trust information",
        "F": "## 7. Fee estimate",
        "unclear": "## 8. What is still unclear",
        "appA": "## Appendix A: evidence",
        "appB": "## Appendix B: rate sources",
        "appC": "## Appendix C: technical details",
        "appD": "## Appendix D: your questionnaire answers",
    },
}
L = {
    "zh": {
        "status": {
            "complete": "完整",
            "partial": "部分页面没查到或不适用",
            "failed": "网站没能检查",
        },
        "decide": "开户, 审核和风控结果由收款通道方决定; 本报告只列出网站和资料里能看到的问题, 不判断能不能过审.",
        "counts": "体检状态: %s. 发现要改的: P0 %d 条, P1 %d 条, P2 %d 条, P3 %d 条; 没查清 %d 项.",
        "fees": "手续费估算: 每月约 %s %s, 一年约 %s %s, 占销售额 %s%% [推测] 依据: 你提供的客单价与单量 + 官方费率 (取数 %s)%s.",
        "fees_unknown": " (有 %d 项费用查不到, 没算进去)",
        "no_fees": "没有算手续费: %s.",
        "no_q": "你没有填收款问卷",
        "missing_inputs": "缺少 %s",
        "top": "**最该先改的几项**",
        "none_top": "没有发现 P0/P1 级别的问题. 通道方审核还会看后台资料和交易情况, 这里只说明公开页面和你的回答里没看到明显缺口.",
        "table_head": "| 优先级 | 问题 | 证据 | 怎么改 |\n|---|---|---|---|",
        "no_first": "没有 P0/P1 问题.",
        "fix": "怎么改",
        "e_intro": "开户与审核结果由通道方决定. 下面是你提供的情况与官方公开条件的对照.",
        "offline_store": "网站还没上线, 这一部分没有检查. 上线后可以再跑一次公开体检.",
        "nothing": "这一组没有可报告的项目.",
        "f_formula": "算法: 单笔 = 客单价 x (通道费率 + 附加费率 x 适用比例) + 固定费; 月费用 = 单笔 x 该路径月单量, 另加平台第三方交易费和提现费.",
        "f_table": "| 路径 | 费用项 | 费率 | 固定费 | 占比 | 月费用 | 年费用 | 证据 |\n|---|---|---|---|---|---|---|---|",
        "f_total": "**合计 (已知项)**: 每月 %s %s, 一年 %s %s, 占月销售额 %s %s 的 %s%% [推测] 依据: 上表各项相加.",
        "f_not": "没算进去的: %s",
        "f_not_fixed": "税 (销售税, 增值税), 应用费, Shopify 套餐月费%s, 汇率波动, 退款 (各通道退款时手续费不退)",
        "plan_ref": " (你的套餐 %s 每月 %s %s, 官方价格页)",
        "f_assume": "假设: %s.",
        "f_fresh": "费率快照: %s 费率以通道方官方页面和你的合同为准.",
        "f_stale": "注意: 费率快照已超过 90 天, 可能已经变了.",
        "f_none": "没有算手续费. 想算的话告诉我: 客单价和币种, 每月单量, 用哪些通道及各占多少, 你的 Shopify 套餐, 每月提现几次.",
        "unclear_always": "后台是否真的开通了各付款方式, 合同费率, 审核与风控状态 [查不到]: 需要你看后台或提供截图.",
        "app_status": "- 完成状态: %s",
        "app_warn": "- warnings: %s",
        "ev_head": "| 编号 | 等级 | 内容 | 来源 |\n|---|---|---|---|",
        "rate_head": "| 编号 | 费率项 | 值 | 状态 | 取数日期 | 来源网址 |\n|---|---|---|---|---|---|",
        "no_rates": "本次没有用到费率.",
        "q_none": "没有填写.",
        "verified": {
            "verified": "已核实",
            "user": "你提供的",
            "unknown": "查不到",
            "inferred": "推测",
        },
        "badge_fee": {
            "verified": "[已核实]",
            "user": "[你提供的]",
            "unknown": "[查不到]",
            "inferred": "[推测] 依据: 你的市场分布",
        },
        "where": "证据位置",
        "who": {"self": "自己可改", "tech": "需要建站或技术人员"},
        "sev_note": "优先级说明: P0 上线收款前必须改; P1 尽快改; P2 建议改; P3 可选. 优先级是规则默认值.",
    },
    "en": {
        "status": {
            "complete": "all pages checked",
            "partial": "only part could be checked",
            "failed": "the site could not be checked",
        },
        "decide": "Account opening, reviews and risk decisions are made by the payment providers; this report only lists issues visible on the site and in your answers, and does not predict approval.",
        "counts": "Check result: %s. Issues to fix: P0 %d, P1 %d, P2 %d, P3 %d; %d items unclear.",
        "fees": "Fee estimate: about %s %s per month, %s %s per year, %s%% of sales [Inferred] Basis: your order value and volume + official rates (retrieved %s)%s.",
        "fees_unknown": " (%d fee items not found and not included)",
        "no_fees": "No fee estimate: %s.",
        "no_q": "you did not answer the payments questionnaire",
        "missing_inputs": "missing %s",
        "top": "**Fix these first**",
        "none_top": "No P0/P1 issues found. Providers also review back-office documents and activity; this only means no obvious gap on the public pages and in your answers.",
        "table_head": "| Priority | Issue | Evidence | How to fix |\n|---|---|---|---|",
        "no_first": "No P0/P1 issues.",
        "fix": "How to fix",
        "e_intro": "Providers decide on accounts and reviews. Below, your answers are compared with the providers' published conditions.",
        "offline_store": "The store is not live yet, so this part was not checked. Run the public check again after launch.",
        "nothing": "Nothing to report in this group.",
        "f_formula": "Method: per order = order value x (rate + surcharge x share) + fixed fee; monthly = per order x orders on that route, plus Shopify's third-party fee and withdrawal fees.",
        "f_table": "| Route | Fee item | Rate | Fixed | Share | Monthly | Yearly | Evidence |\n|---|---|---|---|---|---|---|---|",
        "f_total": "**Total (known items)**: %s %s per month, %s %s per year, %s%% of monthly sales %s %s [Inferred] Basis: sum of the rows above.",
        "f_not": "Not included: %s",
        "f_not_fixed": "tax (sales tax, VAT), app fees, the Shopify plan fee%s, exchange-rate moves, refunds (processing fees are not returned on refunds)",
        "plan_ref": " (your %s plan is %s %s per month, official pricing page)",
        "f_assume": "Assumptions: %s.",
        "f_fresh": "Rate snapshot: %s Rates are as published by providers; your contract wins.",
        "f_stale": "Note: the rate snapshot is over 90 days old and may have changed.",
        "f_none": "No fee estimate yet. Tell me your order value and currency, monthly orders, which providers and their shares, your Shopify plan and withdrawals per month.",
        "unclear_always": "Whether each payment method is really enabled, your contract rates and review / risk status [Not found]: check your dashboard or send screenshots.",
        "app_status": "- Status: %s",
        "app_warn": "- warnings: %s",
        "ev_head": "| ID | Level | Content | Source |\n|---|---|---|---|",
        "rate_head": "| ID | Rate item | Value | Status | Retrieved | Source URL |\n|---|---|---|---|---|---|",
        "no_rates": "No rates were used in this run.",
        "q_none": "Not answered.",
        "verified": {
            "verified": "Verified",
            "user": "You provided",
            "unknown": "Not found",
            "inferred": "Inferred",
        },
        "badge_fee": {
            "verified": "[Verified]",
            "user": "[You provided]",
            "unknown": "[Not found]",
            "inferred": "[Inferred] Basis: your market mix",
        },
        "where": "Evidence",
        "who": {"self": "You", "tech": "Web developer"},
        "sev_note": "Priority: P0 must fix before taking payments; P1 fix soon; P2 recommended; P3 optional. Priorities are rule defaults.",
    },
}


def fixed_ending(lang):
    path = os.path.join(HERE, "..", "references", "report-template.%s.md" % lang)
    with open(path, encoding="utf-8") as fh:
        blocks = re.findall(r"```markdown\n(.*?)\n```", fh.read(), re.S)
    return blocks[-1]


def cell(text):
    return re.sub(r"\s+", " ", str(text or "")).replace("|", "/").strip()


def money(v):
    return "-" if v is None else "{:,.2f}".format(float(v))


def claim(f, lang):
    """Finding text with its badge (and basis for inferred)."""
    b = evidence.badge(f["badge"], lang)
    text = f["finding"].rstrip()
    if f["badge"] == "inferred":
        basis = f["basis"] or ("规则推断" if lang == "zh" else "rule-based inference")
        return "%s %s %s: %s." % (text.rstrip("."), b, evidence.BASIS_WORD[lang], basis)
    return "%s %s" % (text, b)


def finding_line(f, lang):
    t = L[lang]
    sev = (" (%s)" % f["severity"]) if f["result"] == "issue" else ""
    line = "- **%s %s**%s: %s" % (f["id"], f["check"], sev, claim(f, lang))
    if f["result"] == "issue" and f["fix"]:
        line += " %s: %s" % (t["fix"], f["fix"])
    return line


def build(summary, findings, fees, ev, facts, q, rates, lang="zh", today=None):
    s, t = SECTIONS[lang], L[lang]
    today = (today or datetime.date.today()).isoformat()
    subject = summary.get("shop_name") or summary.get("domain") or "store"
    out = [s["title"] % subject, ""]
    url = summary.get("base_url") or summary.get("domain") or ""
    visit = facts["market"].get("country")
    out.append(s["sub"] % (url, today, (s["visit"] % visit) if visit else ""))
    out.append("")
    # at a glance
    c = summary["counts"]
    out += [s["glance"], ""]
    out.append("- " + t["decide"])
    out.append(
        "- "
        + t["counts"]
        % (
            t["status"][summary["status"]],
            c["P0"],
            c["P1"],
            c["P2"],
            c["P3"],
            c["unknown"],
        )
    )
    if fees and fees.get("monthly_total") is not None:
        unk = (
            (t["fees_unknown"] % len(fees["unknown_items"]))
            if fees["unknown_items"]
            else ""
        )
        out.append(
            "- "
            + t["fees"]
            % (
                money(fees["monthly_total"]),
                fees["currency"],
                money(fees["yearly_total"]),
                fees["currency"],
                money(fees["pct_of_sales"]),
                fees["rates_valid_as_of"],
                unk,
            )
        )
    elif fees:
        out.append(
            "- " + t["no_fees"] % (t["missing_inputs"] % ", ".join(fees["missing"]))
        )
    else:
        out.append("- " + t["no_fees"] % t["no_q"])
    out += ["", t["top"], ""]
    top = [
        f for f in pr_checks.priority_list(findings) if f["severity"] in ("P0", "P1")
    ][:5]
    if not top:
        top = pr_checks.priority_list(findings)[:3]
    if top:
        for i, f in enumerate(top, 1):
            out.append(
                "%d. [%s] %s: %s" % (i, f["severity"], f["check"], claim(f, lang))
            )
    else:
        out.append(t["none_top"])
    out.append("")
    # 1. fix first
    out += [s["first"], "", t["sev_note"], ""]
    first = [
        f for f in pr_checks.priority_list(findings) if f["severity"] in ("P0", "P1")
    ]
    if first:
        out.append(t["table_head"])
        for f in first:
            out.append(
                "| %s | %s | %s | %s |"
                % (
                    f["severity"],
                    cell(f["id"] + " " + f["check"]),
                    cell(claim(f, lang)),
                    cell(f["fix"]),
                )
            )
    else:
        out.append(t["no_first"])
    out.append("")
    # groups
    for g in ("E", "S", "P", "M", "T"):
        out += [s[g], ""]
        if g == "E":
            out += [t["e_intro"], ""]
        items = [f for f in findings if f["group"] == g]
        if not items:
            out.append(
                t["offline_store"]
                if facts["mode"] == "offline" and g != "E"
                else t["nothing"]
            )
        for f in items:
            out.append(finding_line(f, lang))
        out.append("")
    # fees
    out += [s["F"], ""]
    rate_ids = []
    if fees and fees.get("rows"):
        out += [t["f_formula"], "", t["f_table"]]
        for r in fees["rows"]:
            sid = r["source_id"]
            ref = ""
            if sid and sid != "user":
                if sid not in rate_ids:
                    rate_ids.append(sid)
                ref = " (B%d)" % (rate_ids.index(sid) + 1)
            badge = t["badge_fee"][r["status"]]
            out.append(
                "| %s | %s | %s | %s | %s | %s | %s | %s%s |"
                % (
                    cell(r["route"]),
                    cell(r["item"]),
                    r["pct"] or "-",
                    r["fixed"] or "-",
                    r["share"] or "-",
                    money(r["monthly"]),
                    money(r.get("yearly")),
                    badge,
                    ref,
                )
            )
        out.append("")
        if fees.get("monthly_total") is not None:
            out.append(
                t["f_total"]
                % (
                    money(fees["monthly_total"]),
                    fees["currency"],
                    money(fees["yearly_total"]),
                    fees["currency"],
                    money(fees["pct_of_sales"]),
                    fees["currency"],
                    money(fees["monthly_sales"]),
                )
                if lang == "en"
                else t["f_total"]
                % (
                    money(fees["monthly_total"]),
                    fees["currency"],
                    money(fees["yearly_total"]),
                    fees["currency"],
                    money(fees["monthly_sales"]),
                    fees["currency"],
                    money(fees["pct_of_sales"]),
                )
            )
            out.append("")
        ref = fees.get("plan_fee_reference")
        plan_txt = (
            (t["plan_ref"] % (ref["plan"], ref["monthly"], ref["currency"]))
            if ref and ref.get("monthly")
            else ""
        )
        nots = [t["f_not_fixed"] % plan_txt] + list(fees["unknown_items"])
        out.append("- " + t["f_not"] % "; ".join(nots) + ".")
        if fees["assumptions"]:
            out.append(
                "- "
                + t["f_assume"] % "; ".join(a.rstrip(".") for a in fees["assumptions"])
            )
        out.append("- " + t["f_fresh"] % fees["rates_note"])
        if fees.get("rates_stale"):
            out.append("- " + t["f_stale"])
    elif fees:
        out.append(
            t["no_fees"] % (t["missing_inputs"] % ", ".join(fees["missing"]))
            + " "
            + t["f_none"]
        )
    else:
        out.append(t["f_none"])
    out.append("")
    # unclear
    out += [s["unclear"], ""]
    for f in findings:
        if f["result"] == "unknown":
            out.append("- %s: %s" % (f["check"], claim(f, lang)))
    if fees:
        for item in fees.get("unknown_items", []):
            out.append("- %s %s" % (item, evidence.badge("not_found", lang)))
    out.append("- " + t["unclear_always"])
    out.append("")
    # appendix A
    out += [
        s["appA"],
        "",
        t["app_status"] % summary["status"],
        t["app_warn"] % (", ".join(summary["warnings"]) or "-"),
        "",
    ]
    out.append(t["ev_head"])
    for e in ev:
        out.append(
            "| %s | %s | %s | %s |"
            % (
                e["evidence_id"],
                e["level"],
                cell(e["content"]),
                cell(e["url"] or e["source"]),
            )
        )
    out.append("")
    # where each finding came from
    for f in findings:
        if f["where"]:
            out.append("- %s %s: %s" % (f["id"], t["where"], ", ".join(f["where"])))
    out.append("")
    # appendix B
    out += [s["appB"], ""]
    by_id = dict((r["id"], r) for r in rates.get("rates", []))
    if rate_ids:
        out.append(t["rate_head"])
        for i, sid in enumerate(rate_ids, 1):
            r = by_id.get(sid, {})
            out.append(
                "| B%d | %s | %s | %s | %s | %s |"
                % (
                    i,
                    sid,
                    cell(rate_value(r)),
                    r.get("status", ""),
                    r.get("retrieved", ""),
                    r.get("source_url", ""),
                )
            )
    else:
        out.append(t["no_rates"])
    official = [w for f in findings for w in f["where"] if re.match(r"^S\d+$", w)]
    for sid in sorted(set(official), key=lambda x: int(x[1:])):
        src = (rates.get("sources") or {}).get(sid, {})
        out.append(
            "- %s: %s (%s) %s"
            % (
                sid,
                src.get("url", ""),
                src.get("retrieved", ""),
                cell(src.get("supports", "")),
            )
        )
    out.append("")
    # appendix C
    out += [s["appC"], ""]
    sig = summary["signals"]
    out.append(
        "- identity: %s (%s)"
        % (summary["identity"]["status"], ", ".join(facts["markers"]) or "-")
    )
    out.append("- mode: %s; visitor market: %s" % (summary["mode"], facts["market"]))
    out.append("- payment icons: %s" % (", ".join(sig["payment_icons"]) or "-"))
    out.append(
        "- payment scripts: %s"
        % (
            ", ".join(
                "%s (%s, %s)" % (x["name"], x["pattern"], x["source"])
                for x in sig["payment_scripts"]
            )
            or "-"
        )
    )
    out.append(
        "- apps: %s"
        % (", ".join("%s (%s)" % (x["name"], x["pattern"]) for x in sig["apps"]) or "-")
    )
    for key, ep in sorted(summary["endpoints"].items()):
        out.append(
            "- endpoint %s: %s %s %s"
            % (
                key,
                ep.get("result"),
                ep.get("http_status") or "",
                cell(ep.get("note") or ""),
            )
        )
    out.append("")
    # appendix D
    out += [s["appD"], ""]
    if q and q.get("answered"):
        for k in q["answered"]:
            out.append("- %s: %s" % (k, cell(q.get(k))))
    else:
        out.append(t["q_none"])
    out.append("")
    out.append(fixed_ending(lang))
    out.append("")
    return "\n".join(out)


def rate_value(r):
    v = r.get("value")
    unit = r.get("unit", "")
    if isinstance(v, dict) and "pct" in v:
        return "%s%% + %s %s" % (v["pct"], v.get("fixed", 0), r.get("currency", ""))
    if unit == "percent":
        return "%s%%" % v
    return "%s %s" % (v, r.get("currency", ""))
