"""Report rendering: markdown -> HTML -> PDF (HTML fallback), Excel CSV.

- ``Converter`` / ``build_html``: a deliberately small markdown converter
  covering what the report templates use (headings, lists with one nesting
  level, tables with auto alignment, quotes, code, images embedded as base64,
  evidence badges). The first h2 containing the summary marker becomes a
  card, the appendix (from the h2 containing the appendix marker up to the
  next ``---``) is small grey text, and what follows is the closing block.
- ``print_pdf``: headless Chrome / Edge / Chromium with a throwaway profile;
  success is detected from the file, not from the process exit.
- ``publish``: PDF when a browser can print, else the HTML copy, plus the
  plain-language reason.
- ``write_csv``: UTF-8 with BOM (Excel / WPS open Chinese correctly) and
  spreadsheet formula injection neutralised.

Python 3.8+, standard library only.
"""

import base64
import csv
import html
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from . import evidence, i18n, output

PDF_TIMEOUT = 120
IMAGE_WARN_BYTES = 2 * 1024 * 1024

MESSAGES = {
    "zh": {
        "img_unsupported": "图片地址不支持, 已跳过: %s",
        "img_unshown": "[图片无法显示: %s]",
        "img_missing": "找不到图片: %s",
        "img_missing_span": "[图片缺失: %s]",
        "img_large": "图片较大 (%.1f MB), 已原样嵌入, 报告文件会变大: %s",
        "img_not_image": "不是图片文件, 已跳过: %s",
        "browser_start": "无法启动浏览器: %s",
        "timeout": "超时 (%d 秒)",
        # Note prefix when the browser exits non-zero without a PDF (e.g. Edge
        # in a service / SYSTEM session exits 1002).
        "exit_note": "浏览器退出码",
        "printing": "正在用浏览器生成 PDF: %s",
        "why_html_only": "按要求只生成网页版",
        "why_no_browser": "这台电脑上没找到 Chrome / Edge 浏览器",
        "why_exit": "浏览器在当前运行方式下没法打印 PDF (常见于后台服务, %s)",
        "why_failed": "浏览器没能生成 PDF (%s)",
    },
    "en": {
        "img_unsupported": "Unsupported image address, skipped: %s",
        "img_unshown": "[image cannot be shown: %s]",
        "img_missing": "Image not found: %s",
        "img_missing_span": "[image missing: %s]",
        "img_large": "Large image (%.1f MB) embedded as is, the report gets bigger: %s",
        "img_not_image": "Not an image file, skipped: %s",
        "browser_start": "cannot start the browser: %s",
        "timeout": "timed out (%d s)",
        "exit_note": "browser exit code",
        "printing": "Printing the PDF with: %s",
        "why_html_only": "HTML only was requested",
        "why_no_browser": "no Chrome / Edge browser was found on this computer",
        "why_exit": (
            "the browser cannot print PDFs when run this way "
            "(common for background services, %s)"
        ),
        "why_failed": "the browser did not produce a PDF (%s)",
    },
}
BROWSER_EXIT_NOTE = MESSAGES["zh"]["exit_note"]


def exit_note(lang=None):
    return MESSAGES[i18n.norm_lang(lang)]["exit_note"]


def badge_regex(lang=None, keys=None):
    """``[label]`` or ``[label, extra]`` (half- or full-width separator)."""
    names = "|".join(re.escape(lb) for lb in evidence.labels(lang, keys))
    return re.compile(
        r"\[(%s)(?:\s*[,:;\uff0c\uff1a\uff1b]\s*([^\]\[]{0,60}))?\]" % names
    )


