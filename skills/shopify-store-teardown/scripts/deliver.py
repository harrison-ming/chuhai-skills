#!/usr/bin/env python3
"""Turn a finished run folder into files an ordinary seller can open.

Reads ``<run>/原始数据/report.md`` (+ summary.json, products.tsv) and writes:

    <run>/<店名> 对标拆解报告.pdf   (or .html when no browser can print)
    <run>/商品清单.csv             (UTF-8 with BOM, Chinese headers)
    <run>/原始数据/report.html
    <run>/原始数据/说明.txt

CLI:
    python deliver.py "<run folder>" [--no-open] [--html-only] [--title TITLE]
                      [--verbose] [--strict]

Before rendering, report.md is run through check_report.check_text() (the
same gate the agent runs by hand). Hard errors are printed and noted in the
final message, but the files are still generated; with ``--strict`` hard
errors stop delivery (exit code 1).

``--title`` replaces the default "<店名> 对标拆解报告" file name and fallback
page title (for example ``--title 多店对比报告`` in the Pro edition).

Markdown is converted by a small built-in converter (no third-party
packages). PDF printing uses a local Chrome / Edge / Chromium in headless
mode with a throwaway profile. Exit code 0 on success (HTML fallback
included), 1 on fatal errors such as a missing report.md.
Python 3.8+, standard library only.
"""

import argparse
import base64
import csv
import datetime
import html
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_report  # noqa: E402
import delivery  # noqa: E402

PDF_TIMEOUT = 120
# Note prefix when the browser exits non-zero without a PDF (e.g. Edge in a
# service / SYSTEM session exits 1002); main() turns it into plain language.
BROWSER_EXIT_NOTE = "浏览器退出码"
IMAGE_WARN_BYTES = 2 * 1024 * 1024
GENERATOR = "shopify-store-teardown"

BADGES = [
    ("已核实", "ok"),
    ("店铺自称", "self"),
    ("第三方说法", "third"),
    ("页面显示", "page"),
    ("推测", "guess"),
    ("有矛盾", "conflict"),
    ("查不到", "unknown"),
]
BADGE_CLASS = dict(BADGES)
BADGE_RE = re.compile(
    r"\[(%s)(?:\s*[,:;，：；]\s*([^\]\[]{0,60}))?\]" % "|".join(b for b, _ in BADGES)
)

