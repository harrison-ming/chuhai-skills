#!/usr/bin/env python3
"""Step 2 (offline): turn ``<raw-data>/raw/`` into the audit.

CLI:
    python audit.py "<run folder or raw-data folder>" [--lang zh|en]
                    [--mode own|other]

Works the same on files saved by collect.py (mode A) and on files saved by
hand (mode B). Reads ``user-inputs/answers.json`` when present (the agent
fills it from the user's screenshots / answers, see references/user-inputs.md).

Writes into the raw-data folder:
    summary.json        schema tracking-audit/audit/1 (fields: references/method.md)
    evidence.jsonl      one line per evidence item
    report.draft.md     a complete first draft of the report (the agent edits it)
    user-inputs/请你提供.md (en: user-inputs/what-we-need.md) and
    user-inputs/answers.template.json   (own-store mode only)
Python 3.8+, standard library only.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog as cat  # noqa: E402
import detect  # noqa: E402
import draft  # noqa: E402
import skill_config as cfg  # noqa: E402
from _shared import datafile, evidence, fetch, i18n, output  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = "tracking-audit/audit/1"
PAGE_ORDER = ("homepage", "collection", "product", "cart", "privacy")
HIGH_WHEN_ADVERTISED = {"T01", "T04", "T05", "T06"}


def read_text(path):
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def badge(key, lang):
    return evidence.badge(key, lang) if key else ""


class Audit(object):
    def __init__(self, data_dir, lang="zh", mode="own", sigs=None):
        self.data_dir = data_dir
        self.raw = os.path.join(data_dir, "raw")
        self.lang = i18n.norm_lang(lang)
        self.mode = mode
        self.sigs = sigs or detect.load_signatures()
        self.status = load_json(os.path.join(self.raw, "_status.json"), {}) or {}
        self.answers = (
            load_json(os.path.join(data_dir, "user-inputs", "answers.json"), {}) or {}
        )
        self.warnings = []
        self.rows = []
        self.checked_at = self.status.get("finished_at_utc") or fetch.utc_now()

    # ------------------------------------------------------------ helpers
    def t(self, pair):
        return cat.pick(pair, self.lang)

    def add(
        self,
        cid,
        result,
        badge_key,
        severity="none",
        platform="all",
        basis="",
        evidence_files=(),
        needs=None,
        source="H",
        item=None,
        fix=None,
    ):
        zh, en, fzh, fen = cat.CHECKS[cid]
        if result in ("pass", "na"):
            fix_text = "-"
        else:
            fix_text = fix or (fzh if self.lang == "zh" else fen)
        row = {
            "id": cid,
            "platform": platform,
            "platform_label": self.t(cat.PLATFORM.get(platform, (platform, platform))),
            "item": item or (zh if self.lang == "zh" else en),
            "result": result,
            "result_label": self.t(cat.RESULT[result]),
            "badge_key": badge_key,
            "badge": badge(badge_key, self.lang),
            "severity": severity,
            "severity_label": self.t(cat.SEVERITY[severity]),
            "basis": basis,
            "evidence": "; ".join(sorted(set(e for e in evidence_files if e))),
            "fix": fix_text,
            "needs_user": needs or "",
            "needs_user_label": self.t(cat.USER_INPUTS[needs][:2]) if needs else "",
            "data_source": ", ".join(
                self.t(cat.SOURCE[s]) for s in source.split("+") if s in cat.SOURCE
            ),
            "checked_at": self.checked_at,
        }
        self.rows.append(row)
        return row

    def basis(self, zh, en):
        word = evidence.BASIS_WORD[self.lang]
        return "%s: %s" % (word, zh if self.lang == "zh" else en)

    # ------------------------------------------------------------ inputs
    def load_pages(self):
        pages, endpoints = [], {}
        reqs = self.status.get("requests") or []
        for r in reqs:
            endpoints[r.get("key")] = {
                "result": r.get("result"),
                "http_status": r.get("http_status"),
                "url": r.get("url"),
                "file": r.get("file"),
                "note": r.get("note") or "",
            }
        self.texts = {}
        for key in PAGE_ORDER:
            fname = cfg.PAGE_FILES[key]
            text = read_text(os.path.join(self.raw, fname))
            if text is not None:
                self.texts[key] = text
                endpoints.setdefault(
                    key,
                    {
                        "result": "ok",
                        "http_status": None,
                        "url": None,
                        "file": fname,
                        "note": "saved by hand",
                    },
                )
            elif key not in endpoints:
                endpoints[key] = {
                    "result": "not_found",
                    "http_status": None,
                    "url": None,
                    "file": None,
                    "note": "no file",
                }
        self.containers = {}
        if os.path.isdir(self.raw):
            for fname in sorted(os.listdir(self.raw)):
                m = re.match(
                    r"^container-((?:GTM|G|AW|GT|DC)-[A-Za-z0-9]+)\.js$", fname
                )
                if m:
                    self.containers[m.group(1)] = read_text(
                        os.path.join(self.raw, fname)
                    )
        for key in PAGE_ORDER:
            ep = endpoints.get(key) or {}
            pages.append(
                {
                    "key": key,
                    "result": ep.get("result"),
                    "url": ep.get("url"),
                    "http_status": ep.get("http_status"),
                    "file": ("raw/" + cfg.PAGE_FILES[key])
                    if key in self.texts
                    else None,
                    "note": ep.get("note", ""),
                }
            )
        self.pages = pages
        self.endpoints = endpoints

    # ------------------------------------------------------------ detection
    def detect_all(self):
        hits, inventory, wp_info = [], [], {}
        for key in PAGE_ORDER:
            text = self.texts.get(key)
            if not text:
                continue
            h, inv, info = detect.scan_page(
                text, self.sigs, "raw/" + cfg.PAGE_FILES[key]
            )
            hits += h
            if inv and not inventory:
                inventory = inv
            if info.get("web_pixels") != "not_observed" and not wp_info:
                wp_info = info
        container_rows = []
        for cid, text in sorted(self.containers.items()):
            via = "gtm_container" if cid.startswith("GTM-") else "google_tag_script"
            h = detect.scan_container(
                text, self.sigs, "raw/container-%s.js" % cid, via, cid
            )
            hits += h
            container_rows.append(
                {
                    "id": cid,
                    "ids": sorted(set(x["id"] for x in h if x["id"])),
                    "vendors": sorted(set(x["platform"] for x in h if not x["id"])),
                }
            )
        # ua residue (pages + containers)
        ua = []
        for key in PAGE_ORDER:
            for u in detect.legacy_ua(self.texts.get(key), self.sigs):
                ua.append((u, "raw/" + cfg.PAGE_FILES[key]))
        for cid, text in self.containers.items():
            for u in re.findall(
                (self.sigs.get("container_signals") or {})
                .get("ua_id", {})
                .get("pattern", "$^"),
                text or "",
            ):
                ua.append((u, "raw/container-%s.js" % cid))
        self.hits = hits
        self.inventory = inventory
        self.web_pixels = wp_info or {
            "web_pixels": "not_observed",
            "web_pixels_count": 0,
        }
        self.container_rows = container_rows
        self.ua = ua
        # gtm ids referenced (even if container not downloaded)
        for key in PAGE_ORDER:
            g1, _g2 = detect.container_ids(self.texts.get(key) or "")
            for g in g1:
                if not any(h["platform"] == "gtm" and h["id"] == g for h in hits):
                    hits.append(
                        detect.make_hit(
                            "gtm",
                            g,
                            "theme_code",
                            "container_reference",
                            True,
                            "raw/" + cfg.PAGE_FILES[key],
                            self.sigs,
                        )
                    )

    def tags(self):
        """Merged tag list: one entry per platform + id."""
        out = {}
        for h in self.hits:
            if h["problem"]:
                continue
            key = (h["platform"], h["id"])
            t = out.setdefault(
                key,
                {
                    "platform": h["platform"],
                    "id": h["id"],
                    "install_via": [],
                    "sources": [],
                    "calibrated": False,
                },
            )
            if h["install_via"] not in t["install_via"]:
                t["install_via"].append(h["install_via"])
            if h["source"] not in t["sources"]:
                t["sources"].append(h["source"])
            t["calibrated"] = t["calibrated"] or h["calibrated"]
        res = list(out.values())
        for t in res:
            t["badge_key"] = "on_page" if t["calibrated"] else "inferred"
        order = {p: i for i, p in enumerate(detect.PLATFORMS)}
        res.sort(key=lambda t: (order.get(t["platform"], 99), t["id"] or ""))
        return res

    # ------------------------------------------------------------ checks
    def platform_tags(self, platform):
        return [t for t in self.tag_list if t["platform"] == platform]

    def user_item(self, uid):
        items = self.answers.get("items") or {}
        it = items.get(uid)
        return it if isinstance(it, dict) else None

    def from_user(self, uid, platform_ids=None):
        """(result, badge_key, basis) from an answered user input, else None."""
        it = self.user_item(uid)
        if not it:
            return None
        src = it.get("source")
        ok = it.get("ok")
        result = "pass" if ok is True else "issue" if ok is False else "needs_input"
        if src == "screenshot":
            key = "verified"
            label = it.get("file") or uid
            basis = self.basis(
                "你提供的截图 %s (%s)" % (label, it.get("date") or "日期未写"),
                "your screenshot %s (%s)" % (label, it.get("date") or "no date"),
            )
            if platform_ids:
                seen_ids = set()
                for p, ids in (it.get("ids") or {}).items():
                    seen_ids |= set(str(i) for i in ids or [])
                page_ids = set(str(i) for i in platform_ids if i)
                if seen_ids and page_ids and not (seen_ids & page_ids):
                    key = "conflicting"
                    basis = self.basis(
                        "截图里的编号与页面里的不一致",
                        "IDs in the screenshot differ from the page",
                    )
        elif src == "said":
            key = "self_claim"
            basis = self.basis("你的口头说明", "your own statement")
        else:
            return None
        if it.get("note"):
            basis += " (%s)" % str(it["note"])[:160]
        return result, key, basis

    def ad_platforms(self):
        ap = self.answers.get("ad_platforms")
        if isinstance(ap, list):
            return [str(p) for p in ap]
        return None

    def check_identity(self):
        home = self.texts.get("homepage")
        markers = detect.shopify_markers(home) if home else []
        other = detect.other_platform(home) if home else None
        if home is None or detect.looks_blocked(home):
            ident = "unknown"
        elif len(markers) >= 2:
            ident = "confirmed"
        elif len(markers) == 1:
            ident = "probable"
        else:
            ident = "not_shopify"
        self.identity = {"status": ident, "markers": markers, "other_platform": other}
        self.is_shopify = ident in ("confirmed", "probable")
        if ident == "unknown":
            self.add(
                "G01",
                "needs_input",
                "not_found",
                basis=self.basis("首页没拿到", "homepage not fetched"),
            )
            return
        if self.is_shopify:
            label = ("Shopify 店铺", "Shopify store")
            key = "on_page" if ident == "confirmed" else "inferred"
            b = (
                self.basis(
                    "页面里有 %d 个 Shopify 特征" % len(markers),
                    "%d Shopify markers in the page" % len(markers),
                )
                if key == "inferred"
                else ""
            )
        else:
            name = other or self.t(("未识别的平台", "unidentified platform"))
            label = ("不是 Shopify (%s)" % name, "Not Shopify (%s)" % name)
            key = "inferred"
            b = self.basis("页面里没有 Shopify 特征", "no Shopify markers in the page")
        self.add(
            "G01",
            "pass",
            key,
            item=self.t(label),
            basis=b,
            evidence_files=["raw/homepage.html"],
        )

    def check_blocked(self):
        blocked = [
            k
            for k, v in self.endpoints.items()
            if v.get("result") == "blocked" and not str(k).startswith("container")
        ]
        home = self.texts.get("homepage")
        challenge = bool(home and detect.looks_blocked(home))
        self.blocked = bool(blocked) or challenge
        if self.blocked:
            what = ", ".join(blocked) or "homepage"
            self.warnings.append("blocked:%s" % what)
            self.add(
                "G02",
                "issue",
                "not_found",
                severity="medium",
                basis=self.basis("被拦的页面: %s" % what, "blocked pages: %s" % what),
                evidence_files=["raw/_status.json"],
            )
        else:
            self.add("G02", "pass", "on_page", evidence_files=["raw/_status.json"])

    def check_platforms(self):
        ads = self.ad_platforms()
        for cid, plat in sorted(cat.T_PLATFORM.items()):
            tags = self.platform_tags(plat)
            if plat in ("ga4", "google_ads") and not tags:
                gt = self.platform_tags("google_tag")
                if gt:
                    b = self.basis(
                        "只看到谷歌代码 (以 GT- 开头), 看不出里面是分析还是广告",
                        "only a Google tag (GT-) seen; cannot tell which product",
                    )
                    self.add(
                        cid,
                        "pass",
                        "inferred",
                        platform=plat,
                        basis=b,
                        evidence_files=sum((t["sources"] for t in gt), []),
                    )
                    continue
            if tags:
                key = (
                    evidence.weakest([t["badge_key"] for t in tags])
                    if not any(t["calibrated"] for t in tags)
                    else "on_page"
                )
                vias = []
                for t in tags:
                    for v in t["install_via"]:
                        if v not in vias:
                            vias.append(v)
                where = ", ".join(self.t(cat.VIA[v]) for v in vias)
                b = (
                    ""
                    if key == "on_page"
                    else self.basis(
                        "识别规则还没在足够多的真实店铺上校准, 位置: %s" % where,
                        "signature not yet calibrated on enough stores; found in: %s"
                        % where,
                    )
                )
                note = self.t(("位置: %s", "where: %s")) % where
                row = self.add(
                    cid,
                    "pass",
                    key,
                    platform=plat,
                    basis=b or note,
                    evidence_files=sum((t["sources"] for t in tags), []),
                    source="H+G",
                )
                up = self.platform_screenshot(plat, [t["id"] for t in tags])
                if up:
                    row.update(
                        badge_key=up[0], badge=badge(up[0], self.lang), basis=up[1]
                    )
                continue
            advertised = ads is not None and plat in ads
            if ads is None:
                sev = "low" if cid in HIGH_WHEN_ADVERTISED else "none"
                b = self.basis(
                    "本次检查的页面里没看到; 如果你在投这个平台, 这一项就是高",
                    "not seen on the pages checked; if you advertise there, this is high",
                )
            elif advertised:
                sev = "high" if cid in HIGH_WHEN_ADVERTISED else "medium"
                b = self.basis(
                    "你说在投这个平台, 但本次检查的页面里没看到",
                    "you said you advertise there, but it was not seen",
                )
            else:
                sev = "none"
                b = self.basis(
                    "本次检查的页面里没看到, 你也说没在投",
                    "not seen, and you said you do not use it",
                )
            if (
                plat == "klaviyo"
                and ads is not None
                and self.answers.get("uses_klaviyo")
            ):
                sev = "medium"
            self.add(
                cid,
                "not_seen",
                "not_found",
                severity=sev,
                platform=plat,
                basis=b,
                source="H+G",
            )

    def platform_screenshot(self, plat, ids):
        """Upgrade from a screenshot that shows this platform's IDs."""
        for uid in sorted((self.answers.get("items") or {})):
            it = self.user_item(uid)
            if not it or it.get("source") != "screenshot":
                continue
            shot_ids = (it.get("ids") or {}).get(plat)
            if not shot_ids:
                continue
            res = self.from_user(uid, ids)
            if res:
                return res[1], res[2]
        return None

    def check_gtm(self):
        gtm = self.platform_tags("gtm")
        if not gtm:
            self.add(
                "T03",
                "not_seen",
                "not_found",
                platform="gtm",
                basis=self.basis(
                    "本次检查的页面里没看到", "not seen on the pages checked"
                ),
            )
            return
        inside = []
        for c in self.container_rows:
            if c["id"].startswith("GTM-") and c["ids"]:
                inside.append("%s: %s" % (c["id"], ", ".join(c["ids"][:6])))
        n = len([t for t in gtm if t["id"]])
        sev = "low" if n > 1 else "none"
        res = "issue" if n > 1 else "pass"
        b = self.t(("%d 个容器", "%d container(s)")) % n
        if inside:
            b += (
                "; "
                + self.t(("容器里配置了: ", "configured inside: "))
                + " | ".join(inside)
            )
        self.add(
            "T03",
            res,
            "on_page",
            severity=sev,
            platform="gtm",
            basis=b,
            evidence_files=sum((t["sources"] for t in gtm), []),
            source="H+G",
        )

    def check_shopify_pixels(self):
        if not self.is_shopify:
            self.add("T09", "na", None, platform="shopify")
            return
        inv = [i for i in self.inventory if i.get("platform") != "shopify"]
        if self.web_pixels.get("web_pixels") == "not_observed":
            self.add(
                "T09",
                "not_seen",
                "not_found",
                platform="shopify",
                needs="U1-a",
                basis=self.basis(
                    "页面里没看到像素登记表", "pixel list not seen in the page"
                ),
            )
            return
        apps = [i for i in inv if i["type"] == "APP"]
        customs = [i for i in inv if i["type"] == "CUSTOM"]
        b = self.t(
            ("应用像素 %d 个, 自定义像素 %d 个", "%d app pixels, %d custom pixels")
        ) % (len(apps), len(customs))
        row = self.add(
            "T09",
            "pass",
            "on_page",
            platform="shopify",
            basis=b,
            evidence_files=["raw/homepage.html"],
        )
        up = self.from_user("U1-a")
        if up:
            row.update(
                badge_key=up[1], badge=badge(up[1], self.lang), basis=b + "; " + up[2]
            )

    def check_placeholders(self):
        bad = [h for h in self.hits if h["problem"]]
        if not bad:
            self.add(
                "T10",
                "pass",
                "on_page",
                basis=self.t(
                    (
                        "没发现模板文字或格式不对的编号",
                        "no placeholder or malformed IDs found",
                    )
                ),
            )
            return
        for h in bad:
            what = (
                self.t(("模板文字", "placeholder text"))
                if h["problem"] == "placeholder"
                else self.t(("格式不对", "wrong format"))
            )
            in_container = h["install_via"] in ("gtm_container", "google_tag_script")
            where = (
                (
                    "代码管理器容器里 (可能是没启用的标签)",
                    "inside a Tag Manager container (may be a paused tag)",
                )
                if in_container
                else ("页面源码", "the page source")
            )
            self.add(
                "T10",
                "issue",
                "verified",
                severity="medium" if in_container else "high",
                platform=h["platform"],
                basis=self.basis(
                    "%s原文里的编号是%s (见附录)" % (where[0], what),
                    "the ID in %s source text is %s (see appendix)" % (where[1], what),
                ),
                evidence_files=[h["source"]],
            )

    def check_ad_platform_gap(self):
        ads = self.ad_platforms()
        if ads is None:
            if self.mode == "own":
                self.add(
                    "T11",
                    "needs_input",
                    "not_found",
                    basis=self.basis(
                        "还不知道你在哪些平台投广告",
                        "we do not know where you advertise yet",
                    ),
                    source="U1",
                )
            return
        seen = set(t["platform"] for t in self.tag_list)
        if "google_tag" in seen:
            seen |= {"google_ads", "ga4"}
        missing = [p for p in ads if p in detect.AD_PLATFORMS and p not in seen]
        if missing:
            names = ", ".join(
                self.t(cat.PLATFORM[p]) for p in missing if p in cat.PLATFORM
            )
            self.add(
                "T11",
                "issue",
                "inferred",
                severity="high",
                basis=self.basis(
                    "你说在投 %s, 本次检查的页面里没看到它们的代码" % names,
                    "you advertise on %s but their code was not seen" % names,
                ),
                source="H+U1",
            )
        else:
            self.add(
                "T11",
                "pass",
                "inferred",
                basis=self.basis(
                    "你说在投的平台在页面里都看得到",
                    "every platform you named was seen",
                ),
                source="H+U1",
            )

    def check_duplicates(self):
        dup_rows = 0
        groups = {}
        for h in self.hits:
            if h["problem"] or not h["id"] or h["platform"] in ("gtm",):
                continue
            groups.setdefault((h["platform"], h["id"]), []).append(h)
        for (plat, ident), hs in sorted(groups.items()):
            vias = sorted(set(h["install_via"] for h in hs))
            if plat == "klaviyo":
                vias = [
                    v for v in vias if v != "app_pixel"
                ]  # app pixel + onsite script is the normal setup
            repeated = [
                h
                for h in hs
                if h.get("count", 1) > 1 and h["install_via"] == "theme_code"
            ]
            if len(vias) >= 2 or repeated:
                where = ", ".join(self.t(cat.VIA[v]) for v in vias)
                if repeated and len(vias) < 2:
                    where = (
                        self.t(("同一页面里装了 %d 次", "loaded %d times in one page"))
                        % repeated[0]["count"]
                    )
                self.add(
                    "D01",
                    "issue",
                    "inferred",
                    severity="high",
                    platform=plat,
                    basis=self.basis(
                        "同一个编号出现在: %s, 两处都会发数据" % where,
                        "the same ID appears in: %s; both send data" % where,
                    ),
                    evidence_files=[h["source"] for h in hs],
                    source="H+G",
                )
                dup_rows += 1
        if not dup_rows:
            self.add(
                "D01",
                "pass",
                "on_page",
                basis=self.t(
                    (
                        "本次检查的页面里没看到同一编号装两处",
                        "no ID seen installed twice",
                    )
                ),
                source="H+G",
            )
        multi = 0
        for plat in ("meta", "ga4", "google_ads", "tiktok", "pinterest", "snap", "gtm"):
            ids = sorted(set(t["id"] for t in self.platform_tags(plat) if t["id"]))
            if len(ids) >= 2:
                multi += 1
                tags = self.platform_tags(plat)
                key = "on_page" if all(t["calibrated"] for t in tags) else "inferred"
                b = self.t(
                    ("%d 个不同编号 (见附录)", "%d different IDs (see appendix)")
                ) % len(ids)
                if key == "inferred":
                    b = self.basis(b, b)
                self.add(
                    "D02",
                    "issue",
                    key,
                    severity="medium",
                    platform=plat,
                    basis=b,
                    evidence_files=sum((t["sources"] for t in tags), []),
                    source="H+G",
                )
        if not multi:
            self.add("D02", "pass", "on_page", source="H+G")
        both = []
        for t in self.platform_tags("ga4"):
            vias = set(t["install_via"])
            if "gtm_container" in vias and vias & {
                "theme_code",
                "app_pixel",
                "google_tag_script",
            }:
                both.append(t)
        if both:
            self.add(
                "D03",
                "issue",
                "inferred",
                severity="medium",
                platform="ga4",
                basis=self.basis(
                    "同一个分析编号既在代码管理器容器里, 也由页面代码或应用像素加载",
                    "the same GA4 ID is in a Tag Manager container and also loaded by page code or an app pixel",
                ),
                evidence_files=sum((t["sources"] for t in both), []),
                source="H+G",
            )
        else:
            self.add("D03", "pass", "on_page", platform="ga4", source="H+G")
        if self.ua:
            self.add(
                "D04",
                "issue",
                "verified",
                severity="low",
                platform="ga4",
                basis=self.basis(
                    "页面源码原文里有旧版编号 (见附录)",
                    "old UA ID in the source text (see appendix)",
                ),
                evidence_files=[s for _u, s in self.ua],
                source="H+G",
            )
        else:
            self.add("D04", "pass", "on_page", platform="ga4", source="H+G")
        if self.is_shopify and self.mode == "own":
            up = self.from_user("U1-g")
            if up:
                self.add(
                    "D05",
                    up[0],
                    up[1],
                    severity="high" if up[0] == "issue" else "none",
                    basis=up[2],
                    needs="U1-g",
                    source="U1",
                )
            else:
                self.add(
                    "D05",
                    "needs_input",
                    "not_found",
                    severity="high",
                    needs="U1-g",
                    basis=self.basis(
                        "从前台看不到结账设置",
                        "checkout settings are not visible from outside",
                    ),
                    source="U1",
                )
        if self.is_shopify:
            apps = set(
                i["platform"]
                for i in self.inventory
                if i["type"] == "APP" and i.get("platform")
            )
            customs = [
                i
                for i in self.inventory
                if i["type"] == "CUSTOM" and i.get("platform") not in (None, "shopify")
            ]
            overlap = [
                i
                for i in customs
                if i["platform"] in apps
                or (i["platform"] == "google" and apps & {"google"})
            ]
            up = self.from_user("U1-a") if self.mode == "own" else None
            if up and up[0] != "needs_input":
                self.add(
                    "D06",
                    up[0],
                    up[1],
                    severity="high" if up[0] == "issue" else "none",
                    basis=up[2],
                    needs="U1-a",
                    source="U1",
                )
            elif overlap:
                names = ", ".join("'%s'" % i["name"] for i in overlap)
                self.add(
                    "D06",
                    "issue",
                    "inferred",
                    severity="high",
                    basis=self.basis(
                        "自定义像素 %s 的名字和已装的应用像素是同一平台, 代码本身看不到"
                        % names,
                        "custom pixel %s is named after a platform that also has an app pixel; its code is not visible"
                        % names,
                    ),
                    needs="U1-a" if self.mode == "own" else None,
                    evidence_files=["raw/homepage.html"],
                )
            elif self.mode == "own":
                self.add(
                    "D06",
                    "needs_input",
                    "not_found",
                    needs="U1-a",
                    basis=self.basis(
                        "自定义像素的代码从前台看不到",
                        "custom pixel code is not visible from outside",
                    ),
                    source="U1",
                )

    def check_checkout_server(self):
        seen = set(t["platform"] for t in self.tag_list)
        ads = set(self.ad_platforms() or [])
        relevant = lambda *ps: any(p in seen or p in ads for p in ps)  # noqa: E731
        other = self.mode != "own"
        not_visible = self.basis(
            "从外面确认不了, 只能看后台",
            "cannot be confirmed from outside; needs the ad admin",
        )
        for plat, uid in sorted(cat.C01_INPUT.items()):
            alias = (plat, "google_ads", "google_tag") if plat == "ga4" else (plat,)
            if not relevant(*alias):
                continue
            item = self.t(("成交事件是否到达 %s", "Purchase events reach %s")) % self.t(
                cat.PLATFORM[plat]
            )
            up = (
                None
                if other
                else self.from_user(uid, [t["id"] for t in self.platform_tags(plat)])
            )
            if up:
                self.add(
                    "C01",
                    up[0],
                    up[1],
                    severity="high" if up[0] != "pass" else "none",
                    platform=plat,
                    basis=up[2],
                    needs=uid,
                    source="U1",
                    item=item,
                )
            else:
                self.add(
                    "C01",
                    "needs_input",
                    "not_found",
                    severity="high",
                    platform=plat,
                    basis=not_visible,
                    needs=None if other else uid,
                    source="U1",
                    item=item,
                )
        server = [
            ("C02", ("meta", "ga4", "google_ads", "tiktok"), "medium", "all"),
            ("S01", ("meta",), "high", "meta"),
            ("S02", ("meta",), "high", "meta"),
            ("S03", ("tiktok",), "high", "tiktok"),
            ("S04", ("google_ads", "google_tag"), "medium", "google_ads"),
            ("S05", ("pinterest", "snap"), "medium", "all"),
        ]
        for cid, plats, sev, plat in server:
            if not relevant(*plats):
                continue
            uid = cat.CHECK_INPUT[cid]
            up = None if other else self.from_user(uid)
            if up:
                self.add(
                    cid,
                    up[0],
                    up[1],
                    severity=sev if up[0] != "pass" else "none",
                    platform=plat,
                    basis=up[2],
                    needs=uid,
                    source="U1",
                )
            else:
                self.add(
                    cid,
                    "needs_input",
                    "not_found",
                    severity=sev,
                    platform=plat,
                    basis=not_visible,
                    needs=None if other else uid,
                    source="U1",
                )

    def check_privacy(self):
        home = self.texts.get("homepage") or ""
        self.markets = detect.market_signals(home)
        self.regions = detect.regions(self.markets)
        texts = [self.texts.get(k) for k in PAGE_ORDER] + list(self.containers.values())
        self.consent = detect.consent_signals(texts, self.sigs)
        priv = self.texts.get("privacy")
        self.privacy = detect.privacy_mentions(priv) if priv else None
        reg_basis = self.basis(
            "页面的语言/地区设置和币种", "page language / region settings and currency"
        )
        if "eu_uk" in self.regions:
            if self.consent["cmp"] or self.consent["consent_default"]:
                what = ", ".join(self.consent["cmp"]) or self.t(
                    ("谷歌同意模式默认设置", "Google consent default")
                )
                self.add(
                    "P01",
                    "pass",
                    "inferred",
                    severity="info",
                    basis=self.basis(
                        "看起来面向欧盟/英国; 页面里有同意工具的痕迹: %s" % what,
                        "seems to serve EU / UK; consent tool traces: %s" % what,
                    ),
                    evidence_files=["raw/homepage.html"],
                )
            else:
                self.add(
                    "P01",
                    "issue",
                    "not_found",
                    severity="info",
                    basis=self.basis(
                        "看起来面向欧盟/英国, 但本次检查的页面里没看到同意横幅的痕迹",
                        "seems to serve EU / UK, but no consent banner trace was seen",
                    ),
                    needs="U1-h" if self.mode == "own" and self.is_shopify else None,
                    evidence_files=["raw/homepage.html"],
                )
        else:
            self.add("P01", "na", None, basis=reg_basis)
        google_seen = any(
            t["platform"] in ("ga4", "google_ads", "google_tag", "gtm")
            for t in self.tag_list
        )
        if self.consent["consent_default"]:
            if self.consent["consent_v2"]:
                self.add(
                    "P02",
                    "pass",
                    "on_page",
                    severity="info",
                    basis=self.t(
                        (
                            "页面代码里设置了同意默认值, 含第 2 版参数",
                            "consent defaults set, including v2 parameters",
                        )
                    ),
                )
            else:
                self.add(
                    "P02",
                    "issue",
                    "on_page",
                    severity="info",
                    basis=self.t(
                        (
                            "设置了同意默认值, 但没看到第 2 版参数",
                            "consent defaults set without v2 parameters",
                        )
                    ),
                )
        elif google_seen:
            self.add(
                "P02",
                "not_seen",
                "not_found",
                severity="info",
                basis=self.basis(
                    "页面代码里没看到同意默认值 (Shopify 应用可能在沙箱里设置, 从外面看不到)",
                    "no consent default seen (Shopify apps may set it inside the sandbox)",
                ),
            )
        else:
            self.add("P02", "na", None)
        if "us" in self.regions:
            if self.consent["us_opt_out"]:
                self.add(
                    "P04",
                    "pass",
                    "on_page",
                    severity="info",
                    basis=self.t(
                        (
                            "页面里有 '你的隐私选择' 或 '不要出售' 类链接",
                            "a 'Your Privacy Choices' / 'Do not sell' link is on the page",
                        )
                    ),
                )
            else:
                self.add(
                    "P04",
                    "issue",
                    "not_found",
                    severity="info",
                    basis=self.basis(
                        "看起来面向美国, 但本次检查的页面里没看到退出链接",
                        "seems to serve the US, but no opt-out link was seen",
                    ),
                )
        else:
            self.add("P04", "na", None, basis=reg_basis)
        if "ca" in self.regions:
            if not self.privacy:
                self.add(
                    "P05",
                    "needs_input",
                    "not_found",
                    severity="info",
                    basis=self.basis("隐私政策页没拿到", "privacy policy not fetched"),
                )
            elif self.privacy["advertising"] and self.privacy["opt_out"]:
                self.add(
                    "P05",
                    "pass",
                    "on_page",
                    severity="info",
                    basis=self.t(
                        (
                            "隐私政策原文提到广告用途和退出方式",
                            "the policy text mentions advertising and opt-out",
                        )
                    ),
                    evidence_files=["raw/privacy.html"],
                )
            else:
                self.add(
                    "P05",
                    "issue",
                    "on_page",
                    severity="info",
                    basis=self.t(
                        (
                            "隐私政策原文没同时提到广告用途和退出方式",
                            "the policy text does not mention both advertising and opt-out",
                        )
                    ),
                    evidence_files=["raw/privacy.html"],
                )
        else:
            self.add("P05", "na", None, basis=reg_basis)
        used = [
            p
            for p in (
                "meta",
                "ga4",
                "google_ads",
                "tiktok",
                "pinterest",
                "snap",
                "klaviyo",
                "microsoft",
                "reddit",
            )
            if self.platform_tags(p)
        ]
        if not self.privacy:
            self.add(
                "P06",
                "not_seen",
                "not_found",
                severity="info",
                basis=self.basis("隐私政策页没拿到", "privacy policy not fetched"),
            )
        elif not used:
            self.add("P06", "na", None)
        else:
            missing = [p for p in used if p not in self.privacy["platforms"]]
            if missing:
                names = ", ".join(self.t(cat.PLATFORM[p]) for p in missing)
                self.add(
                    "P06",
                    "issue",
                    "on_page",
                    severity="info",
                    basis=self.t(
                        ("隐私政策原文里没找到: %s", "not named in the policy text: %s")
                    )
                    % names,
                    evidence_files=["raw/privacy.html"],
                )
            else:
                self.add(
                    "P06",
                    "pass",
                    "on_page",
                    severity="info",
                    evidence_files=["raw/privacy.html"],
                )

    # ------------------------------------------------------------ run
    def run_status(self):
        home = self.endpoints.get("homepage", {}).get("result")
        if "homepage" not in self.texts:
            return "failed"
        partial = self.blocked or home != "ok" and home is not None
        for key in ("product", "privacy"):
            if key not in self.texts:
                partial = True
        if self.identity["status"] == "probable":
            partial = True
        return "partial" if partial else "complete"

    def run(self):
        self.load_pages()
        if "homepage" not in self.texts:
            self.warnings.append("homepage_unavailable")
        self.detect_all()
        self.tag_list = self.tags()
        self.check_identity()
        self.check_blocked()
        if "homepage" in self.texts:
            self.check_platforms()
            self.check_gtm()
            self.check_shopify_pixels()
            self.check_placeholders()
            self.check_ad_platform_gap()
            self.check_duplicates()
            self.check_checkout_server()
            self.check_privacy()
        else:
            self.markets, self.regions, self.privacy = {}, [], None
            self.consent = {
                "consent_default": False,
                "consent_v2": False,
                "cmp": [],
                "us_opt_out": False,
            }
        if self.identity["status"] == "not_shopify":
            self.warnings.append("not_shopify")
        for p in self.pages:
            if p["result"] not in ("ok", None) or (
                p["result"] is None and p["key"] in ("product", "privacy")
            ):
                self.warnings.append("page_missing:%s" % p["key"])
        failed_containers = [
            k
            for k, v in self.endpoints.items()
            if str(k).startswith("container:") and v.get("result") != "ok"
        ]
        for k in failed_containers:
            self.warnings.append("container_missing:%s" % k.split(":", 1)[1])
        data_files = []
        for rel in cfg.DATA_FILES:
            info = datafile.freshness(
                datafile.load_json(os.path.join(HERE, "..", rel)),
                cfg.DATA_MAX_AGE_DAYS,
                lang=self.lang,
            )
            info["file"] = rel
            data_files.append(info)
            if info["stale"] is not False:
                self.warnings.append("data_file_stale:%s" % rel)
        status = self.run_status()
        order = {k: i for i, k in enumerate(cat.CHECKS)}
        self.rows.sort(key=lambda r: order.get(r["id"], 99))
        return {
            "schema": SCHEMA,
            "tool_version": cfg.TOOL_VERSION,
            "lang": self.lang,
            "mode": self.mode,
            "domain": self.status.get("domain") or guess_domain(self.data_dir),
            "base_url": self.status.get("base_url"),
            "captured_at_utc": self.checked_at,
            "summarized_at_utc": fetch.utc_now(),
            "status": status,
            "identity": self.identity,
            "pages": self.pages,
            "endpoints": self.endpoints,
            "tags": self.tag_list,
            "problems": [h for h in self.hits if h["problem"]],
            "legacy_ua": sorted(set(u for u, _s in self.ua)),
            "web_pixels": dict(self.web_pixels, inventory=self.inventory),
            "containers": self.container_rows,
            "markets": self.markets,
            "regions": self.regions,
            "consent": self.consent,
            "privacy_policy": self.privacy,
            "ad_platforms_said": self.ad_platforms(),
            "checks": self.rows,
            "user_inputs": self.user_inputs_needed(),
            "warnings": self.warnings,
            "data_files": data_files,
        }

    def user_inputs_needed(self):
        need = []
        for r in self.rows:
            uid = r.get("needs_user")
            if uid and r["result"] == "needs_input" and uid not in need:
                need.append(uid)
        answered = sorted((self.answers.get("items") or {}).keys())
        return {
            "needed": need,
            "answered": answered,
            "ad_platforms_known": self.ad_platforms() is not None,
        }


