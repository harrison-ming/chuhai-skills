"""Check catalogue texts (zh / en): items, fixes, labels, user-input asks.

Kept apart from the logic in audit.py so wording can change without touching
rules. references/checks.md is the human-readable twin of this file.
Python 3.8+, standard library only.
"""

PLATFORM = {
    "meta": ("Meta 像素", "Meta pixel"),
    "ga4": ("谷歌分析 (GA4)", "Google Analytics 4"),
    "google_ads": ("谷歌广告 (Google Ads)", "Google Ads"),
    "google_tag": ("谷歌代码 (看不出是分析还是广告)", "Google tag (product unclear)"),
    "gtm": ("谷歌代码管理器 (GTM)", "Google Tag Manager"),
    "tiktok": ("TikTok 像素", "TikTok pixel"),
    "pinterest": ("Pinterest 标签", "Pinterest tag"),
    "snap": ("Snap 像素", "Snap pixel"),
    "klaviyo": ("Klaviyo 站内追踪", "Klaviyo onsite tracking"),
    "microsoft": ("微软广告 (Bing) 标签", "Microsoft Ads (Bing) tag"),
    "reddit": ("Reddit 像素", "Reddit pixel"),
    "shopify": ("Shopify", "Shopify"),
    "all": ("全部", "All"),
}
# First mention in a report body gets a plain-language gloss.
GLOSS = {
    "meta": (
        "Meta 像素 (投放 Facebook/Instagram 广告用的追踪代码)",
        "Meta pixel (the tracking code for Facebook / Instagram ads)",
    ),
    "gtm": (
        "谷歌代码管理器 (GTM, 用来集中装各种追踪代码的容器)",
        "Google Tag Manager (GTM, a container that loads other tags)",
    ),
}
VIA = {
    "app_pixel": ("Shopify 应用像素 (装官方应用时自动登记)", "Shopify app pixel"),
    "custom_pixel": (
        "Shopify 自定义像素 (按名称推测)",
        "Shopify custom pixel (guessed from its name)",
    ),
    "theme_code": ("主题或页面里的代码", "code in the theme / page"),
    "gtm_container": ("谷歌代码管理器容器里", "inside a Google Tag Manager container"),
    "google_tag_script": ("谷歌代码脚本里", "inside a Google tag script"),
}
RESULT = {
    "pass": ("通过", "Pass"),
    "issue": ("有问题", "Issue"),
    "not_seen": ("没看到", "Not seen"),
    "needs_input": ("待你确认", "Needs your input"),
    "na": ("不适用", "N/A"),
}
SEVERITY = {
    "high": ("高", "High"),
    "medium": ("中", "Medium"),
    "low": ("低", "Low"),
    "info": ("提示", "Note"),
    "none": ("-", "-"),
}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3, "none": 4}
SOURCE = {
    "H": ("公开页面", "public pages"),
    "G": ("公开的谷歌代码容器", "public Google tag containers"),
    "U1": ("你提供的截图/回答", "your screenshots / answers"),
    "B": ("浏览器实测", "browser test"),
    "U2": ("你提供的导出文件", "your export files"),
}

