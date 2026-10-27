"""Rule engine: storefront facts + questionnaire -> findings (S/P/M/T/E groups).

Each finding: id, group, severity (P0-P3 or info), result (issue | ok |
unknown | skip), badge (evidence key), check, finding, basis (for inferred),
fix, who (self | tech), where (evidence files). Rules follow
references/checks.md; severities are defaults the agent may lower with a
reason, never raise into "will be rejected / frozen" claims.

Badge discipline: icons and scripts are [页面显示] at most; an absence we
looked for is [查不到]; the seller's answers are [你提供的]; anything that
combines inputs takes the weakest one (evidence.weakest).
Python 3.8+, standard library only.
"""

import re

import signals
from _shared import evidence

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "info": 4}
GROUPS = ("S", "P", "M", "T", "E")
CARD_PROVIDERS = ("shopify_payments", "stripe", "airwallex", "card_other")
RESTRICTED_HINTS = [
    r"cbd|hemp|cannabis|marijuana|大麻",
    r"vape|e-?cig|电子烟|烟油|tobacco|烟草|cigar",
    r"firearm|gun|ammo|ammunition|weapon|knife|knives|枪|弹药|刀具|武器",
    r"adult|sex|erotic|成人|情趣",
    r"supplement|nutraceutical|保健品|膳食补充|weight loss|减肥",
    r"pharma|drug|medicine|prescription|药品|处方",
    r"replica|counterfeit|仿牌|高仿|复刻",
    r"crypto|nft|虚拟币|加密货币",
    r"gambl|casino|lottery|赌|彩票",
    r"kratom|kava|psychedelic|mushroom spores",
]


DISPLAY = {
    "zh": {
        "shopify_payments": "Shopify Payments",
        "stripe": "Stripe",
        "paypal": "PayPal",
        "airwallex": "Airwallex",
        "card_other": "其他信用卡通道",
        "manual": "手动付款",
        "cn_bank": "国内银行账户",
        "worldfirst": "WorldFirst (万里汇)",
        "payoneer": "Payoneer",
        "pingpong": "PingPong",
        "lianlian": "连连",
        "mercury": "Mercury",
        "us_bank": "美国银行账户",
        "other": "其他账户",
        "none": "还没有",
    },
    "en": {
        "shopify_payments": "Shopify Payments",
        "stripe": "Stripe",
        "paypal": "PayPal",
        "airwallex": "Airwallex",
        "card_other": "another card processor",
        "manual": "manual payments",
        "cn_bank": "a bank account in China",
        "worldfirst": "WorldFirst",
        "payoneer": "Payoneer",
        "pingpong": "PingPong",
        "lianlian": "LianLian",
        "mercury": "Mercury",
        "us_bank": "a U.S. bank account",
        "other": "another account",
        "none": "none yet",
    },
}


