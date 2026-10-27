"""Render diff.json / baseline data into the agent's draft and CSV rows.

``diff_md(diff, ctx)`` writes the data draft ``diff.md``: it already follows
references/report-template.<lang>.md section by section, with badges, so the
agent copies it to report.md, writes the one placeholder section (signals to
watch, always [Inferred] + basis) and tightens the wording. Everything here
is deterministic and offline.

``ctx`` keys: lang, next_due (YYYY-MM-DD), demo (bool), locators (bool).
Python 3.8+, standard library only.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import evidence  # noqa: E402

BADGE_KEY = {
    "verified": "verified",
    "page_shown": "on_page",
    "store_claim": "self_claim",
}
POLICY_LABEL = {
    "zh": {
        "refund-policy": "退款政策",
        "shipping-policy": "运费政策",
        "privacy-policy": "隐私政策",
        "terms-of-service": "服务条款",
        "contact-information": "联系方式",
    },
    "en": {
        "refund-policy": "Refund policy",
        "shipping-policy": "Shipping policy",
        "privacy-policy": "Privacy policy",
        "terms-of-service": "Terms of service",
        "contact-information": "Contact information",
    },
}
TERM_LABEL = {
    "zh": {
        "return_days": ("退货期", "%s 天"),
        "free_shipping_over": ("免运费门槛", "满 %s"),
        "processing_days": ("处理时效", "%s 个工作日"),
        "restocking_fee_pct": ("补货费", "%s%%"),
        "return_shipping_paid_by": ("退货运费谁付", "%s"),
    },
    "en": {
        "return_days": ("Return window", "%s days"),
        "free_shipping_over": ("Free shipping over", "%s"),
        "processing_days": ("Processing time", "%s business days"),
        "restocking_fee_pct": ("Restocking fee", "%s%%"),
        "return_shipping_paid_by": ("Return shipping paid by", "%s"),
    },
}
PAID_BY = {
    "zh": {"customer": "顾客", "store": "店铺"},
    "en": {"customer": "customer", "store": "store"},
}
TYPE_LABEL = {
    "zh": {
        "store_status_changed": "店铺状态变化",
        "currency_changed": "基础币种变化",
        "price_sitewide_shift": "全店统一调价",
        "product_added": "上新",
        "product_removed": "下架",
        "price_down": "降价",
        "price_up": "涨价",
        "promo_started": "开始打折",
        "promo_ended": "结束打折",
        "discount_deeper": "折扣加深",
        "discount_shallower": "折扣变浅",
        "compare_at_raised": "划线价上调",
        "sold_out": "售罄",
        "restocked": "补货",
        "variant_added": "新增规格",
        "variant_removed": "移除规格",
        "policy_changed": "政策改动",
        "policy_added": "新增政策页",
        "policy_removed": "政策页消失",
        "theme_changed": "换了主题",
        "theme_updated": "主题升级版本",
        "app_signal_added": "新出现应用痕迹",
        "app_signal_removed": "应用痕迹消失",
        "tracking_added": "新增追踪代码",
        "tracking_removed": "追踪代码消失",
        "markets_changed": "多国站点或配送变化",
        "homepage_promo_changed": "首页促销文案变化",
        "homepage_text_changed": "首页文字变化",
        "collection_added": "新增商品分类",
        "collection_removed": "移除商品分类",
        "collection_renamed": "商品分类改名",
        "product_republished": "重新发布",
        "product_handle_changed": "改了商品网址",
        "product_relisted": "删除后重建",
        "product_surfaced": "新出现 (之前没看到)",
        "product_missing_unconfirmed": "疑似缺失 (未确认下架)",
    },
    "en": {
        "store_status_changed": "Store status changed",
        "currency_changed": "Base currency changed",
        "price_sitewide_shift": "Store-wide price change",
        "product_added": "New product",
        "product_removed": "Removed",
        "price_down": "Price cut",
        "price_up": "Price rise",
        "promo_started": "Discount started",
        "promo_ended": "Discount ended",
        "discount_deeper": "Deeper discount",
        "discount_shallower": "Smaller discount",
        "compare_at_raised": "Compare-at price raised",
        "sold_out": "Sold out",
        "restocked": "Back in stock",
        "variant_added": "Option added",
        "variant_removed": "Option removed",
        "policy_changed": "Policy changed",
        "policy_added": "Policy page added",
        "policy_removed": "Policy page gone",
        "theme_changed": "Theme changed",
        "theme_updated": "Theme version update",
        "app_signal_added": "New app trace",
        "app_signal_removed": "App trace gone",
        "tracking_added": "Tracking code added",
        "tracking_removed": "Tracking code gone",
        "markets_changed": "Country sites or shipping changed",
        "homepage_promo_changed": "Homepage promo text changed",
        "homepage_text_changed": "Homepage text changed",
        "collection_added": "Collection added",
        "collection_removed": "Collection removed",
        "collection_renamed": "Collection renamed",
        "product_republished": "Republished",
        "product_handle_changed": "Product URL changed",
        "product_relisted": "Deleted and recreated",
        "product_surfaced": "Newly seen (not new)",
        "product_missing_unconfirmed": "Missing (removal not confirmed)",
    },
}
SEVERITY_LABEL = {
    "zh": {"high": "高", "medium": "中", "low": "低"},
    "en": {"high": "high", "medium": "medium", "low": "low"},
}
SOURCE_LABEL = {
    "zh": {
        "live": "直接读取店铺",
        "archive": "网页历史存档",
        "teardown_import": "之前的对标拆解",
    },
    "en": {
        "live": "read directly from the store",
        "archive": "web archive",
        "teardown_import": "earlier store teardown",
    },
}
STATUS_LABEL = {
    "zh": {
        "complete": "完整",
        "partial": "部分",
        "blocked": "被拦或打不开",
        "not_shopify": "不是 Shopify 店",
    },
    "en": {
        "complete": "complete",
        "partial": "partial",
        "blocked": "blocked or unreachable",
        "not_shopify": "not a Shopify store",
    },
}
WARNING_TEXT = {
    "zh": {
        "mass_removal_suspect": "这次看不到的商品超过三成, 更像是数据没读全, 不判断下架; 建议过一两天再抓一次.",
        "baseline_incomplete": "上一份快照没读全商品, 这次 '多出来' 的商品里有一部分可能早就在卖.",
        "interval_short": "两次间隔不到一天, 变化少属正常.",
        "interval_long": "两次间隔超过 45 天, 中间来回的变化看不到.",
        "archive_baseline": "旧的一份来自网页历史存档: 只有部分商品, 不判断下架, 相关变化最高标 [页面显示].",
        "currency_source_mismatch": "两次读到的币种来源不同 (一次来自店铺设置, 一次来自页面), 币种是否变了无法确认.",
        "prices_skipped_currency_changed": "基础币种变了, 这一期跳过所有价格比较.",
        "ambiguous_identity": "有几款商品对不上号 (同名多款), 没有强行配对.",
        "snapshots_out_of_order": "选的 '旧' 快照比 '新' 快照还晚, 请检查日期.",
        "market_mismatch": "两次看到的是这家店不同国家的站点版本 (例如一次是美国站, 一次是加拿大站), 首页文案, 主题痕迹和政策会因国家不同而不同, 这一期不比较这几项.",
    },
    "en": {
        "mass_removal_suspect": "Over 30% of products are missing this time; this looks like incomplete data, so no removals are reported. Capture again in a day or two.",
        "baseline_incomplete": "The older snapshot did not read every product, so some 'new' items may have been on sale already.",
        "interval_short": "Less than a day between the two captures; few changes are normal.",
        "interval_long": "More than 45 days between captures; back-and-forth changes in between are invisible.",
        "archive_baseline": "The older capture comes from the web archive: only part of the catalog, no removals judged, at most [Shown on page].",
        "currency_source_mismatch": "The currency came from different places in the two captures, so a currency change cannot be confirmed.",
        "prices_skipped_currency_changed": "The base currency changed, so prices were not compared this time.",
        "ambiguous_identity": "Some products could not be matched one-to-one (same names), so they were not paired.",
        "snapshots_out_of_order": "The 'old' snapshot is later than the 'new' one; check the dates.",
        "market_mismatch": "The two captures saw different country versions of the store (e.g. the US site once, the Canadian site once); homepage text, page traces and policies differ by country, so they were not compared.",
    },
}

T = {
    "zh": {
        "title": "# %s 竞品变化报告",
        "title_base": "# %s 竞品基线报告",
        "sub": "<sub>%s · 比较 %s 至 %s · 间隔 %s</sub>",
        "sub_base": "<sub>%s · 快照日期 %s</sub>",
        "demo": "> 演示数据, 非真实店铺. 用来看报告长什么样.",
        "demo_tag": "演示数据",
        "glance": "## 一页看懂",
        "range": "- 比较区间: %s 到 %s (间隔 %s). 旧的一份: %s; 新的一份: %s.",
        "counts": "- 这一期: 上新 %d 款, 下架 %s 款, 降价 %d 款, 涨价 %d 款, 开始打折 %d 款 %s",
        "removed_na": "未判断",
        "nothing": "- 本期无变化: 两次看到的商品, 价格, 折扣, 首页促销和政策都一样 %s",
        "top": "- 最重要的变化:",
        "top_none": "- 没有值得单列的变化 (只有首页文字等小改动, 见下文) %s",
        "products_na": "- 这一期商品没法比 (%s 没有商品数据), 只比首页, 主题, 多国站点和政策 [查不到]",
        "products_na_text": "这一期商品没法比 [查不到]",
        "price_na": "这一期价格没法比 [查不到]",
        "next": '- 下次建议 %s 再跑: 对 AI 说 "再看一下 %s 有什么变化".',
        "price_h": "## 价格变动",
        "price_skip": "两次的基础币种不同 (%s → %s), 价格不能直接比, 这一期跳过价格比较 %s",
        "price_none": "这一期没有看到价格变化 %s",
        "price_sitewide": "全店统一调价: %d 款商品 (共 %d 款可比) 同时变动约 %s%% %s (%s)",
        "price_table": "| 商品 | 原价 | 现价 | 变化 | 现在打折吗 | 事件 |",
        "price_small": "另有 %d 款小幅调价 (幅度低于 %s%%), 明细见 价格变动.csv.",
        "on_sale_yes": "在打折, %s%%",
        "on_sale_no": "没有",
        "new_h": "## 上新与下架",
        "new_sub": "### 新品",
        "new_table": "| 商品 | 类型 | 价格 | 发布日期 | 事件 |",
        "new_none": "这一期没有上新 %s",
        "surfaced": "另有 %d 款这次才看到, 但发布日期早于上一次 (之前没抓到或没公开), 不算上新, 列在 上新下架.csv.",
        "removed_sub": "### 下架",
        "removed_table": "| 商品 | 上次价格 | 事件 |",
        "removed_none": "这一期没有下架 %s",
        "removed_na_text": "这一期不判断下架: %s 有 %d 款这次没看到, 列在 上新下架.csv 的 '疑似缺失' %s",
        "removed_na_why": "这次商品数据没读全.",
        "removed_na_archive": "旧的一份来自网页历史存档, 只含部分商品.",
        "stock_sub": "### 售罄与补货",
        "stock_line": "- %s: %s %s (%s)",
        "variant_sub": "### 新增或移除的规格",
        "cadence_sub": "### 上新节奏",
        "cadence_line": "- 区间内上新 %d 款, 折合每周 %s 款 %s",
        "cadence_na": "- 间隔不到一天, 不折算每周上新数.",
        "month_table": "| 月份 | 上次看到的发布数 | 这次看到的发布数 |",
        "promo_h": "## 促销节奏",
        "promo_line": "- %s: %s (%s) %s",
        "promo_share": "- 在打折商品占比: %s → %s %s",
        "promo_none": "- 这一期没有商品开始或结束打折 %s",
        "home_added": "- 首页新出现的促销文案: %s %s",
        "home_removed": "- 首页不再出现的促销文案: %s %s",
        "home_same": "- 首页促销文案没有变化 %s",
        "home_text": "- 首页其他文字有 %d 行新增, %d 行消失 (明细在附录) %s",
        "promo_note": "- 只有两次快照, 只能看到状态变化 (开始, 结束, 加深), 看不出每次打折持续多久.",
        "tech_h": "## 技术与市场变化",
        "tech_none": "两次看到的主题, 应用痕迹, 追踪代码, 币种和多国站点都没有变化 %s",
        "theme": "- 主题: %s → %s %s",
        "app_add": "- 源码里新出现 %s 的痕迹 (痕迹不等于在用) %s",
        "app_rem": "- 源码里不再出现 %s 的痕迹 %s",
        "track_add": "- 新增追踪代码: %s %s",
        "track_rem": "- 不再看到追踪代码: %s %s",
        "currency": "- 基础币种: %s → %s %s",
        "ships": "- 可配送国家数: %s → %s %s",
        "hreflang": "- 多国站点 (语言/地区) 列表: 新增 %s, 去掉 %s %s",
        "localization": "- 国家/币种切换器: %s → %s %s",
        "coll": "- 商品分类: %s %s",
        "policy_h": "## 政策变化",
        "policy_none": "两次都读到的政策页 (%s) 没有实质改动 %s",
        "policy_na": "政策页这一期没有比较 (两次没有都读到, 或看到的是不同国家的版本) [查不到]",
        "tech_na": "首页, 主题和应用痕迹这一期没有比较 (首页没有都读到, 或看到的是不同国家的版本) [查不到]",
        "policy_term": "- %s, %s: %s → %s %s",
        "policy_text": "- %s 文字有改动: 新增 %d 句, 删掉 %d 句, 改写 %d 句 %s",
        "policy_add": '  - 新增: "%s"',
        "policy_rem": '  - 删掉: "%s"',
        "policy_rew": '  - 改写: "%s" → "%s"',
        "policy_page_add": "- 新出现 %s 页 %s",
        "policy_page_rem": "- %s 页不见了 %s",
        "signals_h": "## 需要关注的信号",
        "signals_ph": "<待写: 3-5 条, 每条写 动作 (今晚 / 本周 / 观察) + 依据的事件编号 + [推测] 依据: ... 不写销量和利润判断. 可参考的事件: %s>",
        "gaps_h": "## 还有哪些没查清",
        "gap_dim": "- %s 这一期没法比 [查不到]. 拿不到不等于没变.",
        "dim": {
            "products": "商品",
            "prices": "价格",
            "removals": "下架",
            "homepage": "首页",
            "collections": "商品分类",
            "markets": "多国站点与配送",
        },
        "gap_none": "- 主要维度两次都读到了. 两次之间来回的变化 (例如先降后涨) 看不到.",
        "appendix_h": "## 附录: 口径与数据来源",
        "status": "- 完成状态: %s",
        "snap_table": "| 快照 | 抓取时间 (UTC) | 来源 | 状态 | 商品数 | 翻页到底 |",
        "yes": "是",
        "no": "否",
        "method": [
            "- 价格是店铺基础币种的公开标价, 不是各国结账价. 商品级价格 = 各规格里的最低价.",
            "- 一页看懂和价格表只列幅度不低于 %s%% 的调价; 全部调价在 价格变动.csv.",
            "- 折扣深度 = 1 - 售价 / 划线价; 加深或变浅不少于 %s 个百分点才记.",
            "- 下架只在新快照翻页到底时判断; 一次少了三成以上的商品时改记 '疑似缺失'.",
            "- 上新 = 发布日期晚于上一次快照; 发布更早但上次没看到的记 '新出现'.",
            "- 运费保障, 礼品卡等虚拟商品不计入统计, 只进 全部变化.csv.",
            "- 应用只写源码里的痕迹, 痕迹不等于在用; 支付图标不等于已开通.",
        ],
        "warnings": "- warnings 原值: %s",
        "events_h": "### 事件编号对照",
        "events_table": "| 编号 | 变化 | 重要程度 | 对象 | 之前 | 之后 | 证据 | 原始数据位置 |",
        "events_more": "另有 %d 条见 全部变化.csv.",
        "home_detail_h": "### 首页文字变化明细",
        "policy_detail_h": "### 政策句子变化明细",
        "tech_terms": "- 技术词: 快照 = 某一时刻存下来的店铺公开数据 (snapshot.json); 商品数据来自店铺公开的 products.json, 划线价即 compare_at_price; 事件明细在 diff.json.",
        "na": "无",
        "base_glance1": "- 这是 %s 的第一份快照, 还没有更早的可以比, 所以这期是基线报告, 不是变化报告.",
        "base_counts": "- 现在公开在卖 %d 款商品%s; 近 30 天发布 %d 款, 近 90 天发布 %d 款 %s",
        "base_virtual": " (另有 %d 款运费保障等虚拟商品未计入)",
        "base_sale": "- 在打折商品占比 %s %s",
        "base_promo": "- 首页促销文案: %s %s",
        "base_promo_none": "- 首页没有看到促销文案 %s",
        "base_next": '- 下次建议 %s 再跑, 才能看到变化: 对 AI 说 "再看一下 %s 有什么变化".',
        "base_products_h": "## 当前商品与价格",
        "base_price": "- 各商品最低价: 最低 %s, 中位 %s, 最高 %s %s %s",
        "base_types": "- 商品类型前几名: %s %s",
        "base_recent_h": "### 近 30 天发布的商品",
        "base_recent_none": "近 30 天没有发布新商品 %s",
        "base_tech_h": "## 技术与市场现状",
        "base_theme": "- 主题: %s %s",
        "base_apps": "- 源码里有这些应用的痕迹 (痕迹不等于在用): %s %s",
        "base_track": "- 追踪代码: %s %s",
        "base_ships": "- 基础币种 %s, 可配送国家 %s 个 %s",
        "base_policy_h": "## 政策要点",
        "base_policy_line": "- %s: %s %s",
        "base_policy_none": "没有读到政策页的关键条款 [查不到]",
        "base_gap": "- 只有一份快照, 看不到任何变化. 价格, 上新, 促销的变化要等下一次快照.",
        "base_archive_na": "- 也试了网页历史存档里一个月前的版本, 这次没取到可用的数据 (存档网站限流, 暂时下线或没有收录) %s",
        "fail_glance": "- 这次没能读到 %s 的店铺数据: %s [查不到]",
        "fail_why": {
            "blocked": "网站打不开, 或者拦住了自动读取",
            "network": "连不上这个网址 (无法解析域名或网络不通): 可能网址不存在, 也可能是当前运行环境不能联网, 不代表店铺本身有问题",
            "unreachable": "这个网址打不开, 而其他网站能连上: 多半是网址不存在或网站本身出了问题",
            "not_shopify": "它看起来不是 Shopify 店, 本技能只支持 Shopify 店",
        },
        "fail_next": "- 没有可比的数据, 所以没有变化结论. 确认网址后可以再试一次; 下次建议 %s 再跑.",
        "fail_gap": "- 店铺商品, 价格, 促销和政策都没读到 [查不到]. 拿不到不等于没有.",
    },
}

T["en"] = {
    "title": "# %s competitor change report",
    "title_base": "# %s competitor baseline report",
    "sub": "<sub>%s · compared %s to %s · %s apart</sub>",
    "sub_base": "<sub>%s · snapshot of %s</sub>",
    "demo": "> Demo data, not a real store. It shows what the report looks like.",
    "demo_tag": "demo data",
    "glance": "## At a glance",
    "range": "- Period: %s to %s (%s apart). Older capture: %s; newer capture: %s.",
    "counts": "- This period: %d new, %s removed, %d price cuts, %d price rises, %d discounts started %s",
    "removed_na": "not judged",
    "nothing": "- No changes: products, prices, discounts, homepage promos and policies are the same in both captures %s",
    "top": "- Most important changes:",
    "top_none": "- Nothing worth listing on its own (only minor homepage text and the like, see below) %s",
    "products_na": "- Products could not be compared this time (no product data in %s); only homepage, theme, country sites and policies were compared [Not found]",
    "products_na_text": "Products could not be compared this time [Not found]",
    "price_na": "Prices could not be compared this time [Not found]",
    "next": '- Next suggested run: %s. Tell the assistant "check %s for changes again".',
    "price_h": "## Price changes",
    "price_skip": "The base currency changed (%s → %s), so prices cannot be compared this time %s",
    "price_none": "No price changes this period %s",
    "price_sitewide": "Store-wide price change: %d products (of %d comparable) moved by about %s%% %s (%s)",
    "price_table": "| Product | Old price | New price | Change | On sale now | Event |",
    "price_small": "%d more products changed by less than %s%%; see price-changes.csv.",
    "on_sale_yes": "yes, %s%%",
    "on_sale_no": "no",
    "new_h": "## New and removed products",
    "new_sub": "### New products",
    "new_table": "| Product | Type | Price | Published | Event |",
    "new_none": "No new products this period %s",
    "surfaced": "%d more product(s) appear for the first time but were published before the last capture (missed or hidden before); they are not counted as new and are listed in new-and-removed.csv.",
    "removed_sub": "### Removed",
    "removed_table": "| Product | Last price | Event |",
    "removed_none": "No removals this period %s",
    "removed_na_text": "Removals are not judged this period: %s %d products were not seen this time and are listed as 'missing' in new-and-removed.csv %s",
    "removed_na_why": "the product data was not read completely this time.",
    "removed_na_archive": "the older capture comes from the web archive and holds only part of the catalog.",
    "stock_sub": "### Sold out and back in stock",
    "stock_line": "- %s: %s %s (%s)",
    "variant_sub": "### Options added or removed",
    "cadence_sub": "### Launch pace",
    "cadence_line": "- %d new products in the period, about %s per week %s",
    "cadence_na": "- Less than a day apart; no weekly rate.",
    "month_table": "| Month | Published (older capture) | Published (newer capture) |",
    "promo_h": "## Promotions",
    "promo_line": "- %s: %s (%s) %s",
    "promo_share": "- Share of products on sale: %s → %s %s",
    "promo_none": "- No product started or ended a discount %s",
    "home_added": "- New homepage promo text: %s %s",
    "home_removed": "- Homepage promo text no longer shown: %s %s",
    "home_same": "- Homepage promo text unchanged %s",
    "home_text": "- Other homepage text: %d lines added, %d removed (details in the appendix) %s",
    "promo_note": "- With two captures we only see state changes (started, ended, deeper), not how long a discount lasted.",
    "tech_h": "## Tech and market changes",
    "tech_none": "Theme, app traces, tracking codes, currency and country sites are unchanged %s",
    "theme": "- Theme: %s → %s %s",
    "app_add": "- New trace of %s in the page source (a trace is not proof of use) %s",
    "app_rem": "- Trace of %s no longer in the page source %s",
    "track_add": "- Tracking code added: %s %s",
    "track_rem": "- Tracking code no longer seen: %s %s",
    "currency": "- Base currency: %s → %s %s",
    "ships": "- Countries it ships to: %s → %s %s",
    "hreflang": "- Country / language sites: added %s, removed %s %s",
    "localization": "- Country / currency selector: %s → %s %s",
    "coll": "- Collections: %s %s",
    "policy_h": "## Policy changes",
    "policy_none": "Policy pages read in both captures (%s) have no material change %s",
    "policy_na": "Policy pages were not compared this time (not read in both captures, or different country versions) [Not found]",
    "tech_na": "Homepage, theme and app traces were not compared this time (homepage not read in both captures, or different country versions) [Not found]",
    "policy_term": "- %s, %s: %s → %s %s",
    "policy_text": "- %s text changed: %d sentences added, %d removed, %d rewritten %s",
    "policy_add": '  - Added: "%s"',
    "policy_rem": '  - Removed: "%s"',
    "policy_rew": '  - Rewritten: "%s" → "%s"',
    "policy_page_add": "- New %s page %s",
    "policy_page_rem": "- %s page is gone %s",
    "signals_h": "## Signals to watch",
    "signals_ph": "<to be written: 3-5 items, each an action (tonight / this week / watch) + the event ids + [Inferred] Basis: ... No sales or profit claims. Events to consider: %s>",
    "gaps_h": "## What we could not check",
    "gap_dim": "- %s could not be compared this time [Not found]. Missing data is not the same as no change.",
    "dim": {
        "products": "products",
        "prices": "prices",
        "removals": "removals",
        "homepage": "homepage",
        "collections": "collections",
        "markets": "country sites and shipping",
    },
    "gap_none": "- The main areas were read both times. Changes that went back and forth between the two captures are invisible.",
    "appendix_h": "## Appendix: method and sources",
    "status": "- Status: %s",
    "snap_table": "| Snapshot | Captured (UTC) | Source | State | Products | All pages read |",
    "yes": "yes",
    "no": "no",
    "method": [
        "- Prices are public list prices in the store's base currency, not checkout prices per country. Product price = lowest option price.",
        "- At a glance and the price table only list changes of at least %s%%; every change is in price-changes.csv.",
        "- Discount depth = 1 - price / compare-at price; a change counts from %s percentage points.",
        "- Removals are only judged when the newer capture read every page; if over 30% vanish at once they are marked 'missing' instead.",
        "- New = published after the older capture; older items seen for the first time are 'newly seen'.",
        "- Shipping protection, gift cards and similar add-ons are left out of the counts and only appear in all-changes.csv.",
        "- Apps are traces in the page source, not proof of use; payment icons are not proof of an enabled method.",
    ],
    "warnings": "- warnings (raw): %s",
    "events_h": "### Event ids",
    "events_table": "| Id | Change | Severity | Item | Before | After | Evidence | Raw data |",
    "events_more": "%d more in all-changes.csv.",
    "home_detail_h": "### Homepage text details",
    "policy_detail_h": "### Policy sentence details",
    "tech_terms": "- Technical terms: a snapshot is the store's public data saved at one moment (snapshot.json); product data comes from the store's public products.json, compare-at price is compare_at_price; event details are in diff.json.",
    "na": "none",
    "base_glance1": "- This is the first snapshot of %s; there is nothing older to compare with yet, so this is a baseline report, not a change report.",
    "base_counts": "- %d products on public sale now%s; %d published in the last 30 days, %d in the last 90 days %s",
    "base_virtual": " (plus %d add-ons such as shipping protection, not counted)",
    "base_sale": "- Share of products on sale: %s %s",
    "base_promo": "- Homepage promo text: %s %s",
    "base_promo_none": "- No promo text seen on the homepage %s",
    "base_next": '- Next suggested run: %s, to see changes. Tell the assistant "check %s for changes again".',
    "base_products_h": "## Current products and prices",
    "base_price": "- Lowest price per product: min %s, median %s, max %s %s %s",
    "base_types": "- Top product types: %s %s",
    "base_recent_h": "### Published in the last 30 days",
    "base_recent_none": "Nothing published in the last 30 days %s",
    "base_tech_h": "## Tech and markets today",
    "base_theme": "- Theme: %s %s",
    "base_apps": "- App traces in the page source (a trace is not proof of use): %s %s",
    "base_track": "- Tracking codes: %s %s",
    "base_ships": "- Base currency %s, ships to %s countries %s",
    "base_policy_h": "## Policy key terms",
    "base_policy_line": "- %s: %s %s",
    "base_policy_none": "No policy key terms could be read [Not found]",
    "base_gap": "- One snapshot shows no changes. Price, launch and promo changes need the next snapshot.",
    "base_archive_na": "- We also tried the web archive's copy from about a month ago and got no usable data this time (rate limited, offline or not archived) %s",
    "fail_glance": "- The store data of %s could not be read this time: %s [Not found]",
    "fail_why": {
        "blocked": "the site did not open or blocked automated reading",
        "network": "the address could not be reached (name not resolved or no network): the address may not exist, or this environment has no internet; it says nothing about the store itself",
        "unreachable": "this address does not open while other sites do: most likely the address does not exist or the site itself has a problem",
        "not_shopify": "it does not look like a Shopify store, and this skill only supports Shopify",
    },
    "fail_next": "- No comparable data, so no change findings. Check the address and try again; next suggested run: %s.",
    "fail_gap": "- Products, prices, promotions and policies were not read [Not found]. Missing data is not the same as absence.",
}


def badge(key, lang):
    return evidence.badge(BADGE_KEY.get(key, key), lang)


def cell(value):
    text = "" if value is None else str(value)
    return text.replace("|", "/").replace("\n", " ")


def fmt_pct(x):
    if x is None:
        return "-"
    return ("%+.1f" % x).rstrip("0").rstrip(".") if x else "0"


def fmt_share(x):
    return "-" if x is None else ("%.1f%%" % x).replace(".0%", "%")


def money(value, currency):
    if value is None:
        return "-"
    return "%s %s" % (value, currency or "")


def date_of(ts):
    return (ts or "")[:10]


def label(etype, lang):
    return TYPE_LABEL[lang].get(etype, etype)


def item_of(ev, lang):
    if ev.get("title"):
        name = ev["title"]
        if ev["type"] == "policy_changed" or ev["type"].startswith("policy_"):
            name = POLICY_LABEL[lang].get(name, name)
        if ev.get("variant"):
            name += " (%s)" % ev["variant"]
        return name
    if ev["type"].startswith("policy_"):
        return POLICY_LABEL[lang].get(ev.get("field"), ev.get("field"))
    return ev.get("field") or ""


def term_value(term, value, lang):
    if value is None:
        return {"zh": "未写明", "en": "not stated"}[lang]
    if term == "return_shipping_paid_by":
        value = PAID_BY[lang].get(value, value)
    return TERM_LABEL[lang].get(term, (term, "%s"))[1] % value


def by_type(events, *types):
    return [e for e in events if e["type"] in types and not e.get("virtual")]


def overall_badge(diff):
    srcs = (diff["from"]["source"], diff["to"]["source"])
    return "page_shown" if "archive" in srcs else "verified"


def top_changes(diff, lang, limit=3):
    """Short lines for 'At a glance', most important first."""
    ev = diff["events"]
    zh = lang == "zh"
    out = []
    for e in by_type(
        ev, "store_status_changed", "currency_changed", "price_sitewide_shift"
    ):
        out.append((label(e["type"], lang), e))
    added = by_type(ev, "product_added")
    if added:
        names = ", ".join(e["title"] for e in added[:3])
        more = (
            (" 等 %d 款" if zh else " and %d more") % len(added)
            if len(added) > 3
            else ""
        )
        if zh and not more:
            more = " 共 %d 款" % len(added)
        out.append(("%s: %s%s" % (label("product_added", lang), names, more), added[0]))
    removed = by_type(ev, "product_removed")
    if removed:
        names = ", ".join(e["title"] for e in removed[:3])
        out.append(("%s: %s" % (label("product_removed", lang), names), removed[0]))
    th = diff["thresholds"]["price_summary_pct"]
    for p in diff["price_changes"]:
        if p["virtual"] or abs(p["delta_pct"] or 0) < th:
            continue
        ev_ref = next((e for e in ev if e.get("event_id") == p.get("event_id")), None)
        if ev_ref is None:
            continue
        out.append(
            (
                "%s %s → %s (%s%%)"
                % (
                    p["title"],
                    money(p["before"], p["currency"]),
                    money(p["after"], p["currency"]),
                    fmt_pct(p["delta_pct"]),
                ),
                ev_ref,
            )
        )
    started = by_type(ev, "promo_started")
    if started:
        names = ", ".join(e["title"] for e in started[:3])
        out.append(("%s: %s" % (label("promo_started", lang), names), started[0]))
    for e in [
        x for x in ev if x["type"] == "policy_changed" and x["severity"] == "high"
    ]:
        term, unit = TERM_LABEL[lang].get(e["field"], (e["field"], "%s"))
        out.append(
            (
                "%s, %s: %s → %s"
                % (
                    POLICY_LABEL[lang].get(e["title"], e["title"]),
                    term,
                    term_value(e["field"], e["before"], lang),
                    term_value(e["field"], e["after"], lang),
                ),
                e,
            )
        )
    for e in by_type(ev, "theme_changed", "homepage_promo_changed"):
        out.append((label(e["type"], lang), e))
    lines = []
    for text, e in out[:limit]:
        lines.append(
            "  %d. %s %s (%s)"
            % (len(lines) + 1, text, badge(e["badge"], lang), e["event_id"])
        )
    return lines


def diff_md(diff, ctx):
    lang = ctx.get("lang") or "zh"
    t = T[lang]
    ev = diff["events"]
    c = diff["counts"]
    cur = diff.get("currency")
    ob = badge(overall_badge(diff), lang)
    name = ctx.get("name") or diff.get("shop_name") or diff["domain"]
    fr, to = (
        date_of(diff["from"]["captured_at_utc"]),
        date_of(diff["to"]["captured_at_utc"]),
    )
    days = span(diff["interval_days"], lang)
    if ctx.get("demo"):
        name = "%s (%s)" % (name, t["demo_tag"])
    L = [t["title"] % name, "", t["sub"] % (diff["domain"], fr, to, days), ""]
    if ctx.get("demo"):
        L += [t["demo"], ""]
    # -- at a glance --------------------------------------------------------
    L += [t["glance"], ""]
    src = SOURCE_LABEL[lang]
    L.append(
        t["range"]
        % (fr, to, days, src[diff["from"]["source"]], src[diff["to"]["source"]])
    )
    bad = diff["from"]["status"] in ("blocked", "not_shopify") or diff["to"][
        "status"
    ] in (
        "blocked",
        "not_shopify",
    )
    comp = diff["comparable"]
    if bad:
        side = (
            diff["to"]
            if diff["to"]["status"] in ("blocked", "not_shopify")
            else diff["from"]
        )
        why = T[lang]["fail_why"].get(side.get("fetch_error") or side["status"], "")
        L.append(t["fail_glance"] % (diff["domain"], why))
    elif not comp["products"]:
        side = diff["from"] if not diff["from"]["products"] else diff["to"]
        L.append(
            t["products_na"]
            % ("%s, %s" % (side["snapshot"], SOURCE_LABEL[lang].get(side["source"])))
        )
    else:
        removed = "%d" % c["product_removed"] if comp["removals"] else t["removed_na"]
        L.append(
            t["counts"]
            % (
                c["product_added"],
                removed,
                c["products_price_down"],
                c["products_price_up"],
                c["promo_started"],
                ob,
            )
        )
    real = [e for e in ev if not e.get("virtual")]
    if not real and not bad:
        L.append(t["nothing"] % ob)
    elif real:
        tops = top_changes(diff, lang)
        if tops:
            L.append(t["top"])
            L += tops
        else:
            L.append(t["top_none"] % ob)
    L.append(t["next"] % (ctx["next_due"], diff["domain"]))
    L.append("")
    if bad:
        L += signals_and_gaps(diff, t, lang, ev, failed=True)
        L += appendix(diff, t, lang, ctx)
        return "\n".join(L) + "\n"
    # -- prices ---------------------------------------------------------------
    L += [t["price_h"], ""]
    th = diff["thresholds"]["price_summary_pct"]
    if not comp["prices"]:
        e = by_type(ev, "currency_changed")
        if e:
            L.append(t["price_skip"] % (e[0]["before"], e[0]["after"], ob))
        else:
            L.append(t["price_na"])
    else:
        for e in by_type(ev, "price_sitewide_shift"):
            d = e.get("details") or {}
            L.append(
                t["price_sitewide"]
                % (
                    d.get("products"),
                    d.get("comparable"),
                    fmt_pct(e["delta_pct"]),
                    badge(e["badge"], lang),
                    e["event_id"],
                )
            )
            L.append("")
        pcs = [p for p in diff["price_changes"] if not p["virtual"]]
        big = [p for p in pcs if abs(p["delta_pct"] or 0) >= th]
        if not pcs:
            L.append(t["price_none"] % ob)
        if big:
            L += [t["price_table"], "|---|---|---|---|---|---|"]
            for p in big:
                sale = (
                    t["on_sale_yes"] % fmt_pct(p["discount_after"]).lstrip("+")
                    if p["on_sale_after"]
                    else t["on_sale_no"]
                )
                L.append(
                    "| %s | %s | %s | %s%% %s | %s | %s |"
                    % (
                        cell(p["title"]),
                        money(p["before"], cur),
                        money(p["after"], cur),
                        fmt_pct(p["delta_pct"]),
                        ob,
                        sale,
                        p.get("event_id") or "",
                    )
                )
        small = len(pcs) - len(big)
        if small:
            L += ["", t["price_small"] % (small, ("%g" % th))]
    L.append("")
    # -- new / removed ---------------------------------------------------------
    L += [t["new_h"], ""]
    if comp["products"]:
        L += new_removed(diff, t, lang, ev, ob, cur, comp)
    else:
        L.append(t["products_na_text"])
    L.append("")
    # -- promotions -------------------------------------------------------------
    L += [t["promo_h"], ""]
    promo = by_type(
        ev,
        "promo_started",
        "promo_ended",
        "discount_deeper",
        "discount_shallower",
        "compare_at_raised",
    )
    if comp["prices"]:
        if promo:
            for e in promo[:25]:
                depth = e["after"] if e["after"] is not None else e["before"]
                info = (
                    ("%s%%" % fmt_pct(depth).lstrip("+"))
                    if isinstance(depth, (int, float))
                    else "-"
                )
                if e["type"] in ("discount_deeper", "discount_shallower"):
                    info = "%s%% → %s%%" % (
                        fmt_pct(e["before"]).lstrip("+"),
                        fmt_pct(e["after"]).lstrip("+"),
                    )
                L.append(
                    t["promo_line"]
                    % (
                        label(e["type"], lang),
                        cell(e["title"]),
                        info,
                        badge(e["badge"], lang) + " " + e["event_id"],
                    )
                )
            if len(promo) > 25:
                L.append(t["events_more"] % (len(promo) - 25))
        else:
            L.append(t["promo_none"] % ob)
        ps = diff["promo_share"]
        L.append(
            t["promo_share"] % (fmt_share(ps["before"]), fmt_share(ps["after"]), ob)
        )
    if comp["homepage"]:
        hp = by_type(ev, "homepage_promo_changed")
        if hp:
            d = hp[0].get("details") or {}
            pb = badge(hp[0]["badge"], lang)
            if d.get("added"):
                L.append(
                    t["home_added"]
                    % ("; ".join('"%s"' % x for x in d["added"][:5]), pb)
                )
            if d.get("removed"):
                L.append(
                    t["home_removed"]
                    % ("; ".join('"%s"' % x for x in d["removed"][:5]), pb)
                )
        else:
            L.append(t["home_same"] % badge("page_shown", lang))
        ht = by_type(ev, "homepage_text_changed")
        if ht:
            L.append(
                t["home_text"]
                % (ht[0]["after"], ht[0]["before"], badge("page_shown", lang))
            )
    L += [t["promo_note"], ""]
    # -- tech and markets --------------------------------------------------------
    L += [t["tech_h"], ""]
    tech = []
    for e in by_type(ev, "theme_changed", "theme_updated"):
        tech.append(t["theme"] % (e["before"], e["after"], badge(e["badge"], lang)))
    for e in by_type(ev, "app_signal_added"):
        tech.append(t["app_add"] % (e["field"], badge(e["badge"], lang)))
    for e in by_type(ev, "app_signal_removed"):
        tech.append(t["app_rem"] % (e["field"], badge(e["badge"], lang)))
    for e in by_type(ev, "tracking_added"):
        tech.append(t["track_add"] % (e["field"], badge(e["badge"], lang)))
    for e in by_type(ev, "tracking_removed"):
        tech.append(t["track_rem"] % (e["field"], badge(e["badge"], lang)))
    for e in by_type(ev, "currency_changed"):
        tech.append(t["currency"] % (e["before"], e["after"], badge(e["badge"], lang)))
    for e in by_type(ev, "markets_changed"):
        b = badge(e["badge"], lang)
        if e["field"] == "ships_to_count":
            tech.append(t["ships"] % (e["before"], e["after"], b))
        elif e["field"] == "hreflang":
            d = e.get("details") or {}
            tech.append(
                t["hreflang"]
                % (
                    ", ".join(d.get("added") or []) or t["na"],
                    ", ".join(d.get("removed") or []) or t["na"],
                    b,
                )
            )
        else:
            tech.append(t["localization"] % (e["before"], e["after"], b))
    for e in by_type(
        ev, "collection_added", "collection_removed", "collection_renamed"
    ):
        tech.append(
            t["coll"]
            % (
                "%s: %s" % (label(e["type"], lang), cell(e["after"] or e["before"])),
                badge(e["badge"], lang),
            )
        )
    if tech:
        L += tech[:30]
    elif comp["homepage"]:
        L.append(t["tech_none"] % badge("page_shown", lang))
    else:
        L.append(t["tech_na"])
    L.append("")
    # -- policies -----------------------------------------------------------------
    L += [t["policy_h"], ""]
    pol = [
        e
        for e in ev
        if e["type"] in ("policy_changed", "policy_added", "policy_removed")
    ]
    if not comp["policies"]:
        L.append(t["policy_na"])
    elif not pol:
        names = ", ".join(POLICY_LABEL[lang].get(n, n) for n in comp["policies"])
        L.append(t["policy_none"] % (names, ob))
    for e in pol:
        b = badge(e["badge"], lang)
        pname = POLICY_LABEL[lang].get(
            e.get("title") or e.get("field"), e.get("title") or e.get("field")
        )
        if e["type"] == "policy_added":
            L.append(t["policy_page_add"] % (pname, b))
        elif e["type"] == "policy_removed":
            L.append(t["policy_page_rem"] % (pname, b))
        elif e["field"] != "text":
            term = TERM_LABEL[lang].get(e["field"], (e["field"], "%s"))[0]
            L.append(
                t["policy_term"]
                % (
                    pname,
                    term,
                    term_value(e["field"], e["before"], lang),
                    term_value(e["field"], e["after"], lang),
                    b + " " + e["event_id"],
                )
            )
        else:
            d = e.get("details") or {}
            L.append(
                t["policy_text"]
                % (
                    pname,
                    len(d.get("added") or []),
                    len(d.get("removed") or []),
                    len(d.get("rewritten") or []),
                    b + " " + e["event_id"],
                )
            )
            shown = 0
            for o, n in d.get("rewritten") or []:
                if shown < 3:
                    L.append(t["policy_rew"] % (clip(o), clip(n)))
                    shown += 1
            for s in d.get("added") or []:
                if shown < 3:
                    L.append(t["policy_add"] % clip(s))
                    shown += 1
            for s in d.get("removed") or []:
                if shown < 3:
                    L.append(t["policy_rem"] % clip(s))
                    shown += 1
    L.append("")
    L += signals_and_gaps(diff, t, lang, ev)
    L += appendix(diff, t, lang, ctx)
    return "\n".join(L) + "\n"


def new_removed(diff, t, lang, ev, ob, cur, comp):
    L = [t["new_sub"], ""]
    added = by_type(ev, "product_added")
    if added:
        L += [t["new_table"], "|---|---|---|---|---|"]
        for e in added:
            d = e.get("details") or {}
            L.append(
                "| %s | %s | %s | %s | %s %s |"
                % (
                    cell(e["title"]),
                    cell(d.get("product_type")) or "-",
                    money(e["after"], cur),
                    date_of(d.get("published_at") or d.get("created_at")) or "-",
                    e["event_id"],
                    badge(e["badge"], lang),
                )
            )
    elif comp["products"]:
        L.append(t["new_none"] % ob)
    surfaced = by_type(ev, "product_surfaced")
    if surfaced:
        L += ["", t["surfaced"] % len(surfaced)]
    L += ["", t["removed_sub"], ""]
    removed = by_type(ev, "product_removed")
    missing = by_type(ev, "product_missing_unconfirmed")
    if not comp["removals"]:
        why = (
            t["removed_na_archive"]
            if "archive_baseline" in diff["warnings"]
            else t["removed_na_why"]
        )
        L.append(t["removed_na_text"] % (why, len(missing), badge("not_found", lang)))
    elif removed:
        L += [t["removed_table"], "|---|---|---|"]
        for e in removed:
            L.append(
                "| %s | %s | %s %s |"
                % (
                    cell(e["title"]),
                    money(e["before"], cur),
                    e["event_id"],
                    badge(e["badge"], lang),
                )
            )
        if missing:
            L += [
                "",
                t["removed_na_text"]
                % (t["removed_na_why"], len(missing), badge("not_found", lang)),
            ]
    elif missing:
        L.append(
            t["removed_na_text"]
            % (t["removed_na_why"], len(missing), badge("not_found", lang))
        )
    else:
        L.append(t["removed_none"] % ob)
    stock = by_type(ev, "sold_out", "restocked")
    if stock:
        L += ["", t["stock_sub"], ""]
        for e in stock:
            L.append(
                t["stock_line"]
                % (
                    label(e["type"], lang),
                    cell(e["title"]),
                    badge(e["badge"], lang),
                    e["event_id"],
                )
            )
    var = by_type(ev, "variant_added", "variant_removed")
    if var:
        L += ["", t["variant_sub"], ""]
        for e in var[:20]:
            L.append(
                t["stock_line"]
                % (
                    label(e["type"], lang),
                    cell(item_of(e, lang)),
                    badge(e["badge"], lang),
                    e["event_id"],
                )
            )
        if len(var) > 20:
            L.append(t["events_more"] % (len(var) - 20))
    cad = diff["cadence"]
    L += ["", t["cadence_sub"], ""]
    if cad["added_per_week"] is None:
        L.append(t["cadence_na"])
    else:
        L.append(t["cadence_line"] % (cad["added"], cad["added_per_week"], ob))
    months = cad["published_by_month"]
    if months.get("after"):
        L += ["", t["month_table"], "|---|---|---|"]
        for m in sorted(months["after"]):
            L.append(
                "| %s | %s | %s |"
                % (
                    m,
                    months["before"].get(m, "-") if diff["from"]["products"] else "-",
                    months["after"][m],
                )
            )
    return L


def span(interval_days, lang):
    """'7.0 天' / '1.1 小时' (en: '7.0 days' / '1.1 hours')."""
    if interval_days is None:
        return "-"
    if interval_days < 1:
        return ("%.1f 小时" if lang == "zh" else "%.1f hours") % (interval_days * 24)
    return ("%s 天" if lang == "zh" else "%s days") % interval_days


def clip(text, n=160):
    text = (text or "").replace('"', "'")
    return text if len(text) <= n else text[: n - 3] + "..."


def signals_and_gaps(diff, t, lang, ev, failed=False):
    ids = [
        e["event_id"] for e in ev if e["severity"] == "high" and not e.get("virtual")
    ][:8]
    L = [t["signals_h"], "", t["signals_ph"] % (", ".join(ids) or t["na"]), ""]
    L += [t["gaps_h"], ""]
    if failed:
        L.append(t["fail_gap"])
    else:
        dims = ", ".join(
            t["dim"][k]
            for k in (
                "products",
                "prices",
                "removals",
                "homepage",
                "collections",
                "markets",
            )
            if not diff["comparable"].get(k)
        )
        if dims:
            L.append(t["gap_dim"] % dims)
        else:
            L.append(t["gap_none"])
    wt = WARNING_TEXT[lang]
    for w in diff["warnings"]:
        if w in wt:
            L.append("- " + wt[w])
    L.append("")
    return L


def snap_rows(t, lang, refs):
    rows = [t["snap_table"], "|---|---|---|---|---|---|"]
    for r in refs:
        rows.append(
            "| %s | %s | %s | %s | %s | %s |"
            % (
                r["snapshot"],
                r["captured_at_utc"],
                SOURCE_LABEL[lang].get(r["source"], r["source"]),
                STATUS_LABEL[lang].get(r["status"], r["status"]),
                r["products"],
                t["yes"] if r.get("pagination_complete") else t["no"],
            )
        )
    for r in refs:
        if r.get("archive_timestamps"):
            stamps = ", ".join(
                "%s=%s" % kv for kv in sorted(r["archive_timestamps"].items())
            )
            rows.append("")
            rows.append("- %s archive timestamps: %s" % (r["snapshot"], stamps))
    return rows


def appendix(diff, t, lang, ctx):
    th = diff["thresholds"]
    L = [t["appendix_h"], "", t["status"] % diff["status"], ""]
    L += snap_rows(t, lang, [diff["from"], diff["to"]])
    L.append("")
    for i, line in enumerate(t["method"]):
        if i == 1:
            line = line % ("%g" % th["price_summary_pct"])
        elif i == 2:
            line = line % ("%g" % th["discount_pp"])
        L.append(line)
    L.append(t["warnings"] % (", ".join(diff["warnings"]) or t["na"]))
    L.append(t["tech_terms"])
    ev = diff["events"]
    if ev:
        L += [
            "",
            t["events_h"],
            "",
            t["events_table"],
            "|---|---|---|---|---|---|---|---|",
        ]
        limit = 80
        for e in ev[:limit]:
            loc = (e.get("evidence") or {}).get("to_locator") or ""
            L.append(
                "| %s | %s | %s | %s | %s | %s | %s | %s |"
                % (
                    e["event_id"],
                    label(e["type"], lang),
                    SEVERITY_LABEL[lang][e["severity"]],
                    cell(item_of(e, lang)),
                    cell(e["before"]) if e["before"] is not None else "-",
                    cell(e["after"]) if e["after"] is not None else "-",
                    badge(e["badge"], lang),
                    "`%s`" % loc if ctx.get("locators", True) and loc else "-",
                )
            )
        if len(ev) > limit:
            L += ["", t["events_more"] % (len(ev) - limit)]
    hd = diff.get("homepage_detail") or {}
    if hd.get("added") or hd.get("removed"):
        L += ["", t["home_detail_h"], ""]
        for s in (hd.get("added") or [])[:30]:
            L.append("- + %s" % clip(s, 200))
        for s in (hd.get("removed") or [])[:30]:
            L.append("- - %s" % clip(s, 200))
    pol = [e for e in ev if e["type"] == "policy_changed" and e["field"] == "text"]
    if pol:
        L += ["", t["policy_detail_h"], ""]
        for e in pol:
            d = e.get("details") or {}
            L.append(
                "- %s (%s)"
                % (POLICY_LABEL[lang].get(e["title"], e["title"]), e["event_id"])
            )
            for o, n in d.get("rewritten") or []:
                L.append(t["policy_rew"] % (clip(o, 300), clip(n, 300)))
            for s in d.get("added") or []:
                L.append(t["policy_add"] % clip(s, 300))
            for s in d.get("removed") or []:
                L.append(t["policy_rem"] % clip(s, 300))
    L.append("")
    L += fixed_ending(lang)
    return L


def fixed_ending(lang):
    """The template's fixed ending, read from references/ (single source)."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "references", "report-template.%s.md" % lang)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    start = text.rindex("```markdown\n") + len("```markdown\n")
    end = text.index("\n```", start)
    return text[start:end].split("\n")