# ---------------------------------------------------------------------------
# Markdown -> HTML (deliberately small; covers what the report templates use)
# ---------------------------------------------------------------------------

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
HR_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
QUOTE_RE = re.compile(r"^\s{0,3}>\s?(.*)$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
IMAGE_ONLY_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
NUMERIC_UNITS = {
    "zh": r"(%|x|倍|个|款|条|次|天|年|月|美元|元)?",
    "en": r"(%|x|USD|CAD|EUR|GBP|AUD|days?|items?|pcs|orders?|months?|years?)?",
}
NUMERIC_CELL_RES = dict(
    (k, re.compile(r"^[~≈约]?\s*[-+]?\s*[$¥€£]?\s*\d[\d,]*(\.\d+)?\s*" + v + "$"))
    for k, v in NUMERIC_UNITS.items()
)
NUMERIC_CELL_RE = NUMERIC_CELL_RES["zh"]
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
CJK_RE = re.compile("[\u2e80-\u9fff\uff00-\uffef\u3000-\u303f]")
SAFE_URL_RE = re.compile(r"^(https?:|mailto:|#|/|\.{0,2}/|[\w\-.%~]+(/|$|#|\?))", re.I)


class Converter(object):
    """Convert report markdown to an HTML fragment.

    ``base_dir`` resolves relative image paths; ``warnings`` collects
    human-readable notes (missing / large images).
    """

    def __init__(self, base_dir=".", lang=None, badge_keys=None):
        self.base_dir = base_dir
        self.lang = i18n.norm_lang(lang)
        self.msg = MESSAGES[self.lang]
        self.badge_keys = evidence.check_keys(badge_keys)
        self.badge_class = dict(evidence.class_map(self.lang, self.badge_keys))
        self.badge_re = badge_regex(self.lang, self.badge_keys)
        self.numeric_re = NUMERIC_CELL_RES[self.lang]
        self.appendix_marker = i18n.text("appendix_marker", self.lang)
        self.summary_marker = i18n.text("summary_marker", self.lang)
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
        text = self.badge_re.sub(
            lambda m: keep(self.badge(m.group(1), m.group(2))), text
        )
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

    def badge(self, label, extra):
        cls = self.badge_class[label]
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
            self.warnings.append(self.msg["img_unsupported"] % src)
            return '<span class="img-missing">%s</span>' % (
                self.msg["img_unshown"] % html.escape(alt or src)
            )
        path = src if os.path.isabs(src) else os.path.join(self.base_dir, src)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            self.warnings.append(self.msg["img_missing"] % src)
            return '<span class="img-missing">%s</span>' % (
                self.msg["img_missing_span"] % html.escape(alt or src)
            )
        if len(data) > IMAGE_WARN_BYTES:
            self.warnings.append(self.msg["img_large"] % (len(data) / 1048576.0, src))
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if not mime.startswith("image/"):
            self.warnings.append(self.msg["img_not_image"] % src)
            return '<span class="img-missing">%s</span>' % (
                self.msg["img_unshown"] % html.escape(alt or src)
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
                if vals and all(self.numeric_re.match(strip_md(v)) for v in vals):
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
        """Group by h2; style the summary card and the appendix.

        The appendix (small grey text) runs from the first h2 containing the
        appendix marker (zh 附录, en Appendix)
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
                    if self.appendix_marker in plain:
                        appendix = True
                    if appendix:
                        classes.append("appendix")
                    elif self.summary_marker in plain:
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


def css_for(badge_keys=None):
    """CSS for a badge set; extra badges only add rules, never change CSS."""
    keys = evidence.check_keys(badge_keys)
    return CSS + "".join(EXTRA_BADGE_CSS[k] for k in keys if k in EXTRA_BADGE_CSS)


def build_html(
    md_text, base_dir, fallback_title, date_str, generator, lang=None, badge_keys=None
):
    """Full HTML document. A footer naming ``generator`` is added unless the
    markdown already mentions it (the templates' fixed ending does)."""
    lang = i18n.norm_lang(lang)
    conv = Converter(base_dir, lang, badge_keys)
    body = conv.convert(md_text)
    title = conv.title or fallback_title
    footer = ""
    if generator + " " not in md_text and generator + "(" not in md_text:
        footer = '<div class="doc-footer">%s</div>\n' % (
            i18n.text("footer", lang) % (generator, html.escape(date_str))
        )
    doc = (
        '<!doctype html>\n<html lang="%s">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n%s\n%s"
        "</body>\n</html>\n"
    ) % (
        i18n.text("html_lang", lang),
        html.escape(title),
        css_for(badge_keys),
        body,
        footer,
    )
    return doc, conv.warnings


EXTRA_BADGE_CSS = {
    "user_input": (
        ".badge-user { background: #f1ecfb; color: #5b3fa0; "
        "border: 0.5pt solid #c8b8ea; }\n"
    ),
}


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


def _run_browser(cmd, pdf_path, timeout, popen, poll_interval, lang=None):
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
        msg = MESSAGES[i18n.norm_lang(lang)]
        return None, msg["browser_start"] % e
    deadline = time.time() + timeout
    last_size, stable = -1, 0
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                if valid_pdf(pdf_path):
                    return True, ""
                return False, "%s %s" % (exit_note(lang), proc.returncode)
            if valid_pdf(pdf_path, complete=True):
                size = os.path.getsize(pdf_path)
                stable = stable + 1 if size == last_size else 0
                last_size = size
                if stable >= 2:
                    return True, ""
            time.sleep(poll_interval)
        return False, MESSAGES[i18n.norm_lang(lang)]["timeout"] % timeout
    finally:
        _stop(proc)


def print_pdf(
    browser,
    html_path,
    pdf_path,
    timeout=PDF_TIMEOUT,
    popen=subprocess.Popen,
    poll_interval=0.5,
    lang=None,
    profile_prefix="chuhai-browser-",
):
    """Print html_path to pdf_path with a throwaway profile. Returns (ok, note).

    ``--headless=new`` is tried first, then legacy ``--headless``.
    """
    url = Path(os.path.abspath(html_path)).as_uri()
    note = ""
    for headless in ("--headless=new", "--headless"):
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        profile = tempfile.mkdtemp(prefix=profile_prefix)
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
            ok, note = _run_browser(cmd, pdf_path, timeout, popen, poll_interval, lang)
        finally:
            shutil.rmtree(profile, ignore_errors=True)
        if ok:
            return True, ""
        if ok is None:  # browser could not start at all; no point retrying
            break
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    return False, note


def publish(
    html_path,
    run_dir,
    raw_dir,
    base,
    html_only=False,
    find_browser=None,
    printer=None,
    lang=None,
    log=print,
):
    """Place the report at ``<run_dir>/<base>.pdf``, else ``<base>.html``.

    ``html_path`` is the rendered document inside ``raw_dir``. Returns
    ``(report_path, kind, why)`` where kind is "PDF" or "HTML" and ``why`` is
    the plain-language reason for an HTML fallback. A stale PDF / HTML from
    an earlier run is removed so the folder never shows both.
    """
    msg = MESSAGES[i18n.norm_lang(lang)]
    find_browser = find_browser or output.find_browser
    printer = printer or print_pdf
    pdf_final = os.path.join(run_dir, base + ".pdf")
    html_final = os.path.join(run_dir, base + ".html")
    report_path, kind, why = None, None, ""
    if html_only:
        why = msg["why_html_only"]
    else:
        browser = find_browser()
        if not browser:
            why = msg["why_no_browser"]
        else:
            log(msg["printing"] % browser)
            tmp_pdf = os.path.join(raw_dir, "_render.pdf")
            ok, note = printer(browser, html_path, tmp_pdf)
            if ok:
                os.replace(tmp_pdf, pdf_final)
                report_path, kind = pdf_final, "PDF"
                if os.path.exists(html_final):
                    os.remove(html_final)
            elif note.startswith(exit_note(lang)):
                why = msg["why_exit"] % note
            else:
                why = msg["why_failed"] % note
    if report_path is None:
        shutil.copyfile(html_path, html_final)
        report_path, kind = html_final, "HTML"
        if os.path.exists(pdf_final):
            os.remove(pdf_final)  # stale PDF from an earlier run would mislead
    return report_path, kind, why


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

PLAIN_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")


def csv_safe(value):
    """Neutralise spreadsheet formula injection."""
    value = value or ""
    if value and value[0] in "=+-@\t\r" and not PLAIN_NUMBER_RE.match(value):
        return "'" + value
    return value


def cell_text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def write_csvs(run_dir, tables):
    """Write several CSVs: tables = [(file_name, header, rows)].

    A table whose rows is None is skipped (e.g. no product data in this run).
    Returns [(path, row_count)] for the files written.
    """
    done = []
    for name, header, rows in tables:
        if rows is None:
            continue
        path = os.path.join(run_dir, name)
        done.append((path, write_csv(path, header, rows)))
    return done


def write_csv(path, header, rows):
    """Excel-friendly CSV: UTF-8 with BOM, header as given, cells made safe.

    ``rows`` are sequences; None becomes an empty cell. Returns row count.
    """
    n = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(list(header))
        for row in rows:
            w.writerow([csv_safe(cell_text(v)) for v in row])
            n += 1
    return n