class Rules(object):
    def __init__(self, facts, q, lang):
        self.f = facts
        self.q = q or {}
        self.lang = lang
        self.out = []

    def t(self, zh, en):
        return zh if self.lang == "zh" else en

    def add(
        self,
        fid,
        severity,
        result,
        badge,
        check,
        finding,
        fix="",
        who="self",
        where=None,
        basis="",
    ):
        self.out.append(
            {
                "id": fid,
                "group": fid[0],
                "severity": severity if result == "issue" else "info",
                "default_severity": severity,
                "result": result,
                "badge": badge,
                "check": check,
                "finding": finding,
                "basis": basis,
                "fix": fix,
                "who": who,
                "where": list(where or []),
            }
        )

    # -- helpers --------------------------------------------------------
    def policy(self, name):
        return self.f["policies"].get(name) or {"state": "missing", "text": ""}

    def ptext(self, name):
        return self.policy(name).get("text") or ""

    def name(self, code):
        return DISPLAY[self.lang].get(code, code)

    def names(self, codes):
        return ", ".join(self.name(c) for c in codes)

    def gw(self):
        return [g["provider"] for g in self.q.get("gateways") or []]

    def has_q(self):
        return bool(self.q.get("answered"))

    def home_usable(self):
        return self.f["endpoints"].get("homepage", {}).get("result") == "ok"

    # -- S group --------------------------------------------------------
    def s_group(self):
        t, f = self.t, self.f
        home = f["endpoints"].get("homepage")
        c_https = t("首页可以打开, 使用加密连接 (https)", "Homepage opens over HTTPS")
        if f["mode"] == "offline":
            self.add(
                "S01",
                "P0",
                "unknown",
                "not_found",
                c_https,
                t(
                    "网站还没上线, 没有检查网页.",
                    "The store is not live yet; no pages were checked.",
                ),
            )
        elif home and home.get("result") == "ok":
            https = (home.get("final_url") or home.get("url") or "").startswith(
                "https://"
            )
            if https:
                self.add(
                    "S01",
                    "P0",
                    "ok",
                    "verified",
                    c_https,
                    t(
                        "首页能打开, 地址是 https 开头.",
                        "The homepage opens and its address starts with https.",
                    ),
                    where=["raw/homepage.html"],
                )
            else:
                self.add(
                    "S01",
                    "P0",
                    "issue",
                    "verified",
                    c_https,
                    t(
                        "首页最终打开的地址不是 https 开头.",
                        "The final homepage address does not start with https.",
                    ),
                    t(
                        "在域名设置里开启 SSL 证书, 并把 http 自动跳到 https.",
                        "Turn on the SSL certificate for your domain and redirect http to https.",
                    ),
                    "tech",
                    ["raw/homepage.html"],
                )
        elif home and "certificate" in (home.get("note") or "").lower():
            self.add(
                "S01",
                "P0",
                "issue",
                "verified",
                c_https,
                t(
                    "首页的安全证书校验失败, 浏览器会提示不安全.",
                    "The homepage certificate failed verification; browsers will warn buyers.",
                ),
                t("修复证书或域名解析.", "Fix the certificate or the DNS setup."),
                "tech",
            )
        else:
            res = (home or {}).get("result") or "missing"
            self.add(
                "S01",
                "P0",
                "unknown",
                "not_found",
                c_https,
                t(
                    "首页没有取到 (%s), 拿不到不等于网站有问题." % res,
                    "The homepage could not be fetched (%s); that alone does not mean the site is broken."
                    % res,
                ),
            )
        ident = f["identity"]
        check = t("建站平台", "Store platform")
        if ident in ("confirmed", "probable"):
            self.add(
                "S02",
                "info",
                "ok",
                "verified" if ident == "confirmed" else "on_page",
                check,
                t(
                    "网页里有 Shopify 的特征, 判断为 Shopify 店.",
                    "The pages carry Shopify markers: a Shopify store.",
                ),
                where=["raw/homepage.html"],
            )
        elif ident == "not_shopify":
            self.add(
                "S02",
                "info",
                "ok",
                "inferred",
                check,
                t(
                    "没有发现 Shopify 特征, 按非 Shopify 网站处理, Shopify 专项检查跳过",
                    "No Shopify markers found; treated as a non-Shopify site and Shopify-only checks skipped",
                ),
                basis=t(
                    "首页源码与店铺公开信息接口都没有 Shopify 特征",
                    "neither the homepage source nor the public store info had Shopify markers",
                ),
            )
        else:
            self.add(
                "S02",
                "info",
                "unknown",
                "not_found",
                check,
                t("没法判断建站平台.", "The store platform could not be determined."),
            )
        # S03 cart
        cart = f["cart"]
        check = t("购物车页有结账入口", "Cart page has a checkout button")
        if cart["state"] == "ok" and cart["checkout_button"]:
            self.add(
                "S03",
                "P1",
                "ok",
                "on_page",
                check,
                t("购物车页里有结账按钮.", "The cart page has a checkout button."),
                where=["raw/cart.html"],
            )
        elif cart["state"] == "ok" and cart["empty"]:
            self.add(
                "S03",
                "P1",
                "unknown",
                "not_found",
                check,
                t(
                    "空购物车页面不显示结账按钮是常见做法, 要加购后才看得到; 本工具不加购.",
                    "Empty carts usually hide the checkout button; it only shows after adding an item, which this tool never does.",
                ),
                where=["raw/cart.html"],
            )
        elif cart["state"] == "ok":
            self.add(
                "S03",
                "P1",
                "unknown",
                "not_found",
                check,
                t(
                    "购物车页里没找到结账按钮, 可能是空购物车不显示.",
                    "No checkout button found on the cart page; empty carts often hide it.",
                ),
                where=["raw/cart.html"],
            )
        elif (
            cart["state"] == "not_found"
            and "shopify.theme" in f["markers"]
            and f["identity"]
            in (
                "confirmed",
                "probable",
            )
        ):
            self.add(
                "S03",
                "P1",
                "issue",
                "not_found",
                check,
                t(
                    "购物车页 (/cart) 打不开 (404).",
                    "The cart page (/cart) returns 404.",
                ),
                t(
                    "检查主题或应用是否改掉了购物车页, 恢复后再上线收款.",
                    "Check whether the theme or an app removed the cart page and restore it.",
                ),
                "tech",
            )
        else:
            self.add(
                "S03",
                "P1",
                "unknown",
                "not_found",
                check,
                t("没有取到购物车页.", "The cart page was not fetched."),
            )
        # S04 icons
        icons = f["icons"]
        check = t("页面上的付款方式图标", "Payment icons on the page")
        if icons:
            self.add(
                "S04",
                "info",
                "ok",
                "on_page",
                check,
                t(
                    "页面上放了这些付款图标: %s. 图标不等于后台已开通."
                    % ", ".join(icons),
                    "The page shows these payment icons: %s. Icons do not prove the methods are enabled."
                    % ", ".join(icons),
                ),
                where=["raw/homepage.html"],
            )
        elif self.home_usable():
            self.add(
                "S04",
                "info",
                "unknown",
                "not_found",
                check,
                t("首页没找到付款方式图标.", "No payment icons found on the homepage."),
            )
        # S05 scripts
        sigs = f["payment_signatures"]
        check = t("支付相关脚本痕迹", "Payment script traces")
        if sigs:
            names = ", ".join(s["name"] for s in sigs)
            self.add(
                "S05",
                "info",
                "ok",
                "on_page",
                check,
                t(
                    "网页里出现了这些收款或分期服务的脚本痕迹: %s. 痕迹可能是残留, 不能说明在用."
                    % names,
                    "The pages contain script traces of: %s. Traces may be leftovers; they do not prove use."
                    % names,
                ),
                where=sorted(set(s["source"] for s in sigs)),
            )
        elif self.home_usable():
            self.add(
                "S05",
                "info",
                "ok",
                "not_found",
                check,
                t(
                    "没发现常见收款服务的脚本痕迹 (结账页不在检查范围).",
                    "No traces of common payment scripts (the checkout itself is out of scope).",
                ),
            )
        self.s06()
        # S07 accelerated checkout
        pages = f["product_pages"]
        check = t("商品页快捷结账按钮位", "Express checkout slot on product pages")
        hits = [
            fn
            for fn, src in pages
            if re.search(
                r"shopify-payment-button|data-shopify=[\"']payment-button", src or ""
            )
        ]
        if hits:
            self.add(
                "S07",
                "info",
                "ok",
                "on_page",
                check,
                t(
                    "%d 个商品页放了快捷结账按钮位; 实际显示哪些钱包要浏览器实测."
                    % len(hits),
                    "%d product pages have an express checkout slot; which wallets render needs a browser test."
                    % len(hits),
                ),
                where=["raw/" + h for h in hits],
            )
        elif pages:
            self.add(
                "S07",
                "info",
                "ok",
                "not_found",
                check,
                t(
                    "抽查的商品页里没有快捷结账按钮位.",
                    "Sampled product pages have no express checkout slot.",
                ),
            )
        # S08 BNPL
        bnpl = [s["name"] for s in sigs if s["name"] in signals.BNPL_NAMES]
        check = t(
            "分期付款挂件与目标市场", "Buy-now-pay-later widgets vs target markets"
        )
        if bnpl:
            markets = [k for k in (self.q.get("market_mix") or {})]
            if len(markets) > 1 or not markets:
                self.add(
                    "S08",
                    "P2",
                    "issue",
                    "inferred",
                    check,
                    t(
                        "页面有分期挂件痕迹 (%s) [页面显示]; 分期服务按国家开放, 你的目标市场里可能有不支持的"
                        % ", ".join(bnpl),
                        "BNPL widget traces (%s) [Shown on page]; these services open country by country, so some of your markets may not be supported"
                        % ", ".join(bnpl),
                    ),
                    t(
                        "到分期服务的后台确认每个目标市场是否可用, 不可用的市场隐藏分期提示.",
                        "Check in the provider's dashboard which markets are supported and hide the widget elsewhere.",
                    ),
                    "tech",
                    basis=t(
                        "脚本痕迹 + 市场分布%s" % ("" if markets else " (未知)"),
                        "script traces + market mix%s"
                        % ("" if markets else " (unknown)"),
                    ),
                )
            else:
                self.add(
                    "S08",
                    "P2",
                    "ok",
                    "on_page",
                    check,
                    t(
                        "有分期挂件痕迹 (%s), 你只卖一个市场." % ", ".join(bnpl),
                        "BNPL traces (%s); you sell to one market." % ", ".join(bnpl),
                    ),
                )
        # S09 payment data handling statement
        check = t(
            "隐私或条款里写了付款信息怎么处理",
            "Policies say how payment details are handled",
        )
        pol = self.ptext("privacy-policy") + "\n" + self.ptext("terms-of-service")
        if pol.strip():
            snip = signals.snippet(
                pol,
                r"payment (?:information|details|data)|credit card (?:information|details|number)|card details|payment processor|PCI|支付信息|银行卡信息",
            )
            if snip:
                self.add(
                    "S09",
                    "P3",
                    "ok",
                    "verified",
                    check,
                    t(
                        '政策里提到了付款信息: "%s"' % snip,
                        'The policies mention payment data: "%s"' % snip,
                    ),
                    where=["raw/policy-privacy-policy.html"],
                )
            else:
                self.add(
                    "S09",
                    "P3",
                    "issue",
                    "not_found",
                    check,
                    t(
                        "隐私政策和服务条款里没找到付款信息如何处理的说明.",
                        "No statement on how payment details are handled in the privacy policy or terms.",
                    ),
                    t(
                        "加一句: 付款信息由支付服务方处理, 本店不保存完整卡号.",
                        "Add a line: payments are processed by the payment provider; the store does not keep full card numbers.",
                    ),
                )

    def s06(self):
        t, f = self.t, self.f
        check = t(
            "付款图标与实际通道是否一致", "Payment icons vs the providers you use"
        )
        icons = set(f["icons"])
        sigs = set(s["name"] for s in f["payment_signatures"])
        gws = self.gw()
        if not gws or not (icons or sigs):
            if icons or sigs:
                self.add(
                    "S06",
                    "P1",
                    "unknown",
                    "not_found",
                    check,
                    t(
                        "没有你的通道信息, 没法对照页面图标.",
                        "Without your provider list the icons cannot be compared.",
                    ),
                )
            return
        problems = []
        card_icons = icons & signals.CARD_ICONS
        if card_icons and not any(g in CARD_PROVIDERS for g in gws):
            problems.append(
                t(
                    "页面有信用卡/钱包图标 (%s), 你说的通道里没有信用卡通道"
                    % ", ".join(sorted(card_icons)),
                    "card/wallet icons (%s) but no card provider in your list"
                    % ", ".join(sorted(card_icons)),
                )
            )
        if "paypal" in icons and "paypal" not in gws:
            problems.append(
                t(
                    "页面有 PayPal 图标, 你说的通道里没有 PayPal",
                    "a PayPal icon but no PayPal in your list",
                )
            )
        if "shopify-pay" in icons and "shopify_payments" not in gws:
            problems.append(
                t(
                    "页面有 Shop Pay 图标, Shop Pay 属于 Shopify Payments, 你说的通道里没有它",
                    "a Shop Pay icon (part of Shopify Payments) but Shopify Payments is not in your list",
                )
            )
        if self.q.get("entity_country") == "CN" and icons & {
            "american-express",
            "amex",
        }:
            if set(gws) <= {"shopify_payments", "stripe", "paypal"}:
                problems.append(
                    t(
                        "页面有 Amex 图标; 官方说明大陆主体的 Shopify Payments 只支持 Visa/Mastercard, Stripe 不支持大陆主体",
                        "an Amex icon; Shopify Payments for mainland China entities supports Visa/Mastercard only and Stripe does not serve mainland entities",
                    )
                )
        bnpl_seen = sigs & set(signals.BNPL_NAMES)
        if bnpl_seen and not (set(gws) & {"stripe", "shopify_payments", "airwallex"}):
            problems.append(
                t(
                    "页面有分期脚本痕迹 (%s), 你说的通道里没有对应服务"
                    % ", ".join(sorted(bnpl_seen)),
                    "BNPL traces (%s) but no matching provider in your list"
                    % ", ".join(sorted(bnpl_seen)),
                )
            )
        if problems:
            self.add(
                "S06",
                "P1",
                "issue",
                "conflicting",
                check,
                "; ".join(problems) + ".",
                t(
                    "只放实际开通的付款方式图标 (Stripe 网站清单: 展示你接受的卡种); 删掉残留的支付脚本.",
                    "Show only the payment methods you actually accept (Stripe website checklist); remove leftover payment scripts.",
                ),
                "self",
                ["raw/homepage.html", "questionnaire.json"],
            )
        else:
            self.add(
                "S06",
                "P1",
                "ok",
                "user_input",
                check,
                t(
                    "页面图标和你说的通道没有明显冲突.",
                    "No obvious conflict between page icons and the providers you named.",
                ),
            )

    # -- P group --------------------------------------------------------
    def p_group(self):
        t, f = self.t, self.f
        if f["mode"] == "offline":
            for fid, check in (("P01", t("五个政策页齐全", "All five policy pages")),):
                self.add(
                    fid,
                    "P0",
                    "unknown",
                    "not_found",
                    check,
                    t(
                        "网站还没上线, 政策页没法检查; 上线前要准备好退款, 配送, 隐私, 条款, 联系信息五页.",
                        "The store is not live; prepare refund, shipping, privacy, terms and contact pages before launch.",
                    ),
                )
            return
        names = {
            "refund-policy": t("退款政策", "refund policy"),
            "shipping-policy": t("配送政策", "shipping policy"),
            "privacy-policy": t("隐私政策", "privacy policy"),
            "terms-of-service": t("服务条款", "terms of service"),
            "contact-information": t("联系信息页", "contact information page"),
        }
        missing, unknown, have = [], [], []
        for name in signals.POLICY_NAMES:
            st = self.policy(name)["state"]
            if st == "ok":
                have.append(name)
            elif st == "not_found":
                if name == "contact-information" and (
                    f["contact"] or f["identity"] == "not_shopify"
                ):
                    continue  # a contact page covers it on non-Shopify sites
                missing.append(name)
            else:
                unknown.append(name)
        check = t("五个政策页齐全", "All five policy pages present")
        if missing and any(
            self.policy(n)["state"] == "js_rendered" for n in signals.POLICY_NAMES
        ):
            # pages on this site render with scripts; a 404 at the usual
            # address says little about whether the policy exists elsewhere
            unknown.extend(missing)
            missing = []
        if missing:
            sev = "P0" if set(missing) & {"refund-policy", "shipping-policy"} else "P1"
            self.add(
                "P01",
                sev,
                "issue",
                "not_found",
                check,
                t(
                    "在常见位置没找到: %s." % ", ".join(names[n] for n in missing),
                    "Not found at the usual addresses: %s."
                    % ", ".join(names[n] for n in missing),
                ),
                t(
                    "在后台政策设置里补齐 (Stripe 网站清单列明了退款, 配送, 退货政策); 已有的话把它链接到页脚.",
                    "Add them in the policy settings (Stripe's website checklist lists refund, shipping and return policies); if they exist, link them in the footer.",
                ),
                "self",
                ["raw/_status.json"],
            )
        elif not unknown:
            self.add(
                "P01",
                "P0",
                "ok",
                "verified",
                check,
                t("五个政策页都能打开.", "All five policy pages open."),
            )
        if unknown:
            self.add(
                "P01",
                "P1",
                "unknown",
                "not_found",
                t("政策页 (部分没取到)", "Policy pages (some not fetched)"),
                t(
                    "这些没读到内容, 不等于没有 (有的网站要浏览器运行脚本才显示正文): %s."
                    % ", ".join(names[n] for n in unknown),
                    "No readable content (not the same as missing; some sites only show text after running scripts in a browser): %s."
                    % ", ".join(names[n] for n in unknown),
                ),
            )
        # P02 short / template
        short, tmpl = [], []
        for name in have:
            text = self.ptext(name)
            if len(text) < signals.POLICY_MIN_CHARS:
                short.append("%s (%s)" % (names[name], len(text) or t("空白", "empty")))
            if re.search(
                r"\[(?:your |insert |company|store )[^\]]*\]|\{\{|lorem ipsum|YOUR COMPANY NAME|\[STORE NAME\]|xxx company",
                text,
                re.I,
            ):
                tmpl.append(names[name])
        check = t("政策正文是否写实", "Policy text is real, not a stub")
        if short or tmpl:
            parts = []
            if short:
                parts.append(
                    t(
                        "正文过短 (字符数): %s" % ", ".join(short),
                        "very short (characters): %s" % ", ".join(short),
                    )
                )
            if tmpl:
                parts.append(
                    t(
                        "还留着模板占位符: %s" % ", ".join(tmpl),
                        "template placeholders left in: %s" % ", ".join(tmpl),
                    )
                )
            self.add(
                "P02",
                "P1",
                "issue",
                "verified",
                check,
                "; ".join(parts) + ".",
                t(
                    "按自家实际情况写, 写清公司名与地址.",
                    "Write them for your real business, with company name and address.",
                ),
                "self",
                ["raw/policy-*.html"],
            )
        elif have:
            self.add(
                "P02",
                "P1",
                "ok",
                "verified",
                check,
                t(
                    "取到的政策页正文都超过 %d 个字符, 没发现模板占位符."
                    % signals.POLICY_MIN_CHARS,
                    "Every fetched policy has over %d characters and no template placeholders."
                    % signals.POLICY_MIN_CHARS,
                ),
            )
        self.p03()
        self.p04()
        self.p05()
        self.p06()
        self.p07()
        self.p08()

    def p03(self):
        t = self.t
        text = self.ptext("refund-policy")
        check = t("退款政策要素", "Refund policy elements")
        if not text:
            return
        elems = [
            (
                "days",
                t("退货期限天数", "return window in days"),
                signals.return_windows(text),
            ),
            (
                "cond",
                t("退货条件", "conditions"),
                signals.contains(
                    text,
                    r"unworn|unused|original (?:condition|packaging)|tags|undamaged|未使用|原包装",
                ),
            ),
            (
                "method",
                t("退款方式", "refund method"),
                signals.contains(
                    text,
                    r"original (?:form of )?payment|store credit|refund (?:will be )?(?:issued|credited)|原路|退回",
                ),
            ),
            (
                "ship",
                t("退货运费谁出", "who pays return shipping"),
                signals.contains(
                    text,
                    r"return shipping|shipping costs? (?:are|is) non-refundable|prepaid (?:return )?label|free returns?|customer (?:is|will be) responsible|退货运费",
                ),
            ),
            (
                "time",
                t("处理时长", "processing time"),
                signals.contains(
                    text,
                    r"within \d+\s*(?:business |working )?days|processed within|\d+\s*-\s*\d+\s*(?:business )?days|处理时间",
                ),
            ),
        ]
        missing = [label for key, label, ok in elems if not ok]
        if missing:
            sev = "P1" if not elems[0][2] else "P2"
            self.add(
                "P03",
                sev,
                "issue",
                "verified",
                check,
                t(
                    "退款政策里没找到: %s." % ", ".join(missing),
                    "Not found in the refund policy: %s." % ", ".join(missing),
                ),
                t(
                    "补齐缺的要素, 每项写一个明确的数.",
                    "Add the missing elements with concrete numbers.",
                ),
                "self",
                ["raw/policy-refund-policy.html"],
            )
        else:
            days = elems[0][2][0]
            self.add(
                "P03",
                "P1",
                "ok",
                "verified",
                check,
                t(
                    "退款政策写了期限 (%d 天), 条件, 退款方式, 退货运费和处理时长."
                    % days,
                    "The refund policy states the window (%d days), conditions, method, return shipping and timing."
                    % days,
                ),
                where=["raw/policy-refund-policy.html"],
            )

    def p04(self):
        t, f = self.t, self.f
        text = self.ptext("shipping-policy")
        check = t("配送政策要素", "Shipping policy elements")
        if not text:
            return
        elems = [
            (
                t("处理天数", "processing time"),
                r"processing time|processed within|ships? within|dispatch|handling time|发货",
            ),
            (
                t("运输时长", "transit time"),
                r"delivery time|transit|arrive|business days|working days|送达",
            ),
            (
                t("运费", "shipping cost"),
                r"free shipping|shipping (?:cost|fee|rate)s?|flat rate|\$\s?\d|运费",
            ),
            (
                t("可送达地区", "destinations"),
                r"ship (?:to|worldwide)|countries|worldwide|international|united states|canada|地区|国家",
            ),
        ]
        missing = [label for label, pat in elems if not signals.contains(text, pat)]
        conflict = None
        ships = f["ships_to"]
        if ships is not None:
            if (
                signals.contains(
                    text, r"only ship (?:within|to) the (?:united states|us\b|u\.s\.)"
                )
                and len(ships) > 1
            ):
                conflict = t(
                    "政策说只送美国, 店铺设置可送 %d 个国家/地区" % len(ships),
                    "the policy says US only, the store settings ship to %d countries"
                    % len(ships),
                )
            elif (
                signals.contains(
                    text,
                    r"worldwide|ship (?:to )?(?:all|most) countries|international shipping",
                )
                and len(ships) == 1
            ):
                conflict = t(
                    "政策说可国际配送, 店铺设置只送 1 个国家/地区 (%s)" % ships[0],
                    "the policy offers international shipping, the store settings ship to 1 country (%s)"
                    % ships[0],
                )
        if conflict:
            self.add(
                "P04",
                "P1",
                "issue",
                "conflicting",
                check,
                conflict + ".",
                t(
                    "政策写的可送达地区与后台配送设置统一.",
                    "Make the policy and the shipping settings list the same destinations.",
                ),
                "self",
                ["raw/policy-shipping-policy.html", "raw/meta.json"],
            )
        elif missing:
            self.add(
                "P04",
                "P1",
                "issue",
                "verified",
                check,
                t(
                    "配送政策里没找到: %s." % ", ".join(missing),
                    "Not found in the shipping policy: %s." % ", ".join(missing),
                ),
                t(
                    "写明处理天数, 运输时长, 运费和可送达地区.",
                    "State processing time, transit time, cost and destinations.",
                ),
                "self",
                ["raw/policy-shipping-policy.html"],
            )
        else:
            self.add(
                "P04",
                "P1",
                "ok",
                "verified",
                check,
                t(
                    "配送政策写了处理天数, 运输时长, 运费和可送达地区.",
                    "The shipping policy states processing, transit, cost and destinations.",
                ),
                where=["raw/policy-shipping-policy.html"],
            )

    def p05(self):
        t, f = self.t, self.f
        check = t(
            "退货天数与免费退货说法前后一致",
            "Return window and free-returns wording match",
        )
        policy = self.ptext("refund-policy")
        windows = signals.return_windows(policy)
        if not policy or not windows:
            return
        main = windows[0]
        others = []
        for fname, src in f["other_texts"]:
            for n in signals.page_return_claims(src):
                if n != main:
                    others.append(
                        (
                            fname,
                            n,
                            signals.snippet(src, r"\b%d[- ]?days?" % n) or str(n),
                        )
                    )
        free_conflict = None
        if signals.contains(
            policy,
            r"customer (?:is|will be) responsible for (?:paying for )?(?:the )?return shipping|return shipping (?:costs? )?(?:is|are) (?:the customer|non-refundable|paid by)",
        ):
            for fname, src in f["other_texts"]:
                snip = signals.snippet(src, r"free returns?")
                if snip:
                    free_conflict = (fname, snip)
                    break
        if others or free_conflict:
            parts = []
            if others:
                fname, n, snip = others[0]
                parts.append(
                    t(
                        '退款政策写 %d 天; %s 写 "%s"'
                        % (main, page_label(fname, "zh"), snip),
                        'refund policy says %d days; %s says "%s"'
                        % (main, page_label(fname, "en"), snip),
                    )
                )
            if free_conflict:
                parts.append(
                    t(
                        '退款政策写退货运费由顾客承担; %s 写 "%s"'
                        % (page_label(free_conflict[0], "zh"), free_conflict[1]),
                        'refund policy says buyers pay return shipping; %s says "%s"'
                        % (page_label(free_conflict[0], "en"), free_conflict[1]),
                    )
                )
            where = ["raw/policy-refund-policy.html"] + [
                "raw/" + o[0] for o in others[:3]
            ]
            if free_conflict:
                where.append("raw/" + free_conflict[0])
            self.add(
                "P05",
                "P1",
                "issue",
                "conflicting",
                check,
                "; ".join(parts) + ".",
                t(
                    "统一成一个数, 以政策页为准 (拒付举证时通道方看政策原文), 再改商品页, FAQ 和页脚.",
                    "Use one number everywhere, matching the policy page (providers read the policy text in disputes).",
                ),
                "self",
                where,
            )
        else:
            self.add(
                "P05",
                "P1",
                "ok",
                "verified",
                check,
                t(
                    "退款政策写 %d 天, 抽查的其他页面没有不同的天数." % main,
                    "Refund policy: %d days; sampled pages show no different number."
                    % main,
                ),
                where=["raw/policy-refund-policy.html"],
            )

    def p06(self):
        t, f = self.t, self.f
        subs = [a["name"] for a in f["apps"] if a.get("kind") == "subscription"]
        pre_pages = [
            fn
            for fn, src in f["product_pages"]
            if re.search(r"(?i)pre-?order", signals.html_to_text(src or ""))
        ]
        wants_sub = subs or self.q.get("subscription") is True
        wants_pre = pre_pages or self.q.get("preorder") is True
        if not (wants_sub or wants_pre):
            return
        check = t(
            "订阅/预售的取消与发货说明",
            "Subscription / pre-order cancellation and shipping terms",
        )
        text = (
            self.ptext("terms-of-service")
            + "\n"
            + self.ptext("refund-policy")
            + "\n"
            + self.ptext("shipping-policy")
        )
        gaps = []
        if wants_sub and not signals.contains(text, r"cancel"):
            gaps.append(t("订阅的取消方式", "how to cancel a subscription"))
        if wants_pre and not signals.contains(text, r"pre-?order"):
            gaps.append(t("预售的发货时间", "pre-order shipping dates"))
        trigger = (
            ", ".join(subs)
            if subs
            else (
                t("预售商品", "pre-order items")
                if pre_pages
                else t("你的回答", "your answers")
            )
        )
        badge = "on_page" if (subs or pre_pages) else "user_input"
        if gaps:
            self.add(
                "P06",
                "P1",
                "issue",
                "not_found",
                check,
                t(
                    "有订阅/预售迹象 (%s), 政策里没找到: %s."
                    % (trigger, ", ".join(gaps)),
                    "Subscription / pre-order signs (%s), but the policies do not cover: %s."
                    % (trigger, ", ".join(gaps)),
                ),
                t(
                    "写清取消政策和预售发货时间 (Stripe 网站清单列了取消政策).",
                    "Write the cancellation policy and pre-order ship dates (Stripe's website checklist lists cancellation).",
                ),
                "self",
                ["raw/policy-terms-of-service.html"],
            )
        else:
            self.add(
                "P06",
                "P1",
                "ok",
                badge,
                check,
                t(
                    "有订阅/预售迹象 (%s), 政策里提到了取消或预售." % trigger,
                    "Subscription / pre-order signs (%s) and the policies mention them."
                    % trigger,
                ),
            )

    def p07(self):
        t, f = self.t, self.f
        check = t(
            "告诉买家卡账单上显示什么名字",
            "Tells buyers what name appears on their card statement",
        )
        texts = [self.ptext(n) for n in signals.POLICY_NAMES] + [
            src for _fn, src in f["other_texts"] if _fn.startswith("page-faq")
        ]
        joined = "\n".join(x for x in texts if x)
        if not joined.strip():
            return
        snip = signals.snippet(
            joined,
            r"(?:appear|show)s? on your (?:credit card |bank |card )?statement|billing descriptor|statement descriptor|charge will (?:appear|show)|账单上显示",
        )
        names = []
        for n in (
            "terms-of-service",
            "contact-information",
            "privacy-policy",
            "refund-policy",
        ):
            for name in signals.legal_names(self.ptext(n)):
                if name not in names:
                    names.append(name)
        brand = f["shop_name"] or ""
        differs = (
            names
            and brand
            and not any(
                signals.norm_legal(brand) in signals.norm_legal(n)
                or signals.norm_legal(n) in signals.norm_legal(brand)
                for n in names
            )
        )
        if snip:
            self.add(
                "P07",
                "P2",
                "ok",
                "verified",
                check,
                t('站内有说明: "%s"' % snip, 'The site explains it: "%s"' % snip),
            )
        else:
            extra = ""
            if differs:
                extra = t(
                    " 公司名 (%s) 和店名 (%s) 不同, 买家更可能认不出扣款"
                    % (names[0], brand),
                    " The company name (%s) differs from the store name (%s), so buyers may not recognise the charge"
                    % (names[0], brand),
                )
            self.add(
                "P07",
                "P2",
                "issue",
                "inferred",
                check,
                t(
                    "政策和 FAQ 里没找到 '卡账单上显示为 XXX' 的说明." + extra,
                    "No 'this charge appears on your statement as XXX' note in policies or FAQ."
                    + extra,
                ),
                t(
                    "在 FAQ 和订单确认邮件加一句账单显示名 (也就是买家信用卡账单上显示的商家名); 可减少 '认不出这笔扣款' 引起的拒付.",
                    "Add the statement name (the merchant name on the buyer's card statement) to the FAQ and order emails.",
                ),
                "self",
                basis=t(
                    "政策与 FAQ 全文检索没有相关说法%s"
                    % ("; 条款中的公司名与店名不同" if differs else ""),
                    "full-text search of policies and FAQ%s"
                    % ("; company name differs from store name" if differs else ""),
                ),
            )

    def p08(self):
        t, f = self.t, self.f
        home_text = f["home_text"]
        promo = signals.snippet(
            home_text, r"\d{1,2}% off|discount code|use code|promo code|coupon"
        )
        if not promo:
            return  # a pop-up app alone is not a promotion
        check = t("促销活动写了条件", "Promotions state their terms")
        all_text = home_text + "\n" + self.ptext("terms-of-service")
        if signals.contains(
            all_text,
            r"terms (?:and conditions )?apply|exclusions apply|promotion terms|offer (?:valid|ends|expires)|see details|cannot be combined|one (?:code|use) per",
        ):
            self.add(
                "P08",
                "P3",
                "ok",
                "verified",
                check,
                t(
                    "有促销, 页面或条款里有活动条件的说法.",
                    "Promotions present, and terms are mentioned.",
                ),
            )
        else:
            trig = '"%s"' % promo
            self.add(
                "P08",
                "P3",
                "issue",
                "inferred",
                check,
                t(
                    "首页有促销文字 (%s) [页面显示], 没找到活动条件说明" % trig,
                    "Promotion text on the homepage (%s) [Shown on page], but no promotion terms found"
                    % trig,
                ),
                t(
                    "在活动处放一个条件链接 (适用范围, 截止时间, 能否叠加).",
                    "Link the terms next to the offer (scope, end date, stacking).",
                ),
                "self",
                basis=t("首页文字与服务条款检索", "search of homepage text and terms"),
            )

    # -- M group --------------------------------------------------------
    def m_group(self):
        t, f = self.t, self.f
        if f["mode"] == "offline":
            return
        pages = f["product_pages"]
        markets = set((self.q.get("market_mix") or {}).keys()) | set(
            f["ships_to"] or []
        )
        dollar = markets & set(signals.DOLLAR_MARKETS)
        check = t("价格显示带币种代码", "Prices show a currency code")
        if pages:
            texts = [signals.html_to_text(src or "") for _fn, src in pages]
            has_dollar = any(re.search(r"\$\s?\d", x) for x in texts)
            has_code = any(
                re.search(r"\b(%s)\b" % "|".join(signals.CURRENCY_CODES), x)
                for x in texts
            )
            if has_dollar and not has_code and len(dollar) > 1:
                self.add(
                    "M01",
                    "P2",
                    "issue",
                    "verified",
                    check,
                    t(
                        "商品页价格只写了 $ 没写币种, 而目标市场里有多个用 $ 的国家 (%s)."
                        % ", ".join(sorted(dollar)),
                        "Product prices show only $ without a code, and several $ markets are targeted (%s)."
                        % ", ".join(sorted(dollar)),
                    ),
                    t(
                        "价格旁显示 USD/CAD 等币种代码 (Stripe 网站清单: 只写符号可能不够).",
                        "Show USD/CAD next to prices (Stripe's checklist: a symbol alone may not be enough).",
                    ),
                    "tech",
                    ["raw/" + pages[0][0]],
                )
            elif has_dollar and not has_code:
                self.add(
                    "M01",
                    "P2",
                    "ok",
                    "verified",
                    check,
                    t(
                        "价格只写了 $; 目前只看到一个用 $ 的市场, 暂不需要改.",
                        "Prices show only $; only one $ market seen so far.",
                    ),
                )
            else:
                self.add(
                    "M01",
                    "P2",
                    "ok",
                    "verified",
                    check,
                    t(
                        "商品页价格带币种代码或不是 $ 计价.",
                        "Product prices carry a currency code or are not in $.",
                    ),
                )
        # M02 currency chain
        base_cur = f["base_currency"]
        shown = f["market"].get("currency_active")
        payout = self.q.get("payout_currency")
        steps = []
        if shown and base_cur and shown != base_cur:
            steps.append(
                t(
                    "顾客看到 %s, 店铺结算 %s" % (shown, base_cur),
                    "buyers see %s, store settles in %s" % (shown, base_cur),
                )
            )
        if payout and base_cur and payout != base_cur:
            steps.append(
                t(
                    "店铺结算 %s, 提现 %s" % (base_cur, payout),
                    "store settles in %s, payout in %s" % (base_cur, payout),
                )
            )
        if base_cur or shown:
            badge = evidence.weakest(["verified"] + (["user_input"] if payout else []))
            self.add(
                "M02",
                "info",
                "ok",
                badge,
                t("币种与换汇环节", "Currencies and conversion steps"),
                t(
                    "店铺基础币种 %s, 本次访问看到的币种 %s%s; 换汇环节 %d 个%s."
                    % (
                        base_cur or "?",
                        shown or "?",
                        ("; 你说的提现币种 %s" % payout) if payout else "",
                        len(steps),
                        (": " + "; ".join(steps)) if steps else "",
                    ),
                    "Store currency %s, currency seen on this visit %s%s; %d conversion steps%s."
                    % (
                        base_cur or "?",
                        shown or "?",
                        ("; your payout currency %s" % payout) if payout else "",
                        len(steps),
                        (": " + "; ".join(steps)) if steps else "",
                    ),
                ),
            )
        # M03 multi-currency display
        conv = [a["name"] for a in f["apps"] if a.get("kind") == "currency"]
        check = t("多币种显示方式", "How multiple currencies are shown")
        if conv:
            self.add(
                "M03",
                "P1",
                "issue",
                "on_page",
                check,
                t(
                    "页面有第三方汇率换算应用痕迹 (%s): 这类应用通常只换算显示价, 顾客实付仍按店铺币种, 显示价和实付价会不一样."
                    % ", ".join(conv),
                    "Third-party currency converter traces (%s): such apps usually convert the displayed price only, so the charged amount differs."
                    % ", ".join(conv),
                ),
                t(
                    "改用平台原生多币种 (Shopify 价格页注明需 Shopify Payments), 或只显示结算币种.",
                    "Use the platform's native multi-currency (Shopify notes it needs Shopify Payments), or show the settlement currency only.",
                ),
                "tech",
                ["raw/homepage.html"],
            )
        elif f["localization"]:
            self.add(
                "M03",
                "P1",
                "ok",
                "on_page",
                check,
                t(
                    "页面有平台自带的国家/币种选择器 (%d 个语言/地区版本链接)."
                    % len(f["hreflang"]),
                    "The page has the platform's own country/currency selector (%d language/region links)."
                    % len(f["hreflang"]),
                ),
            )
        # M04 markets vs ships-to
        mix = self.q.get("market_mix") or {}
        if mix and f["ships_to"] is not None:
            ships = set(f["ships_to"]) | ({"UK"} if "GB" in f["ships_to"] else set())
            gap = [m for m in mix if m not in ships and m != "EU"]
            check = t(
                "目标市场与可配送国家一致", "Target markets vs shipping destinations"
            )
            if gap:
                self.add(
                    "M04",
                    "P2",
                    "issue",
                    "conflicting",
                    check,
                    t(
                        "你说要卖的市场 %s 不在店铺可配送国家里 [你提供的] / [已核实]"
                        % ", ".join(gap),
                        "Markets you named (%s) are not in the store's shipping destinations [You provided] / [Verified]"
                        % ", ".join(gap),
                    ),
                    t(
                        "在后台配送设置里加上这些市场, 或调整目标市场.",
                        "Add these markets to shipping settings or adjust your targets.",
                    ),
                    "self",
                    ["raw/meta.json", "questionnaire.json"],
                )
            else:
                self.add(
                    "M04",
                    "P2",
                    "ok",
                    "verified",
                    check,
                    t(
                        "你说的目标市场都在可配送国家里.",
                        "All your target markets are in the shipping destinations.",
                    ),
                )

    # -- T group --------------------------------------------------------
    def t_group(self):
        t, f = self.t, self.f
        if f["mode"] == "offline":
            return
        texts = [("homepage.html", f["home_raw"])]
        if f["contact"]:
            texts.append(f["contact"])
        for name in (
            "contact-information",
            "refund-policy",
            "terms-of-service",
            "privacy-policy",
        ):
            p = self.policy(name)
            if p.get("raw"):
                texts.append(("policy-%s.html" % name, p["raw"]))
        emails, phones, form = set(), set(), False
        for _fn, src in texts:
            src = src or ""
            for m in re.finditer(r"mailto:([^\"'?>\s]+)", src):
                emails.add(m.group(1).lower())
            plain = signals.html_to_text(src)
            for m in signals.EMAIL_RE.finditer(plain):
                e = m.group(0).lower()
                if not re.search(
                    r"\.(png|jpg|gif|svg|webp)$|sentry|example\.|wixpress|@2x", e
                ):
                    emails.add(e)
            for m in re.finditer(r"tel:([+\d\-\s().]{7,})", src):
                phones.add(re.sub(r"\D", "", m.group(1)))
            if re.search(r"(?i)phone|call us|tel[:.]|电话", plain):
                for m in signals.PHONE_RE.finditer(plain):
                    phones.add(re.sub(r"\D", "", m.group(0)))
            if re.search(
                r"(?i)contact\[email\]|<form[^>]+contact|id=[\"']contact", src
            ):
                form = True
        chat = [a["name"] for a in f["apps"] if a.get("kind") == "chat"]
        methods = []
        if emails:
            methods.append(t("邮箱", "email"))
        if phones:
            methods.append(t("电话", "phone"))
        if chat:
            methods.append(
                t("在线客服 (%s)" % ", ".join(chat), "live chat (%s)" % ", ".join(chat))
            )
        check = t("联系方式不只一个表单", "More than a contact form")
        where = ["raw/" + fn for fn, _s in texts[1:]] or ["raw/homepage.html"]
        if len(methods) >= 2:
            self.add(
                "T01",
                "P1",
                "ok",
                "verified",
                check,
                t("找到 %s." % ", ".join(methods), "Found %s." % ", ".join(methods)),
                where=where,
            )
        elif len(methods) == 1:
            self.add(
                "T01",
                "P2",
                "issue",
                "not_found",
                check,
                t(
                    "只找到一种联系方式 (%s), 首页, 联系页和政策页里没找到其他方式."
                    % methods[0],
                    "Only one contact method found (%s); no other on the homepage, contact page or policies."
                    % methods[0],
                ),
                t(
                    "再加一种 (邮箱, 电话或在线客服), 写在联系页和页脚.",
                    "Add another (email, phone or chat) on the contact page and footer.",
                ),
                "self",
                where,
            )
        elif form:
            self.add(
                "T01",
                "P1",
                "issue",
                "not_found",
                check,
                t(
                    "只找到联系表单, 没找到邮箱, 电话或在线客服.",
                    "Only a contact form; no email, phone or chat found.",
                ),
                t(
                    "加邮箱与客服 (Stripe 网站清单: 多种联系方式, 不只表单).",
                    "Add an email and chat (Stripe's checklist asks for several contact methods, not only a form).",
                ),
                "self",
                where,
            )
        elif self.home_usable():
            self.add(
                "T01",
                "P0",
                "issue",
                "not_found",
                check,
                t(
                    "首页, 联系页和政策页里都没找到联系方式.",
                    "No contact method on the homepage, contact page or policies.",
                ),
                t(
                    "加联系页, 写邮箱和客服方式, 链接到页脚.",
                    "Add a contact page with email and chat, linked in the footer.",
                ),
                "self",
                where,
            )
        # T02 legal name + address
        legal = {}
        addr_hits = []
        for fn, src in texts[1:]:
            plain = signals.main_text(src or "")
            for n in signals.legal_names(plain):
                legal.setdefault(signals.norm_legal(n), (n, fn))
            a = signals.snippet(
                plain,
                r"\b\d{1,5}\s+[A-Z][A-Za-z ]+(?:Street|St\.|Road|Rd\.|Avenue|Ave\.?|Boulevard|Blvd|Lane|Drive|Way|Suite)\b|\b(?:Room|Rm\.|Unit|Flat|Floor)\s*\d+|[一-鿿]{2,}(?:路|街|大道)\d+号",
            )
            if a:
                addr_hits.append((fn, a))
        check = t("公司名称与经营地址", "Company name and business address")
        names = [v[0] for v in legal.values()]
        if len(names) > 1:
            self.add(
                "T02",
                "P2",
                "issue",
                "conflicting",
                check,
                t(
                    "页面里出现了不止一个公司名: %s."
                    % "; ".join('"%s"' % n for n in names[:3]),
                    "More than one company name appears: %s."
                    % "; ".join('"%s"' % n for n in names[:3]),
                ),
                t(
                    "确认收款账户的主体是哪一个; 条款, 联系页, 退货地址写清各自角色, 收款主体要和通道账户一致.",
                    "Confirm which company holds the payment account; state each company's role and keep the payee consistent with the provider account.",
                ),
                "self",
                ["raw/" + v[1] for v in legal.values()],
            )
        elif names and addr_hits:
            self.add(
                "T02",
                "P2",
                "ok",
                "verified",
                check,
                t(
                    '写了公司名 "%s" 和地址 ("%s").' % (names[0], addr_hits[0][1]),
                    'States company "%s" and an address ("%s").'
                    % (names[0], addr_hits[0][1]),
                ),
            )
        elif f["policies_seen"]:
            missing = []
            if not names:
                missing.append(t("公司全称", "legal company name"))
            if not addr_hits:
                missing.append(t("经营地址", "business address"))
            self.add(
                "T02",
                "P2",
                "issue",
                "not_found",
                check,
                t(
                    "联系页和政策页里没找到: %s." % ", ".join(missing),
                    "Not found on the contact page or policies: %s."
                    % ", ".join(missing),
                ),
                t(
                    "在联系页和服务条款里写明公司全称和经营地址.",
                    "State the legal company name and business address on the contact page and terms.",
                ),
                "self",
            )
        # T03 store country vs entity
        ec = self.q.get("entity_country")
        mc = f["meta_country"]
        check = t("店铺国家与收款主体国家", "Store country vs your company's country")
        if ec and mc:
            same = (ec == mc) or (ec == "UK" and mc == "GB")
            if same:
                self.add(
                    "T03",
                    "P2",
                    "ok",
                    "verified",
                    check,
                    t(
                        "店铺设置的国家 (%s) 与你说的主体国家一致." % mc,
                        "Store country (%s) matches your company's country." % mc,
                    ),
                )
            else:
                self.add(
                    "T03",
                    "P2",
                    "issue",
                    "conflicting",
                    check,
                    t(
                        "店铺设置的国家是 %s [已核实], 你说的主体在 %s [你提供的]."
                        % (mc, ec),
                        "Store country is %s [Verified]; your company is in %s [You provided]."
                        % (mc, ec),
                    ),
                    t(
                        "后台店铺地址按实际收款主体填写.",
                        "Set the store address to your actual payee company.",
                    ),
                    "self",
                    ["raw/meta.json", "questionnaire.json"],
                )
        # T04 product descriptions
        ps = f["product_stats"]
        check = t("商品描述与图片是否充分", "Product descriptions and images")
        if ps.get("count"):
            short = (
                ps["desc_chars_median"] is not None and ps["desc_chars_median"] < 150
            )
            few = ps["images_median"] is not None and ps["images_median"] < 2
            desc = t(
                "前 %d 款商品描述正文中位数 %d 字符, 每款图片中位数 %s 张"
                % (ps["count"], ps["desc_chars_median"], fmt(ps["images_median"])),
                "first %d products: median description %d characters, median %s images"
                % (ps["count"], ps["desc_chars_median"], fmt(ps["images_median"])),
            )
            if short or few:
                self.add(
                    "T04",
                    "P2",
                    "issue",
                    "verified",
                    check,
                    desc + ".",
                    t(
                        "写清材质, 尺寸, 颜色, 用途, 每款放多张图 (Stripe 网站清单首项是清楚的商品描述).",
                        "Describe material, size, colour and use, with several images (first item of Stripe's checklist).",
                    ),
                    "self",
                    ["raw/products-1.json"],
                )
            else:
                self.add("T04", "P2", "ok", "verified", check, desc + ".")
        # T05 trust badges
        trust = signals.snippet(
            f["home_text"],
            r"secure checkout|ssl secured|guaranteed safe checkout|norton secured|mcafee secure|trustedsite|安全支付",
        )
        if trust:
            self.add(
                "T05",
                "info",
                "ok",
                "self_claim",
                t("页面上的安全/认证标识", "Security / trust badges on the page"),
                t(
                    '首页写着 "%s"; 只放真实取得的认证.' % trust,
                    'The homepage says "%s"; show only badges you actually hold.'
                    % trust,
                ),
            )

    # -- E group (needs questionnaire) ---------------------------------
    def e_group(self):
        t, q = self.t, self.q
        if not self.has_q():
            self.add(
                "E00",
                "info",
                "unknown",
                "not_found",
                t("收款主体与通道", "Payee company and providers"),
                t(
                    "没有填收款问卷, 主体, 通道, 提现链路都没有检查.",
                    "No questionnaire answers, so company, providers and payout chain were not checked.",
                ),
            )
            return
        ec, gws = q.get("entity_country"), self.gw()
        # E01
        if ec == "CN" and "shopify_payments" in gws:
            self.add(
                "E01",
                "P1",
                "issue",
                "inferred",
                t(
                    "大陆主体使用 Shopify Payments 的条件",
                    "Shopify Payments for a mainland China company",
                ),
                t(
                    "官方写明: 大陆主体的 Shopify Payments 是早期访问, 仅受邀开通, 必须用 WorldFirst 作为唯一提现账户, 不支持卖给大陆境内买家 [已核实]; 你是否收到邀请 [查不到]; 没有邀请就要准备其他卡通道",
                    "Official: Shopify Payments for mainland China companies is invitation-only early access, needs WorldFirst as the only payout account and excludes buyers in mainland China [Verified]; whether you were invited [Not found]; without an invitation you need another card provider",
                ),
                t(
                    "在后台付款设置里看是否有邀请; 没有就按第三方通道规划, 并把第三方交易费算进成本.",
                    "Check the payments settings for an invitation; if none, plan for a third-party provider and count the third-party fee.",
                ),
                "self",
                ["S3", "S4"],
                basis=t(
                    "你说要用 Shopify Payments + 官方条件 (S3, S4)",
                    "you plan Shopify Payments + official conditions (S3, S4)",
                ),
            )
        # E02
        if ec == "CN" and "stripe" in gws:
            self.add(
                "E02",
                "P1",
                "issue",
                "conflicting",
                t("主体国家与通道支持国家", "Company country vs provider coverage"),
                t(
                    "你说主体在中国大陆且用 Stripe [你提供的]; Stripe 支持国家列表里没有中国大陆 (有香港) [已核实].",
                    "You said a mainland China company uses Stripe [You provided]; Stripe's supported countries do not include mainland China (Hong Kong is listed) [Verified].",
                ),
                t(
                    "确认 Stripe 账户实际注册在哪个国家/地区, 并让网站上的主体与它一致.",
                    "Confirm which country the Stripe account is registered in and make the site's company match it.",
                ),
                "self",
                ["S6", "questionnaire.json"],
            )
        # E03
        if q.get("payout_account") == "mercury":
            self.add(
                "E03",
                "P2",
                "issue",
                "inferred",
                t("美国银行账户的业务核验", "U.S. bank account business verification"),
                t(
                    "Mercury 官方帮助页 (2026-09 新增) 写明: 没有美国业务的公司可能被要求证明美国业务, 拿不出可能被锁户 [已核实]; 对你的影响要看你的材料",
                    "Mercury's help page (added 2026-09): companies without U.S. operations may be asked to prove them and accounts may be locked [Verified]; the effect on you depends on your documents",
                ),
                t(
                    "提前准备与账户流水对得上的美国业务材料.",
                    "Prepare U.S. business documents that match your account activity.",
                ),
                "self",
                ["S13"],
                basis=t(
                    "你填的提现账户 + 官方原文 (S13)",
                    "your payout account + official text (S13)",
                ),
            )
        # E04 payout chain
        payout = q.get("payout_account")
        check = t("从收款到提现的链路", "From payment to payout")
        if (
            ec == "CN"
            and "shopify_payments" in gws
            and payout
            and payout != "worldfirst"
        ):
            self.add(
                "E04",
                "P0",
                "issue",
                "conflicting",
                check,
                t(
                    "你说用 Shopify Payments, 提现账户是%s [你提供的]; 官方要求大陆主体只能提现到 WorldFirst [已核实]."
                    % self.name(payout),
                    "You plan Shopify Payments with payout account %s [You provided]; mainland China companies must pay out to WorldFirst [Verified]."
                    % self.name(payout),
                ),
                t(
                    "开通 WorldFirst 账户作为提现账户, 或换通道.",
                    "Open a WorldFirst account for payouts, or change provider.",
                ),
                "self",
                ["S4"],
            )
        elif payout == "none" and gws:
            self.add(
                "E04",
                "P0",
                "issue",
                "user_input",
                check,
                t(
                    "你说还没有提现账户 [你提供的], 收到的钱没地方取出.",
                    "You have no payout account yet [You provided], so funds cannot be withdrawn.",
                ),
                t(
                    "先开好提现账户 (银行或跨境收款账户) 再上线收款.",
                    "Open a payout account (bank or cross-border account) before taking payments.",
                ),
                "self",
                ["questionnaire.json"],
            )
        elif payout and gws:
            self.add(
                "E04",
                "P0",
                "ok",
                "user_input",
                check,
                t(
                    "你说的链路: %s → 提现到%s%s."
                    % (
                        self.names(gws),
                        self.name(payout),
                        ("; 提现币种 %s" % q["payout_currency"])
                        if q.get("payout_currency")
                        else "",
                    ),
                    "Your chain: %s -> payout to %s%s."
                    % (
                        self.names(gws),
                        self.name(payout),
                        ("; payout currency %s" % q["payout_currency"])
                        if q.get("payout_currency")
                        else "",
                    ),
                ),
            )
        elif gws:
            self.add(
                "E04",
                "P0",
                "unknown",
                "not_found",
                check,
                t(
                    "没说提现账户, 链路最后一环不清楚.",
                    "Payout account not given; the last step of the chain is unclear.",
                ),
            )
        # E05 single dependency
        distinct = set(g for g in gws if g != "manual")
        if gws:
            check = t("收款路径是否只有一条", "Only one way to get paid?")
            if len(distinct) <= 1:
                self.add(
                    "E05",
                    "P1",
                    "issue",
                    "inferred",
                    check,
                    t(
                        "只有一条收款路径 (%s), 这条路被暂停时就收不了款"
                        % (self.names(sorted(distinct)) or "-"),
                        "Only one payment route (%s); if it is paused you cannot take payments"
                        % (self.names(sorted(distinct)) or "-"),
                    ),
                    t(
                        "准备至少两条互不依赖的收款路径 (例如卡通道 + PayPal).",
                        "Keep at least two independent routes (e.g. a card provider + PayPal).",
                    ),
                    "self",
                    ["questionnaire.json"],
                    basis=t("你填的通道数", "the providers you listed"),
                )
            else:
                self.add(
                    "E05",
                    "P1",
                    "ok",
                    "user_input",
                    check,
                    t(
                        "有 %d 条收款路径: %s."
                        % (len(distinct), self.names(sorted(distinct))),
                        "%d routes: %s."
                        % (len(distinct), self.names(sorted(distinct))),
                    ),
                )
        # E06 restricted categories
        cat = q.get("category")
        if cat:
            hit = next((p for p in RESTRICTED_HINTS if re.search(p, cat, re.I)), None)
            check = t("品类与通道方限制清单", "Category vs providers' restricted lists")
            if hit:
                self.add(
                    "E06",
                    "P1",
                    "issue",
                    "inferred",
                    check,
                    t(
                        '你填的品类 "%s" 属于收款通道常设限制的类型, 是否受限要看通道方原文'
                        % cat,
                        'Your category "%s" is a type payment providers often restrict; their own list decides'
                        % cat,
                    ),
                    t(
                        "对照 Stripe 限制业务清单原文 (https://stripe.com/legal/restricted-businesses) 和其他通道的清单, 有疑问先问通道方.",
                        "Read Stripe's restricted businesses list (https://stripe.com/legal/restricted-businesses) and other providers' lists; ask them first if unsure.",
                    ),
                    "self",
                    ["S11"],
                    basis=t(
                        "品类关键词对照常见限制类别, 未逐条读原文",
                        "category keywords vs common restricted types; the list was not read item by item",
                    ),
                )
            else:
                self.add(
                    "E06",
                    "P1",
                    "ok",
                    "inferred",
                    check,
                    t(
                        '品类 "%s" 不在本工具的常见受限关键词里, 仍请自己对照原文'
                        % cat,
                        'Category "%s" matches none of this tool\'s restricted keywords; still check the provider lists'
                        % cat,
                    ),
                    basis=t(
                        "关键词对照, 不是通道方结论",
                        "keyword check, not a provider decision",
                    ),
                )
        # E07 MoR / VAT
        mix = q.get("market_mix") or {}
        eu = [m for m in mix if m in signals.EU_UK_MARKETS]
        if eu and q.get("mor") in (None, "no"):
            self.add(
                "E07",
                "P2",
                "issue",
                "inferred",
                t("卖到欧盟/英国的增值税", "VAT when selling to the EU / UK"),
                t(
                    "你要卖到 %s, 没用记录卖方 (代收代缴) 服务, 增值税要另外处理"
                    % ", ".join(eu),
                    "You sell to %s without a merchant-of-record service, so VAT has to be handled separately"
                    % ", ".join(eu),
                ),
                t(
                    "咨询税务专业人士; 本报告不算税.",
                    "Ask a tax professional; this report does not compute tax.",
                ),
                "self",
                ["questionnaire.json"],
                basis=t(
                    "你填的市场分布与记录卖方选项",
                    "your market mix and merchant-of-record answer",
                ),
            )

    def run(self):
        self.s_group()
        self.p_group()
        self.m_group()
        self.t_group()
        self.e_group()
        return sorted(self.out, key=sort_key)


