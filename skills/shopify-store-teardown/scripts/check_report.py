#!/usr/bin/env python3
"""Self-check gate for a finished report.md, run before deliver.py.

CLI:
    python check_report.py "<run folder>"        (or its 原始数据 folder,
                                                  or the report.md itself)
    python check_report.py "<run folder>" --json

Exit code: 0 = no hard errors (warnings may remain), 1 = hard errors found,
2 = report.md not found.

Hard errors (must be fixed, then re-run):
  - missing "## 一页看懂" (or it is not the first "##" section)
  - missing "## 附录..." section
  - technical words before the appendix (E1-E6, L0/L1/L2, products.json,
    myshopify, Shopify.theme, hreflang, RDAP, Wayback, sitemap, JSON, ...).
    A line that explains the word in plain Chinese (contains 也就是 / 即: /
    指的是 / 俗称 ...) is downgraded to a warning.
  - old labels such as [事实] [推断] [存疑] [未知] [观察] [品牌自述] [第三方声称]
  - [推测] without "依据" in the same line (or its continuation line / cell)
  - missing the fixed ending "本报告没有覆盖的"

Warnings (do not fail, but the agent must review each one):
  - suspected over-labeling: the claim attached to [已核实] contains
    judgment words (JUDGMENT_WORDS) or source words (MIXED_SOURCE_WORDS)
  - "handle" / "tag" / snake_case field names before the appendix
  - full-width punctuation

deliver.py imports check_text() / format_result() from this module.
Python 3.8+, standard library only.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# configuration (edit these lists to tune the checker)
# ---------------------------------------------------------------------------

SUMMARY_HEADING = "## 一页看懂"
APPENDIX_PREFIX = "## 附录"
FIXED_ENDING = "本报告没有覆盖的"
RAW_DIRNAME = "原始数据"

BADGES = ("已核实", "店铺自称", "第三方说法", "页面显示", "推测", "有矛盾", "查不到")
OLD_LABELS = {
    "事实": "[已核实]",
    "品牌自述": "[店铺自称]",
    "第三方声称": "[第三方说法]",
    "观察": "[页面显示]",
    "推断": "[推测] + 依据",
    "存疑": "[有矛盾]",
    "未知": "[查不到]",
}

# Words that turn a fact into a judgment. Regex fragments; lookaheads drop
# common non-judgment compounds (靠谱, 基本信息, 关键词...).
JUDGMENT_WORDS = [
    r"核心",
    r"关键(?!词)",
    r"(?:这|也|正好|恰恰|足以|可以)说明|说明了",
    r"意味着",
    r"因此",
    r"所以",
    r"因为",
    r"由于",
    r"从而",
    r"靠(?!谱)",
    r"基本(?!信息|资料|款)",
    r"主要是",
    r"主要靠",
    r"本质",
    r"成立",
    r"策略",
    r"看起来",
    r"看得出",
    r"可见",
    r"应该",
    r"很可能",
    r"大概率",
    r"显然",
    r"其实",
    r"刻意",
    r"为了",
    r"目的",
    r"证明",
    r"优势",
    r"短板",
    r"风险",
    r"最大的",
    r"最值得",
    r"撑住|撑起",
    r"效果",
    r"成功",
]
# Words that mean the claim is someone's saying, not a verified fact.
MIXED_SOURCE_WORDS = [
    r"自称",
    r"号称",
    r"宣称",
    r"据说",
    r"据报道",
    r"媒体报道",
    r"估算",
    r"估计",
    r"网传",
]

# Technical words forbidden before the appendix: (regex, plain-Chinese hint).
TECH_TERMS = [
    (r"(?<![A-Za-z0-9])E[1-6](?![0-9])", "证据等级只写在附录, 正文用标签"),
    (r"(?<![A-Za-z0-9])L[012](?![0-9])", "改成 快速核验 / 深度拆解 / 浏览器实测"),
    (r"products\.json", "店铺公开的商品数据"),
    (r"collections\.json", "店铺公开的商品分类数据"),
    (r"summary\.json|evidence\.jsonl|products\.tsv", "放附录"),
    (r"myshopify", "店铺在 Shopify 上的原始域名"),
    (r"Shopify\.theme", "店铺用的主题"),
    (r"hreflang", "多国站点设置"),
    (r"\bMarkets\b", "多国站点设置"),
    (r"\bRDAP\b", "网站注册记录"),
    (r"\bWHOIS\b", "网站注册记录"),
    (r"Wayback", "网页历史存档"),
    (r"sitemap", "网站地图"),
    (r"\bJSON\b", "店铺公开数据"),
    (r"\bAPI\b", "公开数据接口"),
    (r"(?<![A-Za-z])raw/", "放附录"),
    (r"compare_at_price|created_at|published_at|ships_to|products_count", "放附录"),
]
# Softer technical words (common in English quotes): warning only.
SOFT_TECH_TERMS = [
    (r"\bhandles?\b", "商品网址标识"),
    (r"\btags?\b", "商品后台分类标记"),
    (r"\b[a-z]+_[a-z_]+\b", "字段名放附录"),
]
EXPLAIN_RE = re.compile(r"也就是|即[:,: ]|指的是|俗称|简称|意思是")

# Full-width punctuation, written as escapes so this file itself stays clean:
# , . ; : ! ? ( ) [ ]
FULLWIDTH_RE = re.compile(
    "[\uff0c\u3002\uff1b\uff1a\uff01\uff1f\uff08\uff09\u3010\u3011]"
)

# ---------------------------------------------------------------------------

BADGE_RE = re.compile(r"\[(%s)\]" % "|".join(BADGES))
OLD_RE = re.compile(r"\[(%s)\]" % "|".join(OLD_LABELS))
QUOTE_RE = re.compile(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」')
URL_RE = re.compile(r"https?://\S+|\]\([^)]*\)|`[^`]*`")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
JUDGMENT_RE = re.compile("|".join("(?:%s)" % w for w in JUDGMENT_WORDS))
MIXED_RE = re.compile("|".join("(?:%s)" % w for w in MIXED_SOURCE_WORDS))


class Issue(object):
    def __init__(self, severity, kind, line, text, why, fix):
        self.severity = severity  # "error" | "warning"
        self.kind = kind
        self.line = line  # 1-based, 0 = whole file
        self.text = text
        self.why = why
        self.fix = fix

    def as_dict(self):
        return dict(self.__dict__)


class Result(object):
    def __init__(self, issues):
        self.errors = [i for i in issues if i.severity == "error"]
        self.warnings = [i for i in issues if i.severity == "warning"]

    @property
    def ok(self):
        return not self.errors

    def over_label_count(self):
        return sum(1 for w in self.warnings if w.kind == "over_label")


def clip(text, n=70):
    text = text.strip()
    return text if len(text) <= n else text[: n - 3] + "..."


def content_lines(md_text):
    """Yield (lineno, line) outside fenced code blocks."""
    fence = False
    for i, line in enumerate(md_text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            yield i, line


def claim_segments(line):
    """Yield (badge, claim_text) for each badge in a line.

    Labels trail their claim ("... 在售 132 款 [已核实]"), so the claim is
    the text between the previous badge (or cell / line start) and this one.
    A badge at the start of its segment claims the text after it instead.
    """
    for cell in line.split("|"):
        matches = list(BADGE_RE.finditer(cell))
        prev_end = 0
        for idx, m in enumerate(matches):
            before = LIST_RE.sub("", cell[prev_end : m.start()])
            before = before.strip(" \t>*-;,.:")
            if before:
                claim = before
            else:
                nxt = matches[idx + 1].start() if idx + 1 < len(matches) else None
                claim = cell[m.end() : nxt].strip()
            yield m.group(1), claim
            prev_end = m.end()


def strip_quotes(text):
    return QUOTE_RE.sub('""', text)


def check_text(md_text):
    """Run every rule on report markdown and return a Result."""
    issues = []

    def add(sev, kind, ln, text, why, fix):
        issues.append(Issue(sev, kind, ln, clip(text), why, fix))

    lines = list(content_lines(md_text))
    h2 = [(i, s.strip()) for i, s in lines if re.match(r"^##\s", s)]
    summary_ln = next((i for i, s in h2 if s == SUMMARY_HEADING), None)
    appendix_ln = next((i for i, s in h2 if s.startswith(APPENDIX_PREFIX)), None)

    if summary_ln is None:
        add(
            "error",
            "no_summary",
            0,
            "",
            "缺少 '## 一页看懂' 这一节 (标题必须一字不差).",
            "在报告标题下面第一节加 '## 一页看懂', 写 3-5 条带标签的结论.",
        )
    elif h2 and h2[0][0] != summary_ln:
        add(
            "error",
            "summary_not_first",
            summary_ln,
            h2[0][1],
            "'## 一页看懂' 必须是第一节.",
            "把 '## 一页看懂' 挪到所有其他 '##' 小节前面.",
        )
    if appendix_ln is None:
        add(
            "error",
            "no_appendix",
            0,
            "",
            "缺少以 '## 附录' 开头的附录小节, 技术细节没地方放.",
            "在正文后加 '## 附录: 证据与数据来源', 把证据表和技术细节放进去.",
        )
    if FIXED_ENDING not in md_text:
        add(
            "error",
            "no_ending",
            0,
            "",
            "缺少固定结尾 '本报告没有覆盖的'.",
            "把 report-template.md 里的'固定结尾'原样贴到附录之后.",
        )

    body_end = appendix_ln or (len(md_text.splitlines()) + 1)
    by_no = dict(lines)
    for ln, line in lines:
        # old labels (whole file)
        for m in OLD_RE.finditer(line):
            add(
                "error",
                "old_label",
                ln,
                line,
                "[%s] 是旧版标签, 不会渲染成徽章." % m.group(1),
                "改成 %s." % OLD_LABELS[m.group(1)],
            )
        # [推测] needs 依据 in the same unit
        if "[推测]" in line:
            unit = line
            nxt = by_no.get(ln + 1, "")
            if nxt.startswith((" ", "\t")) and not LIST_RE.match(nxt):
                unit += nxt
            cells = unit.split("|") if unit.lstrip().startswith("|") else [unit]
            for cell in cells:
                if "[推测]" in cell and "依据" not in cell:
                    add(
                        "error",
                        "guess_no_basis",
                        ln,
                        line,
                        "[推测] 后面没有写依据, 读者没法判断这个推测靠不靠谱.",
                        "紧跟一句 '依据: <哪个页面/哪组数据>'; 说不出依据就删掉这句.",
                    )
                    break
        # full-width punctuation
        if FULLWIDTH_RE.search(line):
            add(
                "warning",
                "fullwidth",
                ln,
                line,
                "有全角标点.",
                "统一改成英文半角标点 (, . ; : ! ? ( )).",
            )
        if ln >= body_end:
            continue
        # technical words before the appendix
        plain = URL_RE.sub(" ", line)
        explained = bool(EXPLAIN_RE.search(plain))
        for pattern, hint in TECH_TERMS:
            m = re.search(pattern, plain)
            if m:
                add(
                    "warning" if explained else "error",
                    "tech_term",
                    ln,
                    line,
                    "附录之前出现技术词 '%s'%s."
                    % (
                        m.group(0),
                        ", 已带解释, 请确认正文确实需要它" if explained else "",
                    ),
                    "换成大白话 (%s), 原词和细节放进附录." % hint,
                )
        for pattern, hint in SOFT_TECH_TERMS:
            m = re.search(pattern, strip_quotes(plain))
            if m:
                add(
                    "warning",
                    "soft_tech_term",
                    ln,
                    line,
                    "附录之前出现疑似技术词 '%s'." % m.group(0),
                    "如果不是页面原文引用, 换成大白话 (%s)." % hint,
                )
        # suspected over-labeling
        if line.lstrip().startswith("#"):
            continue
        for badge, claim in claim_segments(line):
            if badge != "已核实" or not claim:
                continue
            text = strip_quotes(claim)
            mj = JUDGMENT_RE.search(text)
            if mj:
                add(
                    "warning",
                    "over_label",
                    ln,
                    claim,
                    "带 [已核实] 的这句含判断用语 '%s'. [已核实] 只能给能直接指到"
                    "页面原文, 商品数据或登记记录的事实, 判断即使有数据支撑也不算."
                    % mj.group(0),
                    "拆成两句: 数据/原文部分保留 [已核实]; 含 '%s' 的判断部分改标 "
                    "[推测] 并写 '依据: <哪些数据>'. 如果整句其实是纯事实, 删掉判断用语."
                    % mj.group(0),
                )
                continue
            mm = MIXED_RE.search(text)
            if mm:
                add(
                    "warning",
                    "mixed_source",
                    ln,
                    claim,
                    "带 [已核实] 的这句含 '%s', 像是店铺或第三方的说法混进了已核实的事实."
                    % mm.group(0),
                    "拆开: 店铺说的标 [店铺自称], 第三方说的标 [第三方说法], "
                    "只把能直接查到的部分留给 [已核实].",
                )
    issues.sort(key=lambda i: (i.severity != "error", i.line))
    return Result(issues)


def format_result(result, path=None, limit=None):
    """Plain-Chinese report of a Result, one block per issue."""
    out = []
    head = "报告自检"
    if path:
        head += ": %s" % path
    out.append(head)
    if result.ok and not result.warnings:
        out.append("通过: 没有发现问题.")
        return "\n".join(out)
    out.append(
        "硬错误 %d 个 (必须改), 需要复查的提示 %d 个 (其中疑似标高 %d 个)."
        % (len(result.errors), len(result.warnings), result.over_label_count())
    )
    items = result.errors + result.warnings
    shown = items if limit is None else items[:limit]
    for it in shown:
        tag = (
            "错误"
            if it.severity == "error"
            else ("疑似标高" if it.kind in ("over_label", "mixed_source") else "提示")
        )
        where = "第 %d 行" % it.line if it.line else "全文"
        out.append("")
        out.append("[%s] %s%s" % (tag, where, (": " + it.text) if it.text else ""))
        out.append("  为什么: %s" % it.why)
        out.append("  怎么改: %s" % it.fix)
    if len(shown) < len(items):
        out.append("")
        out.append(
            "... 还有 %d 条, 运行 check_report.py 查看全部." % (len(items) - len(shown))
        )
    out.append("")
    if result.errors:
        out.append("结论: 未通过. 改完硬错误后重新运行本检查.")
    else:
        out.append(
            "结论: 没有硬错误. 请逐条复查上面的提示 (疑似标高要拆句或改标) 再交付."
        )
    return "\n".join(out)


def find_report(path):
    """Accept the run folder, its 原始数据 folder, or report.md itself."""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(path):
        return path
    for cand in (
        os.path.join(path, RAW_DIRNAME, "report.md"),
        os.path.join(path, "report.md"),
    ):
        if os.path.isfile(cand):
            return cand
    return None


def main(argv=None):
    try:
        import delivery

        delivery.safe_console()
    except Exception:  # noqa: BLE001 - console tweak is optional
        pass
    ap = argparse.ArgumentParser(description="Self-check report.md before delivery.")
    ap.add_argument("path", help="run folder, its 原始数据 folder, or report.md")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)
    md_path = find_report(args.path)
    if not md_path:
        print("错误: 找不到 report.md (在 %s 下)" % args.path, file=sys.stderr)
        return 2
    with open(md_path, encoding="utf-8-sig") as fh:
        result = check_text(fh.read())
    if args.json:
        data = {
            "ok": result.ok,
            "errors": [i.as_dict() for i in result.errors],
            "warnings": [i.as_dict() for i in result.warnings],
        }
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_result(result, md_path))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