# ---------------------------------------------------------------------------
# baseline (one snapshot)
# ---------------------------------------------------------------------------


def baseline_md(base, ctx):
    lang = ctx.get("lang") or "zh"
    t = T[lang]
    snap = base["snapshot"]
    name = ctx.get("name") or base.get("shop_name") or base["domain"]
    day = date_of(snap["captured_at_utc"])
    vb = badge("page_shown" if snap["source"] == "archive" else "verified", lang)
    pb = badge("page_shown", lang)
    cur = base.get("currency")
    L = [t["title_base"] % name, "", t["sub_base"] % (base["domain"], day), ""]
    if ctx.get("demo"):
        L += [t["demo"], ""]
    L += [t["glance"], ""]
    failed = base["status"] == "failed"
    if failed:
        L.append(
            t["fail_glance"]
            % (
                base["domain"],
                t["fail_why"].get(snap.get("fetch_error") or snap["status"], ""),
            )
        )
        L.append(t["fail_next"] % ctx["next_due"])
        L += [""]
        L += [
            t["signals_h"],
            "",
            t["signals_ph"] % t["na"],
            "",
            t["gaps_h"],
            "",
            t["fail_gap"],
            "",
        ]
        L += base_appendix(base, t, lang)
        return "\n".join(L) + "\n"
    L.append(t["base_glance1"] % base["domain"])
    virt = (
        t["base_virtual"] % base["virtual_products"] if base["virtual_products"] else ""
    )
    L.append(
        t["base_counts"]
        % (
            base["products"],
            virt,
            base["published_recent"]["30"],
            base["published_recent"]["90"],
            vb,
        )
    )
    L.append(t["base_sale"] % (fmt_share(base["on_sale_share"]), vb))
    if base["promo_lines"]:
        L.append(
            t["base_promo"]
            % ("; ".join('"%s"' % x for x in base["promo_lines"][:5]), pb)
        )
    elif (base.get("coverage") or {}).get("homepage"):
        L.append(t["base_promo_none"] % pb)
    L.append(t["base_next"] % (ctx["next_due"], base["domain"]))
    L += ["", t["base_products_h"], ""]
    prices = sorted(float(r["price"]) for r in base["rows"] if r["price"] is not None)
    if prices:
        mid = prices[len(prices) // 2]
        L.append(
            t["base_price"]
            % ("%.2f" % prices[0], "%.2f" % mid, "%.2f" % prices[-1], cur or "", vb)
        )
    types = {}
    for r in base["rows"]:
        if r["product_type"]:
            types[r["product_type"]] = types.get(r["product_type"], 0) + 1
    if types:
        top = sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        L.append(t["base_types"] % (", ".join("%s (%d)" % kv for kv in top), vb))
    L += ["", t["base_recent_h"], ""]
    recent = [
        r for r in base["rows"] if r["published"] and r["published"] >= recent_cut(day)
    ]
    if recent:
        L += [
            t["new_table"]
            .replace(" 事件 |", " 在打折 |")
            .replace(" Event |", " On sale |"),
            "|---|---|---|---|---|",
        ]
        for r in recent[:15]:
            L.append(
                "| %s | %s | %s | %s | %s |"
                % (
                    cell(r["title"]),
                    cell(r["product_type"]) or "-",
                    money(r["price"], cur),
                    r["published"],
                    t["yes"] if r["on_sale"] else t["no"],
                )
            )
    else:
        L.append(t["base_recent_none"] % vb)
    L += ["", t["base_tech_h"], ""]
    th = base.get("theme") or {}
    if th.get("name") or th.get("schema_name"):
        L.append(
            t["base_theme"]
            % (
                "%s %s"
                % (
                    th.get("schema_name") or th.get("name"),
                    th.get("schema_version") or "",
                ),
                pb,
            )
        )
    sig = base.get("signals") or {}
    if sig.get("apps_possible"):
        L.append(t["base_apps"] % (", ".join(sig["apps_possible"]), pb))
    if sig.get("tracking_tags"):
        L.append(t["base_track"] % (", ".join(sig["tracking_tags"]), pb))
    shop = base.get("shop") or {}
    L.append(
        t["base_ships"]
        % (
            shop.get("currency") or "-",
            shop.get("ships_to_count")
            if shop.get("ships_to_count") is not None
            else "-",
            vb,
        )
    )
    L += ["", t["base_policy_h"], ""]
    lines = []
    for pname, terms in sorted(base["policies"].items()):
        for term, value in sorted(terms.items()):
            lines.append(
                t["base_policy_line"]
                % (
                    POLICY_LABEL[lang].get(pname, pname)
                    + ", "
                    + TERM_LABEL[lang].get(term, (term, ""))[0],
                    term_value(term, value, lang),
                    badge("store_claim", lang),
                )
            )
    L += lines or [t["base_policy_none"]]
    L += [
        "",
        t["signals_h"],
        "",
        t["signals_ph"] % t["na"],
        "",
        t["gaps_h"],
        "",
        t["base_gap"],
    ]
    if snap["source"] == "archive":
        L.append("- " + WARNING_TEXT[lang]["archive_baseline"])
    if ctx.get("archive_note"):
        L.append(t["base_archive_na"] % badge("not_found", lang))
    L.append("")
    L += base_appendix(base, t, lang)
    return "\n".join(L) + "\n"


def recent_cut(day):
    import datetime

    try:
        d = datetime.date(int(day[:4]), int(day[5:7]), int(day[8:10]))
    except ValueError:
        return "9999"
    return (d - datetime.timedelta(days=30)).isoformat()


def base_appendix(base, t, lang):
    L = [t["appendix_h"], "", t["status"] % base["status"], ""]
    L += snap_rows(t, lang, [base["snapshot"]])
    L += ["", t["method"][0], t["method"][6], t["tech_terms"], ""]
    L += fixed_ending(lang)
    return L


# ---------------------------------------------------------------------------
# CSV rows (tables.json)
# ---------------------------------------------------------------------------

CHANGE_TYPES = [
    "product_added",
    "product_removed",
    "product_missing_unconfirmed",
    "product_surfaced",
    "product_relisted",
    "sold_out",
    "restocked",
    "variant_added",
    "variant_removed",
]


def diff_tables(diff, lang):
    cur = diff.get("currency")
    price_rows, change_rows, all_rows = [], [], []
    for e in diff["events"]:
        url = (e.get("evidence") or {}).get("url")
        d = e.get("details") or {}
        if e["type"] in ("price_down", "price_up") and not e.get("virtual"):
            b, a = e["before"], e["after"]
            delta = None
            try:
                delta = "%.2f" % (float(a) - float(b))
            except (TypeError, ValueError):
                pass
            price_rows.append(
                {
                    "title": e["title"],
                    "variant": e.get("variant"),
                    "sku": d.get("sku"),
                    "before": b,
                    "after": a,
                    "delta": delta,
                    "delta_pct": e.get("delta_pct"),
                    "compare_before": d.get("compare_at_before"),
                    "compare_after": d.get("compare_at_after"),
                    "discount_before": d.get("discount_before"),
                    "discount_after": d.get("discount_after"),
                    "currency": e.get("currency") or cur,
                    "url": url,
                    "event_id": e["event_id"],
                }
            )
        if e["type"] in CHANGE_TYPES and not e.get("virtual"):
            change_rows.append(
                {
                    "change": label(e["type"], lang),
                    "title": item_of(e, lang),
                    "product_type": d.get("product_type"),
                    "price": e["after"] if e["after"] is not None else e["before"],
                    "published": date_of(d.get("published_at")) or None,
                    "url": url,
                    "event_id": e["event_id"],
                }
            )
        all_rows.append(
            {
                "event_id": e["event_id"],
                "label": label(e["type"], lang),
                "severity": SEVERITY_LABEL[lang][e["severity"]],
                "item": item_of(e, lang),
                "before": e["before"],
                "after": e["after"],
                "delta_pct": e.get("delta_pct"),
                "badge": badge(e["badge"], lang),
                "url": url,
            }
        )
    return {
        "price_changes": price_rows,
        "product_changes": change_rows,
        "all_events": all_rows,
    }


def baseline_tables(base, lang):
    rows = []
    for r in base["rows"]:
        row = dict(r)
        row["currency"] = base.get("currency")
        row["on_sale"] = {True: T[lang]["yes"], False: T[lang]["no"]}.get(r["on_sale"])
        row["available"] = {True: T[lang]["yes"], False: T[lang]["no"]}.get(
            r["available"]
        )
        rows.append(row)
    return {"current_products": rows}