# id -> (item zh, item en, fix zh, fix en)
CHECKS = {
    "T01": (
        "Meta 像素",
        "Meta pixel",
        "在 Shopify 装官方 Facebook & Instagram 应用连接像素, 不要再手贴代码.",
        "Connect the pixel through the official Facebook & Instagram app; do not paste code by hand.",
    ),
    "T02": (
        "谷歌分析 (GA4)",
        "Google Analytics 4",
        "用 Google & YouTube 应用或谷歌代码管理器装一处即可.",
        "Install it once, via the Google & YouTube app or Tag Manager.",
    ),
    "T03": (
        "谷歌代码管理器容器",
        "Google Tag Manager containers",
        "一个网站一个容器; 有多个时先问外包各管什么.",
        "One container per site; if there are several, ask whoever set them up what each does.",
    ),
    "T04": (
        "谷歌广告 (Google Ads)",
        "Google Ads",
        "成交转化要在结账里由应用像素发, 前台看不到是正常的, 请按清单提供谷歌广告转化诊断截图.",
        "Purchase conversions fire inside checkout, so the storefront cannot show them; send the Google Ads diagnostics screenshot.",
    ),
    "T05": (
        "TikTok 像素",
        "TikTok pixel",
        "装 TikTok 官方应用连接像素.",
        "Connect the pixel through the official TikTok app.",
    ),
    "T06": (
        "Pinterest 标签",
        "Pinterest tag",
        "装 Pinterest 官方应用连接标签.",
        "Connect the tag through the official Pinterest app.",
    ),
    "T07": ("Snap 像素", "Snap pixel", "-", "-"),
    "T08": (
        "Klaviyo 站内追踪",
        "Klaviyo onsite tracking",
        "在主题编辑器的 '应用嵌入' 里打开 Klaviyo.",
        "Turn Klaviyo on under App embeds in the theme editor.",
    ),
    "T09": (
        "Shopify 后台登记的像素 (应用像素/自定义像素)",
        "Pixels registered in Shopify (app / custom pixels)",
        "-",
        "-",
    ),
    "T10": (
        "追踪编号是否填了模板文字或格式不对",
        "Placeholder or malformed tracking IDs",
        "这段代码等于没装: 删掉, 或填上后台里的真实编号.",
        "This code does nothing: delete it or put in the real ID from the ad platform.",
    ),
    "T11": (
        "你在投的平台, 页面上是否都看得到",
        "Platforms you advertise on vs tags seen",
        "先把缺的平台补上 (用官方应用).",
        "Add the missing platforms first (official apps).",
    ),
    "D01": (
        "同一个编号装了两处以上 (会重复计数)",
        "Same ID installed in two or more places (double counting)",
        "只留一处 (优先官方应用), 其余删掉.",
        "Keep one install (prefer the official app) and remove the rest.",
    ),
    "D02": (
        "同一平台有多个不同编号",
        "Several different IDs for one platform",
        "问清哪个在用, 停用的删掉.",
        "Find out which one is in use and remove the others.",
    ),
    "D03": (
        "同一个谷歌分析编号同时由两种方式装了",
        "Same GA4 ID loaded two ways",
        "只留一处.",
        "Keep only one.",
    ),
    "D04": (
        "旧版谷歌分析 (UA) 代码残留",
        "Leftover Universal Analytics (UA) code",
        "旧版 2023 年起已不处理数据, 删掉即可.",
        "UA stopped processing data in 2023; remove it.",
    ),
    "D05": (
        "成交追踪是否还依赖已停用的结账 '附加脚本'",
        "Purchase tracking still relying on retired checkout additional scripts",
        "已只读或已被替换, 改用应用像素或自定义像素.",
        "Those scripts are read-only or replaced; move tracking to app or custom pixels.",
    ),
    "D06": (
        "自定义像素和应用像素是否装了同一个平台",
        "Custom pixel duplicating an app pixel",
        "同一平台只留一个, 删掉另一个.",
        "Keep one per platform and delete the other.",
    ),
    "C01": (
        "成交事件是否到达广告平台",
        "Purchase events reach the ad platform",
        "自己下一单测试单, 按清单截图.",
        "Place one test order yourself and take the screenshots on the list.",
    ),
    "C02": ("开始结账事件", "Checkout started event", "-", "-"),
    "S01": (
        "Meta 服务端回传 (转化接口) 在发",
        "Meta server-side events (Conversions API)",
        "在官方应用里把数据共享调到最高档 (档位名称以后台为准).",
        "Set data sharing to the highest level in the official app (exact level name per the admin).",
    ),
    "S02": (
        "Meta 浏览器和服务端去重",
        "Meta browser / server deduplication",
        "两边事件编号和事件名必须一致.",
        "Both sides must send the same event ID and event name.",
    ),
    "S03": (
        "TikTok 事件接口与去重",
        "TikTok Events API and deduplication",
        "两边事件编号和事件名必须一致.",
        "Both sides must send the same event ID and event name.",
    ),
    "S04": ("谷歌增强型转化", "Google enhanced conversions", "-", "-"),
    "S05": (
        "Pinterest / Snap 转化接口与去重",
        "Pinterest / Snap Conversions API and deduplication",
        "-",
        "-",
    ),
    "P01": (
        "面向欧盟/英国: 同意横幅",
        "EU / UK visitors: consent banner",
        "用 Shopify 自带横幅或已对接 Shopify 隐私接口的横幅应用, 两边地区设置一致.",
        "Use Shopify's banner or a banner app connected to Shopify's privacy API, with matching regions.",
    ),
    "P02": (
        "谷歌同意模式信号",
        "Google consent mode signals",
        "已用同意模式的要升级到第 2 版 (含两个新的广告参数).",
        "If you use consent mode, upgrade to v2 (two new ad parameters).",
    ),
    "P04": (
        "面向美国: '你的隐私选择' / 退出链接",
        "US visitors: 'Your Privacy Choices' / opt-out link",
        "用 Shopify 的退出页, 它能自动响应浏览器的退出信号.",
        "Use Shopify's opt-out page; it can honour the browser opt-out signal automatically.",
    ),
    "P05": (
        "面向加拿大: 隐私政策写明广告追踪目的与退出方式",
        "Canada: privacy policy explains ad tracking and opt-out",
        "在隐私政策里写清用途和怎么退出.",
        "State the purpose and how to opt out in the privacy policy.",
    ),
    "P06": (
        "隐私政策是否提到实际在用的广告/分析平台",
        "Privacy policy names the ad / analytics platforms in use",
        "把实际在用的平台写进隐私政策.",
        "List the platforms you actually use in the privacy policy.",
    ),
    "G01": ("网站平台识别", "Store platform", "-", "-"),
    "G02": (
        "页面是否被拦截",
        "Pages blocked",
        "换时间重试, 或用浏览器实测.",
        "Retry later or use a browser test.",
    ),
}

