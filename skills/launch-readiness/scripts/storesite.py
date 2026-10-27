"""Load a raw/ folder (mode A, B or P) into one Site object for the checks.

raw/pages.json (written by audit_collect.py / paste_ingest.py) is the index.
When it is missing (mode B: pages saved by hand) roles are inferred from
file names: homepage.html, policies/<handle>.html, pages/<role>-*.html,
pdp/*.html, markets/<code>/homepage.html, robots.txt, sitemap.xml,
products.json.
Python 3.8+, standard library only.
"""

import json
import os
import re

import crawl
import pages

ROLE_TO_POLICY = {
    "refund": "refund-policy",
    "shipping": "shipping-policy",
    "privacy": "privacy-policy",
    "terms": "terms-of-service",
    "legal": "legal-notice",
}


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def infer_index(raw):
    """pages.json rows rebuilt from file names (mode B)."""
    rows = []
    for base, _dirs, names in os.walk(raw):
        for n in sorted(names):
            rel = os.path.relpath(os.path.join(base, n), raw).replace(os.sep, "/")
            if rel.startswith("_") or rel == "pages.json":
                continue
            row = {
                "key": rel,
                "file": rel,
                "url": None,
                "result": "ok",
                "source": "manual",
            }
            parts = rel.split("/")
            if rel == "homepage.html":
                row.update(role="home", key="homepage")
            elif parts[0] == "policies":
                h = re.sub(r"(\.page)?\.(html?|txt|md)$", "", parts[-1])
                row.update(role="policy", policy=h, key="policy:" + h)
            elif parts[0] == "pdp":
                row.update(role="pdp")
            elif parts[0] == "markets" and len(parts) == 3:
                kind = {"homepage.html": "market_home", "product.js": "market_product"}
                row.update(role=kind.get(parts[2], "market_policy"), code=parts[1])
            elif rel in ("robots.txt", "sitemap.xml", "products.json", "meta.json"):
                row.update(role=rel.split(".")[0])
            else:
                role = crawl.role_of(rel, "")
                row.update(role="page", page_role=role or "other")
            rows.append(row)
    return rows


class Site(object):
    def __init__(self, raw):
        self.raw = raw
        self.status = load_json(os.path.join(raw, "_status.json"), {}) or {}
        index = load_json(os.path.join(raw, "pages.json"))
        if index is None:
            index = infer_index(raw)
            self.status.setdefault("mode", "manual")
        self.index = index
        self.mode = self.status.get("mode") or "manual"
        self.paste = self.mode == "paste"
        self.home = self._text_of(self._first("home"))
        self.home_url = (self._first("home") or {}).get("final_url") or self.status.get(
            "base_url"
        )
        self.platform = self.status.get("platform") or (
            "shopify"
            if pages.is_shopify(self.home)
            else ("other" if self.home else "unknown")
        )
        self.password = bool(self.status.get("password_protected"))
        self.policies = {}
        self.info = {}
        self.pdps = []
        self.markets = []
        for row in index:
            self._add(row)
        self.products = (load_json(os.path.join(raw, "products.json"), {}) or {}).get(
            "products"
        ) or []
        self.meta = load_json(os.path.join(raw, "meta.json"), {}) or {}
        self.robots = self._text_of(self._first("robots"))
        self.sitemap = self._text_of(self._first("sitemap"))
        self.footer = pages.footer_html(self.home)
        self.header = pages.header_html(self.home)

    # -- loading ---------------------------------------------------------
    def _first(self, role):
        for row in self.index:
            if row.get("role") == role:
                return row
        return None

    def _text_of(self, row):
        if not row or not row.get("file"):
            return None
        return pages.read_text(os.path.join(self.raw, row["file"]))

    def _add(self, row):
        role = row.get("role")
        src = self._text_of(row)
        url = row.get("final_url") or row.get("url")
        entry = {
            "url": url,
            "result": row.get("result"),
            "file": row.get("file"),
            "html": src,
            "text": pages.policy_text(src) if src else "",
        }
        if src and len(entry["text"]) < 200 and pages.js_rendered(src):
            # Content drawn by JavaScript: unreadable, not "missing".
            entry.update(text="", result="js_rendered")
        if role == "page" and row.get("page_role") in ROLE_TO_POLICY and src:
            handle = ROLE_TO_POLICY[row["page_role"]]
            if handle not in self.policies or not self.policies[handle]["text"]:
                self.policies[handle] = entry
            return
        if role == "policy":
            handle = row.get("policy")
            old = self.policies.get(handle)
            if old is None or len(entry["text"]) > len(old["text"] or ""):
                self.policies[handle] = entry
        elif role == "page":
            self.info.setdefault(row.get("page_role") or "other", []).append(entry)
        elif role == "pdp":
            entry.update(handle=row.get("handle"), sample=row.get("sample"))
            if entry["result"] != "js_rendered":
                self.pdps.append(entry)
        elif role in ("market_home", "market_product", "market_policy"):
            m = self.market(row.get("code"))
            m[role] = entry
            m["code"] = row.get("code")

    def market(self, code):
        for m in self.markets:
            if m.get("code") == code:
                return m
        m = {"code": code}
        self.markets.append(m)
        return m

    # -- helpers for the checks -----------------------------------------
    def online(self):
        """True when live pages were read (not pasted text)."""
        return not self.paste and bool(self.home)

    def policy(self, handle):
        p = self.policies.get(handle)
        return p if p and p.get("text") else None

    def policy_result(self, handle):
        p = self.policies.get(handle)
        return p.get("result") if p else None

    def info_pages(self, role):
        return [p for p in self.info.get(role, []) if p.get("text")]

    def nav_links(self):
        return pages.links(self.header + "\n" + self.footer, self.home_url)

    def footer_links(self):
        return pages.links(self.footer, self.home_url)

    def footer_has(self, regex):
        rx = re.compile(regex, re.I)
        # Footer first; any homepage link still makes the page reachable.
        for links in (self.footer_links(), pages.links(self.home, self.home_url)):
            for url, text, _a in links:
                if rx.search(text or "") or rx.search(url or ""):
                    return url
        return None

    def texts(self):
        """[(source label, url, text)] of everything readable, for claims."""
        out = []
        for h, p in sorted(self.policies.items()):
            if p.get("text"):
                out.append(("policy:" + h, p["url"], p["text"]))
        for role, items in sorted(self.info.items()):
            for p in items:
                if p.get("text"):
                    out.append((role, p["url"], p["text"]))
        for p in self.pdps:
            if p.get("text"):
                out.append(("pdp", p["url"], p["text"]))
        if self.home:
            ann = pages.announcement_text(self.home)
            if ann:
                out.append(("announcement", self.home_url, ann))
            out.append(("footer", self.home_url, pages.html_to_text(self.footer)))
            out.append(("home", self.home_url, pages.page_text(self.home)))
        return out