def guess_domain(data_dir):
    run = os.path.basename(os.path.dirname(os.path.abspath(data_dir)))
    return run.split(" ")[0] or "site"


def write_outputs(data_dir, summary):
    with open(os.path.join(data_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(os.path.join(data_dir, "evidence.jsonl"), "w", encoding="utf-8") as fh:
        for i, c in enumerate(summary["checks"], 1):
            level = {
                "verified": "E1",
                "on_page": "E1",
                "self_claim": "E2",
                "inferred": "E6",
            }.get(c["badge_key"], "-")
            rec = {
                "evidence_id": "E%03d" % i,
                "check": c["id"],
                "platform": c["platform"],
                "result": c["result"],
                "badge": c["badge"],
                "level": level,
                "basis": c["basis"],
                "files": c["evidence"],
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def analyze(data_dir, lang="zh", mode="own"):
    audit = Audit(data_dir, lang, mode)
    summary = audit.run()
    if cfg.pro_extend is not None and hasattr(cfg.pro_extend, "extend"):
        summary = cfg.pro_extend.extend(summary, data_dir, lang) or summary
    return summary


def main(argv=None):
    output.safe_console()
    ap = argparse.ArgumentParser(description="Tracking audit, offline.")
    ap.add_argument("path", help="run folder or its raw-data folder")
    ap.add_argument("--lang", default="zh", choices=i18n.LANGS)
    ap.add_argument(
        "--mode",
        choices=("own", "other"),
        default=None,
        help="own store (default) or someone else's store",
    )
    args = ap.parse_args(argv)
    run_dir, data_dir = output.locate_run(args.path, args.lang)
    if not os.path.isdir(os.path.join(data_dir, "raw")):
        for code in i18n.LANGS:  # raw-data folder named in the other language
            cand = os.path.join(os.path.abspath(args.path), output.raw_dirname(code))
            if os.path.isdir(os.path.join(cand, "raw")):
                data_dir = cand
                break
        else:
            data_dir = os.path.abspath(args.path)
    if not os.path.isdir(os.path.join(data_dir, "raw")):
        print("error: no raw/ under %s" % args.path, file=sys.stderr)
        return 2
    mode = args.mode
    if mode is None:  # keep the mode of an earlier run
        prev = load_json(os.path.join(data_dir, "summary.json"), {}) or {}
        mode = prev.get("mode") or "own"
    summary = analyze(data_dir, args.lang, mode)
    write_outputs(data_dir, summary)
    draft.write(data_dir, summary)
    if mode == "own":
        draft.write_intake(data_dir, summary)
    print("status: %s, warnings: %s" % (summary["status"], summary["warnings"]))
    print(
        "tags: %s" % ", ".join(sorted(set(t["platform"] for t in summary["tags"])))
        or "-"
    )
    print("summary: %s" % os.path.join(data_dir, "summary.json"))
    print("draft: %s" % os.path.join(data_dir, "report.draft.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