# check id -> T platform
T_PLATFORM = {
    "T01": "meta",
    "T02": "ga4",
    "T04": "google_ads",
    "T05": "tiktok",
    "T06": "pinterest",
    "T07": "snap",
    "T08": "klaviyo",
}

# User inputs (U1): id -> (what zh, what en, proves zh, proves en)
USER_INPUTS = {
    "U1-a": (
        "Shopify 后台 '设置 → 客户事件' 列表截图",
        "Shopify admin Settings → Customer events list",
        "装了哪些应用像素/自定义像素, 是否已连接",
        "Which app / custom pixels exist and are connected",
    ),
    "U1-b": (
        "Meta 事件管理工具 → 你的像素 → 概览截图 (含连接方式)",
        "Meta Events Manager → your pixel → Overview (with connection method)",
        "每个事件来自浏览器还是服务器",
        "Whether each event comes from browser or server",
    ),
    "U1-c": (
        "自己下一单测试单后, Meta 事件管理工具 → 测试事件截图",
        "Meta Events Manager → Test events, after your own test order",
        "成交事件是否同时有浏览器和服务器, 是否去重",
        "Purchase from browser and server, deduplicated",
    ),
    "U1-d": (
        "谷歌分析 → 管理 → 调试视图 (或实时报告) 截图, 走到下单",
        "GA4 → Admin → DebugView (or Realtime) while you go through to an order",
        "成交事件是否到达, 参数是否齐",
        "Purchase arrives with its parameters",
    ),
    "U1-e": (
        "谷歌广告 → 目标 → 转化 → 诊断页截图",
        "Google Ads → Goals → Conversions → Diagnostics",
        "增强型转化状态",
        "Enhanced conversions status",
    ),
    "U1-f": (
        "TikTok 事件管理工具 → 测试事件 / 诊断截图",
        "TikTok Events Manager → Test events / Diagnostics",
        "像素和事件接口是否都在, 是否去重",
        "Pixel and Events API both present, deduplicated",
    ),
    "U1-g": (
        "Shopify 后台 '设置 → 结账' 里 '附加脚本' 一栏截图, 以及感谢页是否已升级",
        "Shopify Settings → Checkout: additional scripts box, and whether the thank-you page is upgraded",
        "旧成交代码是否还依赖已停用的入口",
        "Whether old purchase code relies on a retired entry point",
    ),
    "U1-h": (
        "Shopify 后台 '设置 → 客户隐私' (Cookie 横幅) 截图",
        "Shopify Settings → Customer privacy (cookie banner)",
        "横幅地区, 退出页",
        "Banner regions and opt-out page",
    ),
    "U1-i": (
        "Pinterest / Snap 广告后台的转化接口状态截图",
        "Pinterest / Snap ads admin: Conversions API status",
        "服务端回传与去重",
        "Server events and deduplication",
    ),
}
# check id -> user input that answers it
CHECK_INPUT = {
    "D05": "U1-g",
    "D06": "U1-a",
    "C02": "U1-a",
    "S01": "U1-b",
    "S02": "U1-c",
    "S03": "U1-f",
    "S04": "U1-e",
    "S05": "U1-i",
    "T09": "U1-a",
    "P01": "U1-h",
}
C01_INPUT = {"meta": "U1-c", "ga4": "U1-d", "tiktok": "U1-f"}


def pick(pair, lang):
    return pair[0] if lang == "zh" else pair[1]