def sort_key(fd):
    rank = {"issue": 0, "unknown": 1, "ok": 2, "skip": 3}
    return (
        GROUPS.index(fd["group"]) if fd["group"] in GROUPS else 9,
        fd["id"],
        rank.get(fd["result"], 9),
    )


def page_label(fname, lang):
    zh = lang == "zh"
    if fname.startswith("page-product"):
        return "商品页" if zh else "a product page"
    if fname.startswith("page-faq"):
        return "FAQ 页" if zh else "the FAQ page"
    if fname.startswith("homepage"):
        return "首页" if zh else "the homepage"
    if fname.startswith("page-contact"):
        return "联系页" if zh else "the contact page"
    return fname


def fmt(v):
    if v is None:
        return "?"
    return ("%.1f" % v).rstrip("0").rstrip(".")


def evaluate(facts, q, lang="zh"):
    return Rules(facts, q, lang).run()


def priority_list(findings):
    """Issues sorted by severity (P0 first), then id."""
    issues = [f for f in findings if f["result"] == "issue"]
    return sorted(issues, key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["id"]))


def counts(findings):
    out = {"P0": 0, "P1": 0, "P2": 0, "P3": 0, "unknown": 0}
    for f in findings:
        if f["result"] == "issue" and f["severity"] in out:
            out[f["severity"]] += 1
        elif f["result"] == "unknown":
            out["unknown"] += 1
    return out
