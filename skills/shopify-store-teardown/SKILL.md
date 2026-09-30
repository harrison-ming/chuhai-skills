---
name: shopify-store-teardown
description: >-
  拆解任意公开 Shopify 独立站 (对标店/竞品店). 核实店铺身份与店龄, 全量统计商品数/价格带/折扣/上新节奏, 识别主题/应用/Markets/追踪标签, 检查政策页一致性, 输出带证据分级的中文报告. 用于: 对标店分析, 竞品独立站拆解, "这家店是真的吗/开了多久", "它靠什么卖这个价", Shopify store teardown, competitor store analysis. 只读公开页面, 不登录, 不下单.
license: MIT
metadata:
  version: "0.1.1"
  author: StellarByte
  homepage: https://github.com/harrison-ming/chuhai-skills
  status: experimental
  review_by: "2026-12-30"
---

# Shopify 对标店拆解

把一个公开的 Shopify 店铺拆成一份**有证据, 可追溯, 能行动**的报告. 你是分析者: 事实交给脚本算, 判断由你来做, 两者都要能追溯到证据.

## 边界 (先读)

- 只读公开页面和公开接口. **不登录, 不进入结账, 不提交订单, 不填任何个人信息.**
- 请求要礼貌: 同一店铺的请求之间留间隔, 不做并发轰炸.
- 本 skill 覆盖 L0 和 L1 两档. 购物车交互, 移动端实测, 广告库, 性能等需要浏览器的检查 (L2) 不在范围内, 报告里如实写"未覆盖".
- 分析结果是时间点快照, 不是尽职调查或法律意见.

## 第 1 步: 明确研究问题和档位

没有研究问题就不要做"全面分析". 先问用户一次, 用户没说或说"你看着办", 就按下表默认 L1 并在报告里写明.

| 用户想知道 | 档位 | 耗时 |
|---|---|---|
| 真的假的 / 是谁的 / 开了多久 / 规模多大 | L0 | 约 10 分钟 |
| 它靠什么卖这个价 / 页面怎么推动购买 / 我能借鉴什么 | L1 | 约 30-60 分钟 |

输入可以是域名, 首页链接或任意商品页链接.

## 第 2 步: 采集数据 (按环境自动选模式)

脚本在本 skill 目录的 `scripts/` 下, 只用 Python 标准库, 不需要安装任何包. Windows 上用 `python` 代替 `python3`.

**模式 A (首选): 能运行 Python 且能联网**

```bash
python3 <skill 目录>/scripts/collect.py <店铺网址> --depth L1
```

产物默认写到当前目录的 `store-teardown/<domain>/<日期>/`: `summary.json` (统计与端点状态), `products.tsv` (全部商品), `evidence.jsonl` (证据条目), `raw/` (原始数据).

判断: 如果输出显示几乎所有端点都是网络错误 (不是 404 或 403), 说明环境不能联网, 换模式 B.

**模式 B: 能运行 Python 但不能联网, 有网页读取工具** → 按 `references/manual-collection.md` 取数存文件, 再运行 `scripts/summarize.py <运行目录>`.

**模式 C / D: 不能运行 Python, 或没有读取工具** → 按 `references/manual-collection.md` 的对应小节处理. 这两种模式下报告完成状态最多写 `partial`.

## 第 3 步: 读统计结果, 先处理警告

读 `summary.json`. 字段含义见 `references/data-contract.md`. 必须先看:

1. `identity.status`: 只有 `confirmed` / `probable` 才继续按 Shopify 分析. `not_shopify` 就告诉用户并停止 Shopify 专项部分.
2. `warnings`: 每一条都要在报告里交代, 写出涉及的实际数值, 不要自行解释原因. 例如 `collections_count_mismatch` 时写出 `collections_all_products_count` 与 `products.count` 两个数 (前者可能是 0, 也可能远大于后者), 并说明商品量以 `products.count` 为准.
3. `endpoints` 里失败的项: 写进"未知与限制", **失败不等于不存在**.

## 第 4 步: 分析

- **L0**: 身份信号, 店名与基础币种, 店龄三方对照 (RDAP 注册 / Wayback 首抓 / 首个商品 `created_at`, 如有品牌自称年份也放进来), 规模速览. 规则见 `references/shopify-checks.md` §1-2 和 `references/business-analysis.md` §9.
- **L1**: 在 L0 基础上, 读首页, 1 个主要集合页, 3 个商品页 (入门/主力/高价, 按 `summary.json` 的价格分位和 `best_selling_top` 挑), About 页, 5 个政策页 (模式 A 已存到 `raw/`). 按 `references/business-analysis.md` 全文分析, 平台层按 `references/shopify-checks.md` §3-9.

证据与标签规则见 `references/evidence.md`. 最容易犯的错:

- 把 `collections.json` 的 `products_count` 当商品量.
- 看到支付图标就说"已开通 PayPal / Shopify Payments". 只能写 `[观察]`.
- 源码里有某应用字符串就说"在用某应用". 脚本结果只到 `possible`.
- 按页面样式猜主题名. 读不到 `Shopify.theme` 就写 `unknown`.
- 把域名注册日期当开店日期.
- 没有成本数据却估算利润率.
- 把一个市场的价格和政策当成全球通用.

## 第 5 步: 写报告

按 `references/report-template.md` 的对应档位模板写, 保存为运行目录下的 `report.md`, 并在对话里给用户一个 5-8 行的摘要 (结论 + 最值得借鉴的点 + 主要未知项).

- 每条承重结论带标签: `[事实]` `[品牌自述]` `[第三方声称]` `[观察]` `[推断]` (附依据) `[存疑]` `[未知]`.
- 数字写明来源和币种 (products.json 价格是店铺基础币种).
- **报告末尾必须原样附上模板里的"固定结尾"一节.**
- 用户用什么语言提问, 报告就用什么语言.

## 失败时

- 店铺打不开或身份无法确认: 交付 `failed` 状态的短报告, 写清楚卡在哪一步, 建议用户换一个网址 (例如带 `https://` 的首页) 或稍后重试.
- 证书错误: 如果用的是 macOS 上 python.org 安装的 Python, 提示用户运行一次 "Install Certificates.command". **不要关闭证书校验.**
- 被限流 (429): 加大 `--delay` 重试, 不要并发.
