"""Configurable self-check engine for a finished report.md.

A skill describes its rules in a ``Profile`` (language, required headings,
fixed ending, forbidden technical words, ...) and calls ``check_text``.
Rules:

Hard errors (must be fixed, then re-run):
  - missing summary section (zh "## 一页看懂", en "## At a glance") or it is
    not the first "##" section
  - missing appendix section (zh "## 附录...", en "## Appendix...")
  - technical words before the appendix (profile.tech_terms). A line that
    explains the word in plain language (profile.explain_re) is downgraded
    to a warning.
  - old labels (profile.old_labels)
  - "inferred" badge without the basis word in the same line / cell / its
    continuation line
  - missing fixed ending (profile.fixed_ending)
  - missing completion status, only when profile.require_status is true
  - per-skill forbidden terms (profile.forbidden_terms), e.g. "合规" as a
    conclusion, anywhere before the appendix
  - per-skill rules (profile.rules): functions ``rule(ctx) -> [Issue]``;
    ``claim_badge_rule`` builds the common "this wording needs that badge"
    rule (e.g. "已开通" backed only by [页面显示])

Warnings (do not fail, but the agent must review each one):
  - suspected over-labeling: a "verified" claim containing judgment words
    or source words (someone's saying, an estimate)
  - soft technical words before the appendix
  - full-width punctuation

Python 3.8+, standard library only.
"""

import argparse
import json
import os
import re
import sys

from . import evidence, i18n

# ---------------------------------------------------------------------------
# default word lists (a skill may extend or replace them)
# ---------------------------------------------------------------------------