# ---------------------------------------------------------------------------
# Markdown -> HTML (deliberately small; covers what report-template.md uses)
# ---------------------------------------------------------------------------

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
HR_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
QUOTE_RE = re.compile(r"^\s{0,3}>\s?(.*)$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
IMAGE_ONLY_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
NUMERIC_CELL_RE = re.compile(
    r"^[~≈约]?\s*[-+]?\s*[$¥€£]?\s*\d[\d,]*(\.\d+)?\s*"
    r"(%|x|倍|个|款|条|次|天|年|月|美元|元)?$"
)
# Emphasis delimiters: not inside words (ASCII letters/digits) and not glued
# to path or glob punctuation. CJK neighbours are allowed (这是**重点**).
_EM_BEFORE = r"(?<![A-Za-z0-9*\\/.\-])"
_EM_AFTER = r"(?![A-Za-z0-9*])"
STRONG_RE = re.compile(_EM_BEFORE + r"\*\*(?=[^\s*])(.+?)(?<=[^\s*])\*\*" + _EM_AFTER)
EM_RE = re.compile(
    _EM_BEFORE + r"\*(?=[^\s*.\-/])(.+?)(?<=[^\s*\-/])\*" + r"(?![A-Za-z0-9*.])"
)
# Table cells that must not wrap: IDs (E001), levels (E1-E4), dates, numbers.
NOWRAP_CELL_RE = re.compile(
    r"^(?:[A-Z]{1,4}-?\d{1,5}(?:\s*[-~/]\s*[A-Z]{0,4}\d{1,5})?"
    r"|\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?(?:[ T]\d{1,2}:\d{2}(?::\d{2})?Z?)?"
    r"|\d{4}\s*年(?:\s*\d{1,2}\s*月)?(?:\s*\d{1,2}\s*日)?"
    r"|[<>≤≥~≈约]?\s*[-+]?\s*[$¥€£]?\s*\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%|x|倍|个|款|条|次|天|年|月|美元|元|USD|CAD|EUR|GBP)?)$"
)
ALLOWED_TAGS_RE = re.compile(r"&lt;(/?sub|br\s*/?)&gt;", re.I)
CJK_RE = re.compile(r"[⺀-鿿＀-￯　-〿]")
SAFE_URL_RE = re.compile(r"^(https?:|mailto:|#|/|\.{0,2}/|[\w\-.%~]+(/|$|#|\?))", re.I)


class Converter(object):
    """Convert report markdown to an HTML fragment.

    ``base_dir`` resolves relative image paths; ``warnings`` collects
    human-readable notes (missing / large images).
    """

    def __init__(self, base_dir="."):
        self.base_dir = base_dir
        self.warnings = []
        self.title = None

    # -- inline -----------------------------------------------------------

    def inline(self, text):
        slots = []

        def keep(fragment):
            slots.append(fragment)
            return "\x00%d\x00" % (len(slots) - 1)

        text = re.sub(
            r"(`+)(.+?)\1",
            lambda m: keep("<code>%s</code>" % html.escape(m.group(2).strip())),
            text,
        )
        text = html.escape(text, quote=False)
        text = ALLOWED_TAGS_RE.sub(lambda m: keep(self._allowed_tag(m.group(1))), text)
        text = re.sub(
            r"!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+&quot;[^)]*&quot;|\s+\"[^)]*\")?\s*\)",
            lambda m: keep(self.image(html.unescape(m.group(1)), m.group(2))),
            text,
        )
        text = re.sub(
            r"\[([^\]]+)\]\(\s*([^)\s]+)\s*\)",
            lambda m: keep(self.link(m.group(1), m.group(2))),
            text,
        )
        text = re.sub(
            r"(?<![\w/\"'=])(https?://[^\s<>()\x00]+?)(?=[.,;:!?)]*(?:\s|$|\x00))",
            lambda m: keep(self.link(m.group(1), m.group(1))),
            text,
        )
        text = BADGE_RE.sub(lambda m: keep(self.badge(m.group(1), m.group(2))), text)
        # Only ``*`` delimits emphasis (``_`` never does: __NEXT_DATA__,
        # snake_case). A delimiter next to a letter/digit or path punctuation
        # does not count, so globs like ``raw/products-*.json`` stay literal.
        text = STRONG_RE.sub(r"<strong>\1</strong>", text)
        text = EM_RE.sub(r"<em>\1</em>", text)
        for _ in range(3):  # nested placeholders (badge inside link text etc.)
            text = re.sub(r"\x00(\d+)\x00", lambda m: slots[int(m.group(1))], text)
        return text

    @staticmethod
    def _allowed_tag(tag):
        tag = tag.lower().replace(" ", "")
        if tag.startswith("br"):
            return "<br>"
        return "<%s>" % tag

    @staticmethod
    def badge(label, extra):
        cls = BADGE_CLASS[label]
        inner = html.escape(label)
        if extra and extra.strip():
            inner += '<span class="badge-extra">%s</span>' % extra.strip()
        return '<span class="badge badge-%s">%s</span>' % (cls, inner)

    def link(self, label_html, url_escaped):
        url = html.unescape(url_escaped)
        if not SAFE_URL_RE.match(url) or url.lower().startswith("javascript:"):
            return label_html
        return '<a href="%s">%s</a>' % (html.escape(url, quote=True), label_html)

    def image(self, alt, src_escaped):
        src = html.unescape(src_escaped)
        alt_attr = html.escape(alt, quote=True)
        if re.match(r"^https?://", src, re.I):
            return '<img src="%s" alt="%s">' % (html.escape(src, quote=True), alt_attr)
        if re.match(r"^[a-z][a-z0-9+.-]*:", src, re.I) and not re.match(
            r"^[a-zA-Z]:[\\/]", src
        ):
            self.warnings.append("图片地址不支持, 已跳过: %s" % src)
            return '<span class="img-missing">[图片无法显示: %s]</span>' % html.escape(
                alt or src
            )
        path = src if os.path.isabs(src) else os.path.join(self.base_dir, src)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            self.warnings.append("找不到图片: %s" % src)
            return '<span class="img-missing">[图片缺失: %s]</span>' % html.escape(
                alt or src
            )
        if len(data) > IMAGE_WARN_BYTES:
            self.warnings.append(
                "图片较大 (%.1f MB), 已原样嵌入, 报告文件会变大: %s"
                % (len(data) / 1048576.0, src)
            )
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if not mime.startswith("image/"):
            self.warnings.append("不是图片文件, 已跳过: %s" % src)
            return '<span class="img-missing">[图片无法显示: %s]</span>' % html.escape(
                alt or src
            )
        uri = "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))
        return '<img src="%s" alt="%s">' % (uri, alt_attr)

    # -- blocks -----------------------------------------------------------

    def convert(self, md):
        lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        blocks = self.parse_blocks(lines)
        return self.render_sections(blocks)

    def parse_blocks(self, lines):
        """Return a list of (kind, payload) where payload is rendered HTML,
        except for headings: ("h", (level, text_html, plain))."""
        out = []
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            if FENCE_RE.match(line):
                fence = FENCE_RE.match(line).group(1)
                i += 1
                buf = []
                while i < n and not lines[i].strip().startswith(fence):
                    buf.append(lines[i])
                    i += 1
                i += 1
                out.append(
                    ("html", "<pre><code>%s</code></pre>" % html.escape("\n".join(buf)))
                )
                continue
            m = HEADING_RE.match(line)
            if m:
                level = len(m.group(1))
                plain = m.group(2)
                out.append(("h", (level, self.inline(plain), plain)))
                i += 1
                continue
            if HR_RE.match(line):
                out.append(("hr", "<hr>"))
                i += 1
                continue
            if QUOTE_RE.match(line):
                buf = []
                while i < n and lines[i].strip() and QUOTE_RE.match(lines[i]):
                    buf.append(QUOTE_RE.match(lines[i]).group(1))
                    i += 1
                inner = self.parse_blocks(buf)
                out.append(
                    ("html", "<blockquote>%s</blockquote>" % self.render_flat(inner))
                )
                continue
            if (
                "|" in line
                and i + 1 < n
                and TABLE_SEP_RE.match(lines[i + 1])
                and "-" in lines[i + 1]
            ):
                j = i + 2
                while j < n and lines[j].strip() and "|" in lines[j]:
                    j += 1
                out.append(
                    ("html", self.table(lines[i], lines[i + 1], lines[i + 2 : j]))
                )
                i = j
                continue
            if LIST_RE.match(line):
                j = i
                items = []
                while j < n:
                    cur = lines[j]
                    if not cur.strip():
                        # a blank line ends the list unless the list continues
                        k = j + 1
                        while k < n and not lines[k].strip():
                            k += 1
                        if k < n and LIST_RE.match(lines[k]):
                            j = k
                            continue
                        break
                    lm = LIST_RE.match(cur)
                    if lm:
                        indent = len(lm.group(1).expandtabs(4))
                        if (
                            items
                            and indent < items[0][0] + 2
                            and lm.group(2)[0].isdigit() != items[0][1][0].isdigit()
                        ):
                            break  # bullet <-> numbered switch starts a new list
                        items.append([indent, lm.group(2), lm.group(3)])
                    elif items and (cur.startswith(" ") or cur.startswith("\t")):
                        items[-1][2] += "\n" + cur.strip()
                    elif items and not self.starts_block(cur):
                        items[-1][2] += "\n" + cur.strip()  # lazy continuation
                    else:
                        break
                    j += 1
                out.append(("html", self.list_html(items)))
                i = j
                continue
            if IMAGE_ONLY_RE.match(line):
                out.append(("html", "<figure>%s</figure>" % self.inline(line.strip())))
                i += 1
                continue
            buf = [line.strip()]
            i += 1
            while (
                i < n
                and lines[i].strip()
                and not self.starts_block(lines[i], lines[i + 1] if i + 1 < n else "")
            ):
                buf.append(lines[i].strip())
                i += 1
            out.append(("html", "<p>%s</p>" % self.inline(join_lines(buf))))
        return out

    @staticmethod
    def starts_block(line, nxt=""):
        return bool(
            HEADING_RE.match(line)
            or HR_RE.match(line)
            or FENCE_RE.match(line)
            or QUOTE_RE.match(line)
            or LIST_RE.match(line)
            or ("|" in line and TABLE_SEP_RE.match(nxt or "") and "-" in (nxt or ""))
        )

    def list_html(self, items):
        """items: [indent, marker, text]; supports one nesting level."""
        base = min(it[0] for it in items)
        top = []
        for indent, marker, text in items:
            if indent >= base + 2 and top:
                top[-1]["children"].append((marker, text))
            else:
                top.append({"marker": marker, "text": text, "children": []})

        def tag_for(marker):
            return "ol" if marker[0].isdigit() else "ul"

        def open_tag(marker):
            tag = tag_for(marker)
            if tag == "ol":
                start = int(re.match(r"\d+", marker).group(0))
                if start != 1:
                    return '<ol start="%d">' % start
            return "<%s>" % tag

        parts = [open_tag(top[0]["marker"])]
        for it in top:
            body = self.inline(join_lines(it["text"].split("\n")))
            if it["children"]:
                cm = it["children"][0][0]
                sub = [open_tag(cm)]
                for _m, t in it["children"]:
                    sub.append("<li>%s</li>" % self.inline(join_lines(t.split("\n"))))
                sub.append("</%s>" % tag_for(cm))
                body += "".join(sub)
            parts.append("<li>%s</li>" % body)
        parts.append("</%s>" % tag_for(top[0]["marker"]))
        return "".join(parts)

    def table(self, header, sep, rows):
        heads = split_row(header)
        aligns = []
        for cell in split_row(sep):
            c = cell.strip()
            if c.startswith(":") and c.endswith(":"):
                aligns.append("center")
            elif c.endswith(":"):
                aligns.append("right")
            elif c.startswith(":"):
                aligns.append("left")
            else:
                aligns.append(None)
        body = [split_row(r) for r in rows]
        ncol = len(heads)
        aligns = (aligns + [None] * ncol)[:ncol]
        for ci in range(ncol):
            if aligns[ci] is None:
                vals = [r[ci].strip() for r in body if ci < len(r) and r[ci].strip()]
                vals = [v for v in vals if v not in ("-", "—", "无", "n/a", "N/A")]
                if vals and all(NUMERIC_CELL_RE.match(strip_md(v)) for v in vals):
                    aligns[ci] = "right"

        def cell(tag, text, ci):
            al = aligns[ci] if ci < len(aligns) else None
            classes = ["al-%s" % al] if al else []
            if is_nowrap_cell(text) if tag == "td" else len(strip_md(text)) <= 6:
                classes.append("nw")
            attr = ' class="%s"' % " ".join(classes) if classes else ""
            return "<%s%s>%s</%s>" % (tag, attr, self.inline(text.strip()), tag)

        out = ["<table><thead><tr>"]
        out.extend(cell("th", h, ci) for ci, h in enumerate(heads))
        out.append("</tr></thead><tbody>")
        for r in body:
            r = (r + [""] * ncol)[:ncol]
            out.append(
                "<tr>%s</tr>" % "".join(cell("td", c, ci) for ci, c in enumerate(r))
            )
        out.append("</tbody></table>")
        return "".join(out)

    def render_flat(self, blocks):
        parts = []
        for kind, payload in blocks:
            if kind == "h":
                level, text_html, _plain = payload
                parts.append("<h%d>%s</h%d>" % (level, text_html, level))
            else:
                parts.append(payload)
        return "\n".join(parts)

    def render_sections(self, blocks):
        """Group by h2; style the 一页看懂 card and the appendix.

        The appendix (small grey text) runs from the first ``## ...附录...``
        heading up to the next ``---``; what follows the rule is the fixed
        closing block, rendered at normal size.
        """
        parts = []
        in_section = False
        appendix = False
        for kind, payload in blocks:
            if kind == "hr" and appendix:
                if in_section:
                    parts.append("</section>")
                parts.append('<section class="closing">')
                in_section = True
                appendix = False
                continue
            if kind == "h":
                level, text_html, plain = payload
                if level == 1 and self.title is None:
                    self.title = strip_md(plain)
                    parts.append('<h1 class="doc-title">%s</h1>' % text_html)
                    continue
                if level == 2:
                    if in_section:
                        parts.append("</section>")
                    classes = []
                    if "附录" in plain:
                        appendix = True
                    if appendix:
                        classes.append("appendix")
                    elif "一页看懂" in plain:
                        classes.append("summary-card")
                    parts.append('<section class="%s">' % " ".join(classes or ["sec"]))
                    in_section = True
                parts.append("<h%d>%s</h%d>" % (level, text_html, level))
            else:
                parts.append(payload)
        if in_section:
            parts.append("</section>")
        return "\n".join(parts)


def split_row(line):
    line = line.strip().replace("\\|", "\x01")
    # keep pipes inside inline code
    line = re.sub(r"`[^`]*`", lambda m: m.group(0).replace("|", "\x01"), line)
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.replace("\x01", "|") for c in line.split("|")]


