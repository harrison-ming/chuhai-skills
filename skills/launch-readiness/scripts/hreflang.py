"""Validate language/region URL annotations (rule LOC-03, source S3).

Codes are validated by pattern plus a short list of frequent mistakes
instead of full ISO tables: language = 2-3 letters (optionally a 4-letter
script), region = 2 letters or 3 digits, or the literal ``x-default``.
Python 3.8+, standard library only.
"""

import re
from urllib.parse import urlsplit, urlunsplit

CODE_RE = re.compile(
    r"^(?P<lang>[a-z]{2,3})(?:-(?P<script>[a-z]{4}))?(?:-(?P<region>[a-z]{2}|\d{3}))?$",
    re.I,
)
# Frequent wrong codes -> what was probably meant.
BAD_LANGUAGES = {
    "jp": "ja",
    "cn": "zh",
    "kr": "ko",
    "dk": "da",
    "se": "sv",
    "gr": "el",
    "cz": "cs",
    "ua": "uk",
    "vn": "vi",
    "us": "en",
    "gb": "en",
    "eng": "en",
}
BAD_REGIONS = {"uk": "gb", "eu": None, "la": None, "en": None}


def code_problem(code):
    """None when the code is valid, else a short machine reason."""
    c = (code or "").strip()
    if c.lower() == "x-default":
        return None
    m = CODE_RE.match(c)
    if not m:
        return "bad_format"
    lang = m.group("lang").lower()
    region = (m.group("region") or "").lower()
    if lang in BAD_LANGUAGES:
        return "bad_language:%s->%s" % (lang, BAD_LANGUAGES[lang])
    if region in BAD_REGIONS:
        fix = BAD_REGIONS[region]
        return "bad_region:%s%s" % (region, "->" + fix if fix else "")
    return None


def norm_url(url):
    p = urlsplit((url or "").strip())
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/") or "/"
    return urlunsplit(("", host, path, p.query, ""))


def check(versions):
    """Validate annotations collected from several fetched versions.

    ``versions``: list of dicts {url, final_url, pairs: [[code, href], ...]}
    (the homepage first). Returns {status, problems: [...], codes, count}
    where status is pass | fail | absent; problems are dicts {kind, page,
    code, href, detail}.
    """
    problems = []
    fetched = [v for v in versions if v.get("pairs") is not None]
    home = fetched[0] if fetched else None
    if not home or not home.get("pairs"):
        return {"status": "absent", "problems": [], "codes": [], "count": 0}
    seen_codes = []
    for v in fetched:
        page = v.get("final_url") or v.get("url")
        pairs = v.get("pairs") or []
        if not pairs:
            problems.append(_p("missing_on_version", page, None, None, ""))
            continue
        hrefs = set()
        codes = {}
        for code, href in pairs:
            low = (code or "").lower()
            if low not in seen_codes:
                seen_codes.append(low)
            why = code_problem(code)
            if why:
                problems.append(_p("invalid_code", page, code, href, why))
            if not re.match(r"^https?://", href or "", re.I):
                problems.append(_p("relative_url", page, code, href, ""))
            if low in codes and norm_url(codes[low]) != norm_url(href):
                problems.append(_p("duplicate_code", page, code, href, codes[low]))
            codes.setdefault(low, href)
            hrefs.add(norm_url(href))
        if norm_url(page) not in hrefs and norm_url(v.get("url")) not in hrefs:
            problems.append(_p("no_self_reference", page, None, None, ""))
        if "x-default" not in codes:
            problems.append(_p("no_x_default", page, None, None, ""))
    # Return links: every fetched version listed by the homepage must list
    # the homepage back.
    home_url = norm_url(home.get("final_url") or home.get("url"))
    by_url = {}
    for v in fetched[1:]:
        by_url[norm_url(v.get("final_url") or v.get("url"))] = v
        by_url[norm_url(v.get("url"))] = v
    for code, href in home.get("pairs") or []:
        v = by_url.get(norm_url(href))
        if v is None or not v.get("pairs"):
            continue
        back = set(norm_url(h) for _c, h in v["pairs"])
        if home_url not in back:
            problems.append(
                _p("no_return_link", v.get("final_url") or v.get("url"), code, href, "")
            )
    status = "fail" if problems else "pass"
    return {
        "status": status,
        "problems": problems,
        "codes": seen_codes,
        "count": len(home.get("pairs") or []),
    }


def _p(kind, page, code, href, detail):
    return {"kind": kind, "page": page, "code": code, "href": href, "detail": detail}


def describe(problem, lang="zh"):
    """One plain-language line for the report body (no technical words)."""
    zh = {
        "invalid_code": "代码 %(code)s 无效 (%(detail)s)",
        "relative_url": "%(code)s 用的不是完整网址",
        "duplicate_code": "%(code)s 指向了两个不同网址",
        "no_self_reference": "页面 %(page)s 没有标注自己",
        "no_x_default": "页面 %(page)s 没有指定默认版本",
        "no_return_link": "%(page)s 没有回指首页",
        "missing_on_version": "%(page)s 这个版本没有任何标注",
    }
    en = {
        "invalid_code": "code %(code)s is invalid (%(detail)s)",
        "relative_url": "%(code)s uses a relative URL",
        "duplicate_code": "%(code)s points to two different URLs",
        "no_self_reference": "%(page)s does not reference itself",
        "no_x_default": "%(page)s has no default version",
        "no_return_link": "%(page)s does not link back to the homepage",
        "missing_on_version": "%(page)s has no annotations",
    }
    table = zh if lang == "zh" else en
    vals = dict((k, problem.get(k) or "") for k in ("code", "page", "href", "detail"))
    return table.get(problem.get("kind"), problem.get("kind")) % vals