# Words that turn a fact into a judgment. Regex fragments; lookaheads drop
# common non-judgment compounds (靠谱, 基本信息, 关键词...).
JUDGMENT_WORDS_ZH = [
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
JUDGMENT_WORDS_EN = [
    r"(?i)\bkey\b",
    r"(?i)\bcore\b",
    r"(?i)\btherefore\b",
    r"(?i)\bthus\b",
    r"(?i)\bbecause\b",
    r"(?i)\bso that\b",
    r"(?i)\bmeans?\b",
    r"(?i)\bshows? that\b",
    r"(?i)\bproves?\b",
    r"(?i)\bstrateg(?:y|ic)\b",
    r"(?i)\blikely\b",
    r"(?i)\bprobably\b",
    r"(?i)\bclearly\b",
    r"(?i)\bobviously\b",
    r"(?i)\bshould\b",
    r"(?i)\bseems?\b",
    r"(?i)\bin order to\b",
    r"(?i)\badvantage\b",
    r"(?i)\bweakness\b",
    r"(?i)\brisk\b",
    r"(?i)\bsuccess(?:ful)?\b",
    r"(?i)\beffective\b",
    r"(?i)\bdeliberate(?:ly)?\b",
]
# Words that mean the claim is someone's saying, not a verified fact.
MIXED_SOURCE_WORDS_ZH = [
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
MIXED_SOURCE_WORDS_EN = [
    r"(?i)\bclaim(?:s|ed)?\b",
    r"(?i)\breportedly\b",
    r"(?i)\baccording to\b",
    r"(?i)\ballegedly\b",
    r"(?i)\bestimat(?:e|ed|es)\b",
    r"(?i)\brumou?red\b",
    r"(?i)\bsays\b",
]
# Technical words every skill keeps out of the body: (regex, plain hint).
COMMON_TECH_TERMS = {
    "zh": [
        (r"(?<![A-Za-z0-9])E[1-6](?![0-9])", "证据等级只写在附录, 正文用标签"),
        (r"summary\.json|evidence\.jsonl", "放附录"),
        (r"\bJSON\b", "公开数据"),
        (r"\bAPI\b", "公开数据接口"),
        (r"(?<![A-Za-z])raw/", "放附录"),
    ],
    "en": [
        (r"(?<![A-Za-z0-9])E[1-6](?![0-9])", "evidence levels belong in the appendix"),
        (r"summary\.json|evidence\.jsonl", "move to the appendix"),
        (r"\bJSON\b", "public data"),
        (r"(?<![A-Za-z])raw/", "move to the appendix"),
    ],
}
COMMON_SOFT_TECH_TERMS = {
    "zh": [(r"\b[a-z]+_[a-z_]+\b", "字段名放附录")],
    "en": [(r"\b[a-z]+_[a-z_]+\b", "field names belong in the appendix")],
}
EXPLAIN_RE = {
    "zh": r"也就是|即[:,: ]|指的是|俗称|简称|意思是",
    "en": r"(?i)\bi\.e\.|\bthat is\b|\bmeaning\b|\bwhich means\b|\balso called\b",
}
STATUS_KEYWORD = {"zh": "完成状态", "en": "Status"}

# Full-width punctuation, written as escapes so this file stays clean:
# , . ; : ! ? ( ) [ ]
FULLWIDTH_RE = re.compile(
    "[\uff0c\u3002\uff1b\uff1a\uff01\uff1f\uff08\uff09\u3010\u3011]"
)
QUOTE_RE = re.compile('"[^"\\n]*"|\u201c[^\u201d\\n]*\u201d|\u300c[^\u300d\\n]*\u300d')
URL_RE = re.compile(r"https?://\S+|\]\([^)]*\)|`[^`]*`")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
STATUS_RE = re.compile(r"\b(complete|partial|failed)\b")

MESSAGES = {
    "zh": {
        "no_summary": (
            "缺少 '{s}' 这一节 (标题必须一字不差).",
            "在报告标题下面第一节加 '{s}', 写 3-5 条带标签的结论.",
        ),
        "summary_not_first": (
            "'{s}' 必须是第一节.",
            "把 '{s}' 挪到所有其他 '##' 小节前面.",
        ),
        "no_appendix": (
            "缺少以 '{a}' 开头的附录小节, 技术细节没地方放.",
            "在正文后加 '{ax}', 把证据表和技术细节放进去.",
        ),
        "no_ending": (
            "缺少固定结尾 '{e}'.",
            "把 {t} 里的'固定结尾'原样贴到附录之后.",
        ),
        "no_status": (
            "报告里没有写完成状态 (complete / partial / failed).",
            "在附录里加一行 '{k}: complete' (或 partial / failed), 和实际情况一致.",
        ),
        "old_label": ("[{x}] 是旧版标签, 不会渲染成徽章.", "改成 {y}."),
        "guess_no_basis": (
            "[{g}] 后面没有写依据, 读者没法判断这个推测靠不靠谱.",
            "紧跟一句 '{b}: <哪个页面/哪组数据>'; 说不出依据就删掉这句.",
        ),
        "fullwidth": ("有全角标点.", "统一改成英文半角标点 (, . ; : ! ? ( ))."),
        "tech_term": (
            "附录之前出现技术词 '{x}'{y}.",
            "换成大白话 ({h}), 原词和细节放进附录.",
        ),
        "tech_explained": ", 已带解释, 请确认正文确实需要它",
        "soft_tech_term": (
            "附录之前出现疑似技术词 '{x}'.",
            "如果不是页面原文引用, 换成大白话 ({h}).",
        ),
        "over_label": (
            "带 [{v}] 的这句含判断用语 '{x}'. [{v}] 只能给能直接指到"
            "页面原文, 商品数据或登记记录的事实, 判断即使有数据支撑也不算.",
            "拆成两句: 数据/原文部分保留 [{v}]; 含 '{x}' 的判断部分改标 "
            "[{g}] 并写 '{b}: <哪些数据>'. 如果整句其实是纯事实, 删掉判断用语.",
        ),
        "mixed_source": (
            "带 [{v}] 的这句含 '{x}', 像是店铺或第三方的说法混进了已核实的事实.",
            "拆开: 店铺说的标 [{sc}], 第三方说的标 [{tp}], 只把能直接查到的部分留给 [{v}].",
        ),
        "head": "报告自检",
        "pass": "通过: 没有发现问题.",
        "counts": "硬错误 %d 个 (必须改), 需要复查的提示 %d 个 (其中疑似标高 %d 个).",
        "tag_error": "错误",
        "tag_over": "疑似标高",
        "tag_warning": "提示",
        "line": "第 %d 行",
        "whole": "全文",
        "why": "  为什么: %s",
        "fix": "  怎么改: %s",
        "more": "... 还有 %d 条, 运行 %s 查看全部.",
        "fail": "结论: 未通过. 改完硬错误后重新运行本检查.",
        "ok": "结论: 没有硬错误. 请逐条复查上面的提示 (疑似标高要拆句或改标) 再交付.",
        "not_found": "错误: 找不到 report.md (在 %s 下)",
    },
    "en": {
        "no_summary": (
            "The '{s}' section is missing (the heading must match exactly).",
            "Add '{s}' as the first section under the title with 3-5 labelled findings.",
        ),
        "summary_not_first": (
            "'{s}' must be the first section.",
            "Move '{s}' above every other '##' section.",
        ),
        "no_appendix": (
            "No section starting with '{a}', so technical details have no place.",
            "Add '{ax}' after the body and move the evidence table there.",
        ),
        "no_ending": (
            "The fixed ending '{e}' is missing.",
            "Paste the fixed ending from {t} unchanged after the appendix.",
        ),
        "no_status": (
            "The report does not state its status (complete / partial / failed).",
            "Add a line '{k}: complete' (or partial / failed) to the appendix.",
        ),
        "old_label": (
            "[{x}] is an old label and is not rendered as a badge.",
            "Use {y} instead.",
        ),
        "guess_no_basis": (
            "[{g}] has no basis, so readers cannot judge the inference.",
            "Follow it with '{b}: <which page / which data>'; no basis, no sentence.",
        ),
        "fullwidth": (
            "Full-width punctuation found.",
            "Use half-width punctuation (, . ; : ! ? ( )).",
        ),
        "tech_term": (
            "Technical word '{x}' before the appendix{y}.",
            "Say it in plain words ({h}) and keep the original in the appendix.",
        ),
        "tech_explained": ", explained in the line; check the body really needs it",
        "soft_tech_term": (
            "Possible technical word '{x}' before the appendix.",
            "Unless it is quoted from the page, say it in plain words ({h}).",
        ),
        "over_label": (
            "This [{v}] claim contains the judgment word '{x}'. [{v}] is only for "
            "facts that point straight at page text, data or a public record.",
            "Split it: keep [{v}] for the data part; label the part with '{x}' "
            "[{g}] and add '{b}: <which data>'. If it is a pure fact, drop the word.",
        ),
        "mixed_source": (
            "This [{v}] claim contains '{x}': someone's statement mixed into a fact.",
            "Split it: the store's words get [{sc}], third parties get [{tp}], "
            "only directly checkable parts keep [{v}].",
        ),
        "head": "Report self-check",
        "pass": "Passed: no issues found.",
        "counts": "%d hard errors (must fix), %d warnings to review (%d possible over-labels).",
        "tag_error": "Error",
        "tag_over": "Over-label?",
        "tag_warning": "Warning",
        "line": "line %d",
        "whole": "whole file",
        "why": "  Why: %s",
        "fix": "  Fix: %s",
        "more": "... %d more, run %s to see all.",
        "fail": "Result: failed. Fix the hard errors and run this check again.",
        "ok": "Result: no hard errors. Review each warning above before delivery.",
        "not_found": "Error: report.md not found (under %s)",
    },
}


class Profile(object):
    """Rules for one skill and language. Every list may be overridden."""

    def __init__(
        self,
        lang=None,
        summary_heading=None,
        appendix_prefix=None,
        appendix_example=None,
        fixed_ending=None,
        old_labels=None,
        judgment_words=None,
        mixed_source_words=None,
        tech_terms=None,
        soft_tech_terms=None,
        explain_re=None,
        template_name=None,
        script_name="check_report.py",
        require_status=False,
        badge_keys=None,
        extra_tech_terms=None,
        forbidden_terms=None,
        rules=None,
    ):
        self.lang = i18n.norm_lang(lang)
        zh = self.lang == "zh"
        self.summary_heading = summary_heading or i18n.text(
            "summary_heading", self.lang
        )
        self.appendix_prefix = appendix_prefix or i18n.text(
            "appendix_prefix", self.lang
        )
        self.appendix_example = appendix_example or i18n.text(
            "appendix_example", self.lang
        )
        self.fixed_ending = fixed_ending or i18n.text("fixed_ending", self.lang)
        self.badge_keys = evidence.check_keys(badge_keys)
        self.badges = evidence.labels(self.lang, self.badge_keys)
        self.verified = evidence.label("verified", self.lang)
        self.inferred = evidence.label("inferred", self.lang)
        self.basis_word = evidence.BASIS_WORD[self.lang]
        self.old_labels = dict(
            (evidence.OLD_LABELS_ZH if zh else {}) if old_labels is None else old_labels
        )
        self.judgment_words = list(
            judgment_words
            if judgment_words is not None
            else (JUDGMENT_WORDS_ZH if zh else JUDGMENT_WORDS_EN)
        )
        self.mixed_source_words = list(
            mixed_source_words
            if mixed_source_words is not None
            else (MIXED_SOURCE_WORDS_ZH if zh else MIXED_SOURCE_WORDS_EN)
        )
        self.tech_terms = list(
            COMMON_TECH_TERMS[self.lang] if tech_terms is None else tech_terms
        )
        self.tech_terms += list(extra_tech_terms or [])
        # (regex, why, fix): hard error before the appendix, never downgraded;
        # a %s in why is replaced by the matched text
        self.forbidden_terms = list(forbidden_terms or [])
        self.rules = list(rules or [])
        self.soft_tech_terms = list(
            COMMON_SOFT_TECH_TERMS[self.lang]
            if soft_tech_terms is None
            else soft_tech_terms
        )
        self.explain_re = re.compile(explain_re or EXPLAIN_RE[self.lang])
        self.template_name = template_name or ("report-template.%s.md" % self.lang)
        self.script_name = script_name
        self.require_status = require_status
        self.msg = MESSAGES[self.lang]
        self.badge_re = re.compile(
            r"\[(%s)\]" % "|".join(re.escape(b) for b in self.badges)
        )
        self.old_re = (
            re.compile(r"\[(%s)\]" % "|".join(re.escape(k) for k in self.old_labels))
            if self.old_labels
            else None
        )
        self.judgment_re = _join(self.judgment_words)
        self.mixed_re = _join(self.mixed_source_words)
        basis_flags = 0 if zh else re.I
        self.basis_re = re.compile(re.escape(self.basis_word), basis_flags)
        self.inferred_tag = "[%s]" % self.inferred
        self.fmt = {
            "s": self.summary_heading,
            "a": self.appendix_prefix,
            "ax": self.appendix_example,
            "e": self.fixed_ending,
            "t": self.template_name,
            "v": self.verified,
            "g": self.inferred,
            "b": self.basis_word,
            "sc": evidence.label("self_claim", self.lang),
            "tp": evidence.label("third_party", self.lang),
            "k": STATUS_KEYWORD[self.lang],
        }

    def message(self, kind, **extra):
        why, fix = self.msg[kind]
        values = dict(self.fmt, **extra)
        return why.format(**values), fix.format(**values)


def _join(words):
    """One regex from fragments; inline flags like (?i) are kept per fragment."""
    if not words:
        return None
    parts = []
    flags = 0
    for w in words:
        if w.startswith("(?i)"):
            flags = re.I
            w = w[4:]
        parts.append("(?:%s)" % w)
    return re.compile("|".join(parts), flags)


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
    def __init__(self, issues, status=None):
        self.errors = [i for i in issues if i.severity == "error"]
        self.warnings = [i for i in issues if i.severity == "warning"]
        self.status = status

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


def claim_segments(line, badge_re):
    """Yield (badge, claim_text) for each badge in a line.

    Labels trail their claim ("... 在售 132 款 [已核实]"), so the claim is
    the text between the previous badge (or cell / line start) and this one.
    A badge at the start of its segment claims the text after it instead.
    """
    for cell in line.split("|"):
        matches = list(badge_re.finditer(cell))
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


def report_status(md_text, profile=None):
    """The status a report declares ("complete" / "partial" / "failed").

    Looks for the status keyword (zh 完成状态, en Status) followed by one of
    the values on the same line; a line listing all three (a template hint)
    does not count.
    """
    lang = profile.lang if profile else i18n.DEFAULT_LANG
    keyword = STATUS_KEYWORD[lang]
    for _ln, line in content_lines(md_text):
        if keyword.lower() not in line.lower():
            continue
        found = STATUS_RE.findall(
            line.split(keyword, 1)[-1] if keyword in line else line
        )
        if len(set(found)) == 1:
            return found[0]
    return None


def check_text(md_text, profile):
    """Run every rule on report markdown and return a Result."""
    issues = []
    p = profile

    def add(sev, kind, ln, text, why, fix):
        issues.append(Issue(sev, kind, ln, clip(text), why, fix))

    lines = list(content_lines(md_text))
    h2 = [(i, s.strip()) for i, s in lines if re.match(r"^##\s", s)]
    summary_ln = next((i for i, s in h2 if s == p.summary_heading), None)
    appendix_ln = next((i for i, s in h2 if s.startswith(p.appendix_prefix)), None)

    if summary_ln is None:
        add("error", "no_summary", 0, "", *p.message("no_summary"))
    elif h2 and h2[0][0] != summary_ln:
        add(
            "error",
            "summary_not_first",
            summary_ln,
            h2[0][1],
            *p.message("summary_not_first"),
        )
    if appendix_ln is None:
        add("error", "no_appendix", 0, "", *p.message("no_appendix"))
    if p.fixed_ending not in md_text:
        add("error", "no_ending", 0, "", *p.message("no_ending"))
    status = report_status(md_text, p)
    if p.require_status and status is None:
        add("error", "no_status", 0, "", *p.message("no_status"))

    body_end = appendix_ln or (len(md_text.splitlines()) + 1)
    by_no = dict(lines)
    for ln, line in lines:
        # old labels (whole file)
        if p.old_re is not None:
            for m in p.old_re.finditer(line):
                add(
                    "error",
                    "old_label",
                    ln,
                    line,
                    *p.message("old_label", x=m.group(1), y=p.old_labels[m.group(1)]),
                )
        # inferred needs the basis word in the same unit
        if p.inferred_tag in line:
            unit = line
            nxt = by_no.get(ln + 1, "")
            if nxt.startswith((" ", "\t")) and not LIST_RE.match(nxt):
                unit += nxt
            cells = unit.split("|") if unit.lstrip().startswith("|") else [unit]
            for cell in cells:
                if p.inferred_tag in cell and not p.basis_re.search(cell):
                    add(
                        "error",
                        "guess_no_basis",
                        ln,
                        line,
                        *p.message("guess_no_basis"),
                    )
                    break
        # full-width punctuation
        if FULLWIDTH_RE.search(line):
            add("warning", "fullwidth", ln, line, *p.message("fullwidth"))
        if ln >= body_end:
            continue
        # technical words before the appendix
        plain = URL_RE.sub(" ", line)
        explained = bool(p.explain_re.search(plain))
        for pattern, hint in p.tech_terms:
            m = re.search(pattern, plain)
            if m:
                add(
                    "warning" if explained else "error",
                    "tech_term",
                    ln,
                    line,
                    *p.message(
                        "tech_term",
                        x=m.group(0),
                        y=p.msg["tech_explained"] if explained else "",
                        h=hint,
                    ),
                )
        for pattern, hint in p.soft_tech_terms:
            m = re.search(pattern, strip_quotes(plain))
            if m:
                add(
                    "warning",
                    "soft_tech_term",
                    ln,
                    line,
                    *p.message("soft_tech_term", x=m.group(0), h=hint),
                )
        for pattern, why, fix in p.forbidden_terms:
            m = re.search(pattern, plain)
            if m:
                why_text = why % m.group(0) if "%s" in why else why
                add("error", "forbidden_term", ln, line, why_text, fix)
        # suspected over-labeling
        if line.lstrip().startswith("#"):
            continue
        for badge, claim in claim_segments(line, p.badge_re):
            if badge != p.verified or not claim:
                continue
            text = strip_quotes(claim)
            mj = p.judgment_re.search(text) if p.judgment_re else None
            if mj:
                add(
                    "warning",
                    "over_label",
                    ln,
                    claim,
                    *p.message("over_label", x=mj.group(0)),
                )
                continue
            mm = p.mixed_re.search(text) if p.mixed_re else None
            if mm:
                add(
                    "warning",
                    "mixed_source",
                    ln,
                    claim,
                    *p.message("mixed_source", x=mm.group(0)),
                )
    ctx = RuleContext(md_text, lines, body_end, p)
    for rule in p.rules:
        issues.extend(rule(ctx) or [])
    issues.sort(key=lambda i: (i.severity != "error", i.line))
    return Result(issues, status)


class RuleContext(object):
    """What a per-skill rule sees: text, (lineno, line) pairs outside code
    fences, the first appendix line number and the profile."""

    def __init__(self, md_text, lines, body_end, profile):
        self.md_text = md_text
        self.lines = lines
        self.body_end = body_end
        self.profile = profile

    def body_lines(self):
        return [(ln, s) for ln, s in self.lines if ln < self.body_end]

    def issue(self, severity, kind, line, text, why, fix):
        return Issue(severity, kind, line, clip(text), why, fix)

    def badges_in(self, text):
        """Badge keys present in text, in order."""
        return [
            evidence.key_of(m.group(1)) for m in self.profile.badge_re.finditer(text)
        ]


def claim_badge_rule(
    pattern, kind, why, fix, forbidden=(), required=(), severity="error"
):
    """Rule: a body line matching ``pattern`` must not carry any badge key in
    ``forbidden`` as its only support, and must carry one of ``required``
    when given. Example (payments)::

        claim_badge_rule(r"已开通", "icon_as_enabled",
                         "页面只显示了图标, 不能写成 '已开通'.",
                         "改成 '页面显示了 X 图标 [页面显示]'.",
                         forbidden=("on_page",))
    """
    regex = re.compile(pattern)

    def rule(ctx):
        out = []
        for ln, line in ctx.body_lines():
            for cell in line.split("|"):
                if not regex.search(cell):
                    continue
                keys = [k for k in ctx.badges_in(cell) if k]
                bad = bool(keys) and all(k in forbidden for k in keys)
                missing = bool(required) and not any(k in required for k in keys)
                if bad or missing:
                    out.append(ctx.issue(severity, kind, ln, line, why, fix))
                    break
        return out

    return rule


def format_result(result, profile, path=None, limit=None):
    """Plain-language report of a Result, one block per issue."""
    msg = profile.msg
    out = []
    head = msg["head"]
    if path:
        head += ": %s" % path
    out.append(head)
    if result.ok and not result.warnings:
        out.append(msg["pass"])
        return "\n".join(out)
    out.append(
        msg["counts"]
        % (len(result.errors), len(result.warnings), result.over_label_count())
    )
    items = result.errors + result.warnings
    shown = items if limit is None else items[:limit]
    for it in shown:
        tag = (
            msg["tag_error"]
            if it.severity == "error"
            else (
                msg["tag_over"]
                if it.kind in ("over_label", "mixed_source")
                else msg["tag_warning"]
            )
        )
        where = msg["line"] % it.line if it.line else msg["whole"]
        out.append("")
        out.append("[%s] %s%s" % (tag, where, (": " + it.text) if it.text else ""))
        out.append(msg["why"] % it.why)
        out.append(msg["fix"] % it.fix)
    if len(shown) < len(items):
        out.append("")
        out.append(msg["more"] % (len(items) - len(shown), profile.script_name))
    out.append("")
    out.append(msg["fail"] if result.errors else msg["ok"])
    return "\n".join(out)


def result_dict(result, with_status=False, lang=None):
    data = {
        "ok": result.ok,
        "errors": [i.as_dict() for i in result.errors],
        "warnings": [i.as_dict() for i in result.warnings],
    }
    if with_status:
        data["status"] = result.status
        data["lang"] = i18n.norm_lang(lang)
    return data


def find_report(path, raw_dirname):
    """Accept the run folder, its raw-data folder, or report.md itself."""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(path):
        return path
    for cand in (
        os.path.join(path, raw_dirname, "report.md"),
        os.path.join(path, "report.md"),
    ):
        if os.path.isfile(cand):
            return cand
    return None


def main(argv, profile):
    """Generic CLI: ``check_report.py <run|raw|report.md> [--json]``.

    Exit code: 0 = no hard errors, 1 = hard errors, 2 = report.md not found.
    The JSON output carries ``status`` and ``lang`` as well.
    """
    ap = argparse.ArgumentParser(description="Self-check report.md before delivery.")
    ap.add_argument("path", help="run folder, its raw-data folder, or report.md")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)
    md_path = find_report(args.path, i18n.text("raw_dirname", profile.lang))
    if not md_path:
        print(profile.msg["not_found"] % args.path, file=sys.stderr)
        return 2
    with open(md_path, encoding="utf-8-sig") as fh:
        result = check_text(fh.read(), profile)
    if args.json:
        data = result_dict(result, with_status=True, lang=profile.lang)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_result(result, profile, md_path))
    return 0 if result.ok else 1