def is_nowrap_cell(text):
    """Short tokens (IDs, levels, dates, numbers) are kept on one line."""
    plain = strip_md(text)
    return bool(plain) and len(plain) <= 24 and bool(NOWRAP_CELL_RE.match(plain))


def strip_md(text):
    text = re.sub(r"[*_`]", "", text)
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text).strip()


def join_lines(lines):
    """Join soft-wrapped lines; no space between two CJK characters."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out and not (CJK_RE.match(out[-1]) and CJK_RE.match(ln[0])):
            out += " "
        out += ln
    return out


# ---------------------------------------------------------------------------
# HTML document
# ---------------------------------------------------------------------------

CSS = """
@page { size: A4; margin: 20mm 18mm; }
* { -webkit-print-color-adjust: exact; print-color-adjust: exact; box-sizing: border-box; }
html { background: #fff; }
body {
  font-family: "PingFang SC","Microsoft YaHei","Hiragino Sans GB","Noto Sans CJK SC",sans-serif;
  font-size: 11pt; line-height: 1.7; color: #1f2933; margin: 0 auto; max-width: 820px;
  padding: 0; background: #fff; word-wrap: break-word; overflow-wrap: anywhere;
}
@media screen { body { padding: 32px 24px 48px; } }
h1, h2, h3, h4 { color: #1f3a5f; line-height: 1.35; break-after: avoid; page-break-after: avoid; }
h1.doc-title { font-size: 21pt; margin: 0 0 6mm; padding-bottom: 3mm; border-bottom: 2.5pt solid #1f3a5f; }
h1 { font-size: 18pt; }
h2 { font-size: 15pt; margin: 9mm 0 3mm; padding-bottom: 1.5mm; border-bottom: 0.75pt solid #c9d3df; }
h3 { font-size: 12.5pt; margin: 6mm 0 2mm; }
h4 { font-size: 11pt; margin: 4mm 0 1.5mm; color: #33506f; }
p { margin: 0 0 3mm; }
ul, ol { margin: 0 0 3mm; padding-left: 6mm; }
li { margin: 0.8mm 0; }
li > ul, li > ol { margin: 1mm 0 0; }
strong { color: #10263f; }
a { color: #1f5fa8; text-decoration: none; word-break: break-all; }
code { font-family: "SFMono-Regular",Consolas,Menlo,monospace; font-size: 0.88em;
  background: #f1f4f8; border-radius: 3px; padding: 0 3px; }
pre { background: #f1f4f8; padding: 3mm; border-radius: 4px; white-space: pre-wrap; font-size: 9pt; }
pre code { background: none; padding: 0; }
blockquote { margin: 3mm 0; padding: 2mm 4mm; border-left: 3pt solid #9fb3c8; background: #f5f8fb; color: #3e4c59; }
blockquote p:last-child { margin-bottom: 0; }
hr { border: 0; border-top: 0.75pt solid #d5dde6; margin: 6mm 0; }
table { border-collapse: collapse; width: 100%; margin: 2mm 0 4mm; font-size: 9.5pt; line-height: 1.5; }
thead { display: table-header-group; }
th { background: #1f3a5f; color: #fff; font-weight: 600; text-align: left; }
th, td { border: 0.5pt solid #c9d3df; padding: 1.6mm 2.2mm; vertical-align: top; }
tbody tr:nth-child(even) td { background: #f3f6fa; }
tr { break-inside: avoid; page-break-inside: avoid; }
.al-right { text-align: right; } .al-center { text-align: center; } .al-left { text-align: left; }
td.nw, th.nw { white-space: nowrap; overflow-wrap: normal; word-break: keep-all; }
img { max-width: 100%; height: auto; }
figure { margin: 3mm 0; text-align: center; break-inside: avoid; page-break-inside: avoid; }
.img-missing { color: #9aa5b1; font-size: 9pt; }
.badge { display: inline-block; font-size: 8pt; line-height: 1.35; padding: 0.2mm 1.6mm;
  margin: 0 0.6mm; border-radius: 8pt; font-weight: 600; vertical-align: 1px; white-space: nowrap; }
.badge-extra { font-weight: 400; margin-left: 1mm; opacity: 0.85; }
.badge-ok { background: #e3f4e8; color: #1e7b3c; border: 0.5pt solid #9fd6b0; }
.badge-self { background: #e4eefb; color: #1f5fa8; border: 0.5pt solid #a9c6ec; }
.badge-third { background: #eceef1; color: #4b5563; border: 0.5pt solid #c4c9d0; }
.badge-page { background: #e6ecf2; color: #3d5a78; border: 0.5pt solid #b3c3d4; }
.badge-guess { background: #fdf0e1; color: #b45309; border: 0.5pt solid #f2c48f; }
.badge-conflict { background: #fde8e8; color: #b42318; border: 0.5pt solid #f3aaaa; }
.badge-unknown { background: #f5f6f7; color: #8a939d; border: 0.5pt solid #dde1e5; }
section.summary-card { background: #f2f7fc; border-left: 4pt solid #1f3a5f; border-radius: 6px;
  padding: 1mm 6mm 3mm; margin: 4mm 0 8mm; }
section.summary-card h2 { border-bottom: none; margin-top: 3mm; }
section.appendix { font-size: 9.5pt; color: #52606d; }
section.appendix h2 { font-size: 13pt; color: #52606d; }
section.appendix h3, section.appendix h4 { color: #52606d; }
section.appendix table { font-size: 8.5pt; }
sub { vertical-align: baseline; font-size: 0.86em; color: #7b8794; }
h1.doc-title + p > sub:first-child { display: block; margin-top: -3mm; }
section.closing { margin-top: 6mm; padding: 3mm 5mm; border-top: 0.75pt solid #d5dde6;
  background: #f7f8fa; border-radius: 4px; color: #3e4c59; }
section.closing h3 { color: #3e4c59; margin-top: 1mm; }
.doc-footer { margin-top: 10mm; padding-top: 2mm; border-top: 0.5pt solid #d5dde6;
  font-size: 8.5pt; color: #9aa5b1; text-align: center; }
"""


def build_html(md_text, base_dir, fallback_title, date_str):
    conv = Converter(base_dir)
    body = conv.convert(md_text)
    title = conv.title or fallback_title
    footer = ""
    if GENERATOR + " " not in md_text and GENERATOR + "(" not in md_text:
        footer = '<div class="doc-footer">由 %s 生成 · %s</div>\n' % (
            GENERATOR,
            html.escape(date_str),
        )
    doc = (
        '<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n%s\n%s"
        "</body>\n</html>\n"
    ) % (html.escape(title), CSS, body, footer)
    return doc, conv.warnings


# ---------------------------------------------------------------------------
# PDF via headless Chromium
# ---------------------------------------------------------------------------


def valid_pdf(path, complete=False):
    """True when path looks like a real PDF (> 1 KB, ``%PDF`` header).

    ``complete`` also requires the ``%%EOF`` trailer near the end.
    """
    try:
        size = os.path.getsize(path)
        if size <= 1024:
            return False
        with open(path, "rb") as fh:
            if fh.read(4) != b"%PDF":
                return False
            if complete:
                fh.seek(max(size - 1024, 0))
                return b"%%EOF" in fh.read()
        return True
    except OSError:
        return False


def _stop(proc):
    """Terminate the browser (and its helpers on POSIX) without raising."""
    try:
        if proc.poll() is not None:
            return
        if os.name == "posix" and hasattr(os, "killpg"):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (OSError, AttributeError, TypeError):
                proc.terminate()
        else:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_browser(cmd, pdf_path, timeout, popen, poll_interval):
    """Run one headless print. Some Chrome builds keep running after the PDF
    is written, so success is detected from the file (complete and no longer
    growing) instead of waiting for the process to exit."""
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    try:
        proc = popen(cmd, **kwargs)
    except OSError as e:
        return None, "无法启动浏览器: %s" % e
    deadline = time.time() + timeout
    last_size, stable = -1, 0
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                if valid_pdf(pdf_path):
                    return True, ""
                return False, "%s %s" % (BROWSER_EXIT_NOTE, proc.returncode)
            if valid_pdf(pdf_path, complete=True):
                size = os.path.getsize(pdf_path)
                stable = stable + 1 if size == last_size else 0
                last_size = size
                if stable >= 2:
                    return True, ""
            time.sleep(poll_interval)
        return False, "超时 (%d 秒)" % timeout
    finally:
        _stop(proc)


def print_pdf(
    browser,
    html_path,
    pdf_path,
    timeout=PDF_TIMEOUT,
    popen=subprocess.Popen,
    poll_interval=0.5,
):
    """Print html_path to pdf_path with a throwaway profile. Returns (ok, note).

    ``--headless=new`` is tried first, then legacy ``--headless``.
    """
    url = Path(os.path.abspath(html_path)).as_uri()
    note = ""
    for headless in ("--headless=new", "--headless"):
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        profile = tempfile.mkdtemp(prefix="teardown-browser-")
        try:
            cmd = [
                browser,
                headless,
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--user-data-dir=%s" % profile,
                "--no-pdf-header-footer",
                "--print-to-pdf-no-header",
                "--print-to-pdf=%s" % pdf_path,
                url,
            ]
            ok, note = _run_browser(cmd, pdf_path, timeout, popen, poll_interval)
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        if ok:
            return True, ""
        if ok is None:  # browser could not start at all; no point retrying
            break
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    return False, note


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    ("商品名称", "title"),
    ("类型", "product_type"),
    ("最低价", "price_min"),
    ("最高价", "price_max"),
    ("原价", "compare_at_max"),
    ("币种", "currency"),
    ("是否有货", "available"),
    ("规格数", "variant_count"),
    ("首次上架时间", "created_at"),
    ("商品链接", "url"),
]
PLAIN_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")


def csv_safe(value):
    """Neutralise spreadsheet formula injection."""
    value = value or ""
    if value and value[0] in "=+-@\t\r" and not PLAIN_NUMBER_RE.match(value):
        return "'" + value
    return value


def fmt_available(v):
    v = (v or "").strip().lower()
    if v in ("true", "1", "yes"):
        return "是"
    if v in ("false", "0", "no"):
        return "否"
    return ""


def write_products_csv(tsv_path, csv_path):
    """Convert products.tsv to an Excel-friendly CSV. Returns row count."""
    with open(tsv_path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        rows = list(reader)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([c for c, _ in CSV_COLUMNS])
        for r in rows:
            out = []
            for _cn, key in CSV_COLUMNS:
                v = r.get(key) or ""
                if key == "available":
                    v = fmt_available(v)
                elif key == "created_at":
                    v = (v or r.get("published_at") or "")[:10]
                out.append(csv_safe(v))
            w.writerow(out)
    return len(rows)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def locate_run(path):
    """Return (run_dir, raw_dir). Accepts the 原始数据 folder itself too."""
    path = os.path.abspath(os.path.expanduser(path))
    raw = os.path.join(path, delivery.RAW_DIRNAME)
    if not os.path.isdir(raw) and os.path.basename(path) == delivery.RAW_DIRNAME:
        return os.path.dirname(path), path
    return path, raw


def load_summary(raw):
    try:
        with open(os.path.join(raw, "summary.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def guess_domain(run_dir, summary):
    if summary.get("domain"):
        return summary["domain"]
    name = os.path.basename(run_dir)
    return name.split(" ")[0] if name else "store"


def main(argv=None):
    delivery.safe_console()
    ap = argparse.ArgumentParser(
        description="Render report.md into a PDF (or HTML) plus a product CSV."
    )
    ap.add_argument(
        "run_dir", help='run folder, e.g. ".../example.com 对标拆解 2026-09-30"'
    )
    ap.add_argument("--no-open", action="store_true", help="do not open the folder")
    ap.add_argument("--html-only", action="store_true", help="skip PDF printing")
    ap.add_argument(
        "--verbose", action="store_true", help="print extra notes (e.g. no CSV)"
    )
    ap.add_argument(
        "--title",
        help='report file name / fallback title, default "<店名> 对标拆解报告"',
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="do not generate files when the report self-check has hard errors",
    )
    args = ap.parse_args(argv)

    run_dir, raw = locate_run(args.run_dir)
    md_path = os.path.join(raw, "report.md")
    if not os.path.isfile(md_path):
        print("错误: 找不到报告正文 %s" % md_path, file=sys.stderr)
        print(
            "请先让 agent 把报告写进这个文件, 再运行 deliver.py. "
            '传入的应当是 "<域名> 对标拆解 <日期>" 这一层文件夹.',
            file=sys.stderr,
        )
        return 1
    summary = load_summary(raw)
    domain = guess_domain(run_dir, summary)
    shop_name = (summary.get("shop") or {}).get("name") or ""
    if args.title and args.title.strip():
        base = delivery.sanitize_filename(args.title, fallback="report")
    else:
        base = delivery.report_basename(shop_name, domain)
    today = datetime.date.today().isoformat()

    try:
        with open(md_path, encoding="utf-8-sig") as fh:
            md_text = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        print("错误: 读取 report.md 失败: %s" % e, file=sys.stderr)
        return 1
    check = check_report.check_text(md_text)
    if check.errors or check.warnings:
        print(check_report.format_result(check, md_path, limit=12))
        print("")
    if check.errors and args.strict:
        print(
            "错误: 报告自检有 %d 个硬错误, 按 --strict 要求不生成文件. "
            "改完 report.md 后重新运行." % len(check.errors),
            file=sys.stderr,
        )
        return 1
    doc, warnings = build_html(md_text, raw, base, today)
    html_path = os.path.join(raw, "report.html")
    try:
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(doc)
        delivery.write_readme(raw)
    except OSError as e:
        print("错误: 写入文件失败: %s" % e, file=sys.stderr)
        return 1
    for w in warnings:
        print("提示: %s" % w)

    pdf_final = os.path.join(run_dir, base + ".pdf")
    html_final = os.path.join(run_dir, base + ".html")
    report_path, kind, why = None, None, ""
    if args.html_only:
        why = "按要求只生成网页版"
    else:
        browser = delivery.find_browser()
        if not browser:
            why = "这台电脑上没找到 Chrome / Edge 浏览器"
        else:
            print("正在用浏览器生成 PDF: %s" % browser)
            tmp_pdf = os.path.join(raw, "_render.pdf")
            ok, note = print_pdf(browser, html_path, tmp_pdf)
            if ok:
                os.replace(tmp_pdf, pdf_final)
                report_path, kind = pdf_final, "PDF"
                if os.path.exists(html_final):
                    os.remove(html_final)
            elif note.startswith(BROWSER_EXIT_NOTE):
                why = "浏览器在当前运行方式下没法打印 PDF (常见于后台服务, %s)" % note
            else:
                why = "浏览器没能生成 PDF (%s)" % note
    if report_path is None:
        shutil.copyfile(html_path, html_final)
        report_path, kind = html_final, "HTML"
        if os.path.exists(pdf_final):
            os.remove(pdf_final)  # stale PDF from an earlier run would mislead

    tsv = os.path.join(raw, "products.tsv")
    csv_path = os.path.join(run_dir, delivery.CSV_NAME)
    csv_rows = None
    if os.path.isfile(tsv):
        try:
            csv_rows = write_products_csv(tsv, csv_path)
        except (OSError, csv.Error, UnicodeDecodeError) as e:
            print("提示: 商品清单生成失败: %s" % e)
    elif args.verbose:
        print("提示: 没有 products.tsv, 未生成商品清单.")

    print("")
    print("完成. 拆解文件夹: %s" % run_dir)
    if kind == "PDF":
        print("报告 (PDF): %s" % report_path)
    else:
        print("报告 (网页版 HTML): %s" % report_path)
        print("  %s, 所以给的是网页版." % why)
        print("  双击用浏览器打开即可; 需要 PDF 可在浏览器里 打印 → 另存为 PDF.")
    if csv_rows is not None:
        print("商品清单 (%d 个商品, Excel/WPS 可直接打开): %s" % (csv_rows, csv_path))
    print("原始数据 (普通用户可以不看): %s" % raw)
    if check.errors:
        print(
            "注意: 报告自检有 %d 个硬错误没改 (详见上方), 文件照常生成. "
            "建议改完 report.md 后重新运行 deliver.py." % len(check.errors)
        )
    elif check.over_label_count():
        print(
            "注意: 报告自检有 %d 处疑似标高, 请确认已逐条复查."
            % check.over_label_count()
        )
    if not args.no_open and delivery.open_folder(run_dir):
        print("已为你打开这个文件夹.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
