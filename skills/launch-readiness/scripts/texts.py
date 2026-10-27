"""Plain-language text for machine note codes, missing items and verdicts.
Python 3.8+, standard library only.
"""

NOTES = {
    "zh": {
        "exists_not_in_footer": "页面存在, 但首页源码里没有指向它的链接 (如果是脚本加载的链接, 请用浏览器确认)",
        "not_in_pasted_text": "你提供的文本里没有这一项",
        "homepage_unavailable": "首页没打开, 无法检查",
        "footer_link_not_read": "页脚有链接, 但那一页没读到",
        "no_page_no_footer_link": "政策网址打不开 (找不到页面), 首页页脚和页头也没有相应链接",
        "not_evaluated": "没有检查",
        "agent_reviewed": "人工复核后排除 (脚本误判)",
        "js_home": "首页内容靠脚本显示, 脚本读不到, 请用浏览器确认",
        "fetch_js_rendered": "页面内容靠脚本显示, 脚本读不到",
        "password_only": "店铺开着密码页, 只能看到密码页; 走粘贴模式或上线后重跑",
        "needs_live_site": "需要线上页面, 粘贴模式或店铺未公开时查不到",
        "needs_browser": "需要浏览器实测, 免费版不覆盖",
        "no_product_page": "没读到商品页",
        "no_return_text": "没读到退货相关文字",
        "not_stated": "没写",
        "no_legal_notice": "没有法律声明 (Impressum) 页",
        "password_page": "店铺开着密码页; 还没上线可以先不管, 上线时撤掉",
        "no_refund_policy": "没有退货政策",
        "no_shipping_policy": "没有运费政策",
        "no_privacy_policy": "没有隐私政策",
        "subscription_products_no_policy": "有订阅类商品, 没找到订阅政策",
        "no_annotations": "没有多语言网址标注",
        "metric_only": "只写了公制单位 (cm/kg)",
        "imperial_only": "只写了英制单位 (inch/lb)",
        "no_products": "网站地图里没有商品",
        "sitemap_not_found": "网站地图打不开",
        "sitemap_error": "网站地图打不开",
    },
    "en": {
        "exists_not_in_footer": "The page exists but the homepage footer does not link to it",
        "not_in_pasted_text": "Not in the text you provided",
        "homepage_unavailable": "Homepage not reachable",
        "footer_link_not_read": "Footer links to it, but the page could not be read",
        "no_page_no_footer_link": "Policy URL not found and no footer/header link",
        "not_evaluated": "Not checked",
        "agent_reviewed": "Excluded after manual review (false positive)",
        "js_home": "Homepage content is drawn by JavaScript and could not be read; check in a browser",
        "fetch_js_rendered": "Page content is drawn by JavaScript and could not be read",
        "password_only": "Only the password page is public; use paste mode or re-run after launch",
        "needs_live_site": "Needs the live site (not available in paste mode or behind a password)",
        "needs_browser": "Needs a browser test (not in the free version)",
        "no_product_page": "No product page read",
        "no_return_text": "No return wording read",
        "not_stated": "Not stated",
        "no_legal_notice": "No legal notice (Impressum) page",
        "password_page": "Store is password-protected; fine before launch, remove it when going live",
        "no_refund_policy": "No refund policy",
        "no_shipping_policy": "No shipping policy",
        "no_privacy_policy": "No privacy policy",
        "subscription_products_no_policy": "Subscription items found but no subscription policy",
        "no_annotations": "No language/region URL annotations",
        "metric_only": "Only metric units (cm/kg)",
        "imperial_only": "Only imperial units (inch/lb)",
        "no_products": "Sitemap lists no products",
        "sitemap_not_found": "Sitemap not reachable",
        "sitemap_error": "Sitemap not reachable",
    },
}
ITEMS = {
    "zh": {
        "window": "退货期",
        "start": "从哪天起算",
        "condition": "商品条件",
        "how": "怎么发起退货",
        "refund_time": "退款多久到账",
        "processing": "处理时长",
        "transit": "运输时长",
        "rates": "运费或包邮门槛",
        "tracking": "追踪号",
        "controller_contact": "主体与联系方式",
        "purposes": "用途",
        "recipients": "分享给谁",
        "transfer": "是否传往境外",
        "retention": "保存期限",
        "rights": "用户权利",
        "legal_basis": "法律依据",
        "phone": "电话",
        "address": "地址",
        "currency": "币种说明",
        "refund_terms": "退换条件",
        "email": "邮箱",
        "footer_link": "页脚链接",
    },
    "en": {
        "window": "return window",
        "start": "start date",
        "condition": "item condition",
        "how": "how to start a return",
        "refund_time": "refund timing",
        "processing": "processing time",
        "transit": "transit time",
        "rates": "rates or free-shipping threshold",
        "tracking": "tracking",
        "controller_contact": "controller and contact",
        "purposes": "purposes",
        "recipients": "recipients",
        "transfer": "transfers abroad",
        "retention": "retention",
        "rights": "user rights",
        "legal_basis": "legal basis",
        "phone": "phone",
        "address": "address",
        "currency": "currency",
        "refund_terms": "refund terms",
        "email": "email",
        "footer_link": "footer link",
    },
}
VERDICT = {
    "zh": {
        "go": "可以上线",
        "fix_p0": "改完 P0 再上线",
        "hold": "先别上线",
        None: "无法判断",
    },
    "en": {
        "go": "OK to launch",
        "fix_p0": "Fix the P0 items, then launch",
        "hold": "Do not launch yet",
        None: "Cannot tell",
    },
}
MISSING_LABEL = {"zh": "缺: ", "en": "Missing: "}


