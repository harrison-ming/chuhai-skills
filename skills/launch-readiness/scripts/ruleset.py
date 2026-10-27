"""Check catalogue (references/rules.json) and the context the checks write to.

Results per rule:
    fail     a gap was found (goes to the fix list)
    manual   needs a human / admin / browser check (goes to the fix list)
    pass     checked, nothing found
    unknown  could not be checked ([查不到]; listed under "not verified")
    skip     not applicable (market not targeted, no ads planned, ...)
    same     duplicate of another rule (``same_as``), appendix only
    info     hint for the appendix only
Python 3.8+, standard library only.
"""

import json
import os
import re

import markets

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(os.path.dirname(HERE), "references", "rules.json")
LEVELS = ("P0", "P1", "P2", "P3")
IN_FIXLIST = ("fail", "manual")


def load_rules(path=RULES_PATH):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rules = dict((r["id"], r) for r in data["rules"])
    return data, rules


def p0_allowed(rule, sources):
    """P0 needs verified sources and must not rest on an experience item."""
    if rule.get("p0_ok"):
        return True
    if rule.get("exp") or not rule.get("src"):
        return False
    return all((sources.get(s) or {}).get("verified") for s in rule["src"])


def demote(level):
    i = LEVELS.index(level)
    return LEVELS[min(i + 1, len(LEVELS) - 1)]


class Ctx(object):
    def __init__(self, site, countries, ads=True, lang_countries=None):
        self.data, self.rules = load_rules()
        self.sources = self.data["sources"]
        self.site = site
        self.countries = list(countries)
        self.groups = markets.groups(self.countries)
        self.lang_countries = list(lang_countries or self.countries)
        self.ads = ads
        self.findings = {}
        self.default_note = "not_evaluated"
        self.order = [r["id"] for r in self.data["rules"]]

    # -- context helpers --------------------------------------------------
    def targets(self, *tokens):
        return any(t in self.groups for t in tokens)

    def multi(self):
        return len(self.lang_countries) > 1 or len(self.site.markets) > 0

    def fact_badge(self):
        return "self_claim" if self.site.paste else "verified"

    def applicable(self, rid):
        rule = self.rules[rid]
        if rule.get("gmc") and not self.ads:
            return False, "no_ads"
        if not markets.applies(rule.get("mk"), self.countries):
            return False, "market_not_targeted"
        if rule.get("multi") and not self.multi():
            return False, "single_market"
        return True, None

    # -- recording --------------------------------------------------------
    def add(
        self,
        rid,
        result,
        where=None,
        evidence=None,
        note=None,
        level=None,
        badge=None,
        data=None,
        same_as=None,
    ):
        rule = self.rules[rid]
        if result in ("fail", "manual", "pass", "unknown"):
            ok, why = self.applicable(rid)
            if not ok:
                result, note = "skip", why
        lv = level or rule["lv"]
        if lv == "P0" and not p0_allowed(rule, self.sources):
            lv = demote(lv)
        if badge is None:
            badge = self._badge(rule, result)
        ev = [re.sub(r"\s+", " ", e).strip()[:300] for e in (evidence or []) if e]
        self.findings[rid] = {
            "id": rid,
            "result": result,
            "level": lv,
            "badge": badge,
            "markets": [m for m in rule.get("mk") or []],
            "where": where,
            "evidence": ev[:3],
            "note": note,
            "data": data,
            "same_as": same_as,
            "sources": list(rule.get("src") or []),
        }
        return self.findings[rid]

    def _badge(self, rule, result):
        if result == "unknown":
            return "not_found"
        if result in ("skip", "same", "info"):
            return None
        want = rule.get("badge") or "verified"
        if want == "verified" and self.site.paste:
            return "self_claim"
        if want == "conflicting" and result != "fail":
            return self.fact_badge()
        if want in ("inferred", "not_found"):
            return "inferred" if result != "pass" else self.fact_badge()
        return want

    def result(self, rid):
        f = self.findings.get(rid)
        return f["result"] if f else None

    def ordered(self):
        out = [self.findings[r] for r in self.order if r in self.findings]
        missing = [r for r in self.order if r not in self.findings]
        for r in missing:
            out.append(
                {
                    "id": r,
                    "result": "unknown",
                    "level": self.rules[r]["lv"],
                    "badge": "not_found",
                    "markets": [],
                    "where": None,
                    "evidence": [],
                    "note": self.default_note,
                    "data": None,
                    "same_as": None,
                    "sources": self.rules[r].get("src", []),
                }
            )
        return out


def first_match(regex, items, flags=re.I):
    """(item, sentence) for the first sentence matching regex in
    items [(label, url, text)], else (None, None)."""
    import pages

    rx = re.compile(regex, flags) if isinstance(regex, str) else regex
    for item in items:
        for sent in pages.sentences(item[2]):
            if rx.search(sent):
                return item, sent
    return None, None