def note_text(note, lang="zh"):
    """Plain text for a note: known codes, or comma lists of missing items."""
    if not note:
        return ""
    table = NOTES[lang]
    if note in table:
        return table[note]
    parts = note.split(",")
    items = ITEMS[lang]
    if all(p in items for p in parts):
        sep = ", "
        return MISSING_LABEL[lang] + sep.join(items[p] for p in parts)
    return table.get(note, "")


SOURCE_NAMES = {
    "zh": {
        "policy:refund-policy": "退货政策",
        "policy:shipping-policy": "运费政策",
        "policy:privacy-policy": "隐私政策",
        "policy:terms-of-service": "服务条款",
        "policy:legal-notice": "法律声明",
        "policy:contact-information": "联系信息",
        "policy:subscription-policy": "订阅政策",
        "refund-policy": "退货政策",
        "shipping-policy": "运费政策",
        "privacy-policy": "隐私政策",
        "terms-of-service": "服务条款",
        "legal-notice": "法律声明",
        "faq": "常见问题",
        "announcement": "公告栏",
        "pdp": "商品页",
        "footer": "页脚",
        "home": "首页",
        "contact": "联系页",
        "about": "关于我们",
        "other": "其他页面",
        "return days": "退货天数",
        "tracking": "追踪代码",
        "sold out": "售罄",
        "copyright": "版权年份",
        "title": "网页标题",
        "social": "社媒链接",
        "store": "店铺币种",
    },
    "en": {
        "policy:refund-policy": "refund policy",
        "policy:shipping-policy": "shipping policy",
        "policy:privacy-policy": "privacy policy",
        "policy:terms-of-service": "terms of service",
        "policy:legal-notice": "legal notice",
        "policy:contact-information": "contact information",
        "announcement": "announcement bar",
        "pdp": "product page",
        "faq": "FAQ",
        "home": "homepage",
        "store": "store currency",
    },
}


def plain_evidence(text, lang="zh"):
    """'policy:refund-policy: We accept ...' -> '退货政策: We accept ...'."""
    import re

    names = SOURCE_NAMES.get(lang, {})
    m = re.match(r"^([a-z][a-z:\- ]{1,40}?): (.*)$", text or "", re.S)
    if m and m.group(1) in names:
        text = "%s: %s" % (names[m.group(1)], m.group(2))
    elif lang == "zh":
        text = re.sub(r"(\d+) chars$", r"只有 \1 个字符", text or "")
        text = re.sub(r"^copyright (\d{4})$", r"版权年份 \1", text)
        text = re.sub(r"~(\d+)% English$", r"约 \1% 是英文", text)
        text = re.sub(r"\(expected ([A-Z]{3})\)", r"(应为 \1)", text)
    return text


def plain_where(where, lang="zh"):
    if where and where.startswith("paste:"):
        name = where[len("paste:") :]
        return ("你提供的文件 %s" if lang == "zh" else "your file %s") % name
    return where
