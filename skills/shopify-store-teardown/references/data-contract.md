# 数据契约

`scripts/collect.py` 负责联网, 把原始数据写进 `原始数据/raw/`; `scripts/summarize.py` 完全离线, 读 `raw/` 产出 `summary.json` + `products.tsv` + `evidence.jsonl`; `scripts/check_report.py` 检查 agent 写的 `report.md` (结构, 标签, 技术词); `scripts/deliver.py` 先跑同一检查, 再把 `report.md` 排版成 PDF 并导出商品清单. 采集和统计分开, 是为了让不能联网的沙箱也能用: 先用网页读取工具把原始内容存成下面约定的文件名, 再跑 `summarize.py`.

## 1. 运行目录

交付根目录 `出海拆解报告/` 默认在用户的"文档"文件夹下 (Windows 自动识别 OneDrive 的文档; 找不到用主目录); 云端沙箱 (`/mnt/user-data/outputs` 存在且可写) 写到 `/mnt/user-data/outputs/出海拆解报告`; 可用 `--out-root <目录>` 或环境变量 `CHUHAI_OUTPUT_DIR` 指定. 每次运行一个文件夹, 重名自动加 " (2)":

```text
出海拆解报告/
└── <域名> 对标拆解 <YYYY-MM-DD>/          # 运行目录, collect.py 结束时打印
    ├── <店名> 对标拆解报告.pdf            # deliver.py 生成; 无 Edge/Chrome 时为 .html
    ├── 商品清单.csv                        # deliver.py 生成, 中文表头
    └── 原始数据/                           # summarize.py 的输入输出目录
        ├── raw/                           # 原始数据, 文件名固定 (见 §2)
        ├── summary.json                   # 派生统计 + 端点状态 + 身份信号
        ├── products.tsv                   # 一行一个商品
        ├── evidence.jsonl                 # 脚本可自动生成的证据条目 (E1/E4)
        ├── report.md                      # agent 按 report-template.md 写的报告
        ├── report.html                    # deliver.py 生成的排版稿 (PDF 的来源)
        └── 说明.txt                        # 给用户看的文件说明
```

下文的 `raw/...` 都指 `<运行目录>/原始数据/raw/...`.

## 2. `raw/` 文件名约定

| 文件 | 来源 URL | 必需 |
|---|---|---|
| `meta.json` | `https://<domain>/meta.json` | L0 |
| `products-<N>.json` | `https://<domain>/products.json?limit=250&page=<N>`, N 从 1 起, 翻到 `{"products": []}` 为止 | L0 |
| `collections-<N>.json` | `https://<domain>/collections.json?limit=250&page=<N>` | 可选 |
| `sitemap.xml` | `https://<domain>/sitemap.xml` | L0 |
| `sitemap-products-<N>.xml` | sitemap.xml 中 `sitemap_products_*` 子地图 | L0 |
| `homepage.html` | `https://<domain>/` 的 HTML 源码 | L0 |
| `best-selling.html` | `https://<domain>/collections/all?sort_by=best-selling` | 可选 |
| `rdap.json` | `https://rdap.org/domain/<registrable-domain>` | L0 |
| `wayback.json` | `https://web.archive.org/cdx/search/cdx?url=<domain>&output=json&fl=timestamp&collapse=timestamp:6` | L0 |
| `policy-<name>.html` | `https://<domain>/policies/<name>`, name 取 `refund-policy` `shipping-policy` `privacy-policy` `terms-of-service` `contact-information`; summarize 会从中派生正文 `raw/policy-<name>.txt` | L1 |
| `page-about.html` | `https://<domain>/pages/about` (依次试 `about-us` `our-story`) | L1 |
| `page-collection-<handle>.html` | 主要集合页: 优先 `/collections/all`, 取不到用 collections.json 第一个非空集合 | L1 |
| `page-product-<handle>.html` | 3 个样本商品页 (入门/主力/高价), 选择结果写进 `_status.json` 的 `l1_samples` | L1 |
| `page-product-<handle>.html` (价格抽样) | products.json 没拿到商品时 (例如被限流 429): 从 `sitemap-products-*.xml` 的商品网址里均匀挑最多 `--price-sample` (默认 12, 上限 30) 个, 跳过运费保障/礼品卡等, 请求间隔至少 1.5 秒, 遇到 429 或拦截立即停. 请求键 `product_page_sample`, 清单写进 `_status.json` 的 `price_sample` (`population` `planned` `requested` `stopped` `items`) | L0 |
| `_status.json` | collect 写入的每个请求的状态 (有 429 时另有 `rate_limit`: 被限流的主机, 已等待秒数, 等待预算); 手动模式可省略 | 可选 |

网页读取工具返回的内容若被截断或转成了 markdown, 照样保存, 但在 `_status.json` 或报告里注明. 手动模式没有 `products-*.json`, 只存了 `page-product-*.html` 时, 这些商品页同样当价格抽样统计 (`endpoints.product_page_sample.source` 为 `saved_files`).

限流 (HTTP 429): collect 按 `Retry-After` (秒数或 HTTP 日期) 等待, 单次最多 `--max-wait` (默认 75 秒; 店铺要求更久就不重试), 每个请求最多重试 2 次, 全程等待合计不超过 `--wait-budget` (默认 240 秒, 用完后再遇 429 直接放弃), 之后对该主机的请求间隔提高到 2-3 秒. products.json 第 1 页仍被限流时不再翻页, 也不再请求 collections.json (记为 `not_checked`), 改走上面的商品页抽样; 第 N 页 (N > 1) 被限流时保留已拿到的页. 之后的停止规则 ("最终 429" 指等待重试后仍是 429): 批量商品数据 (products.json / collections.json) 最终 429, 只跳过另一个批量接口, 商品页抽样照常; 商品类页面 (`sitemap_products` 子地图, 商品页, 集合页, best-selling 排序页) 最终 429, 本次跳过其余全部商品类请求 (含剩余子地图); 其他店铺页面 (政策页, 关于页, 网站地图索引) 最终 429, 不再请求这个主机的任何页面. 跳过的请求在 `_status.json` 记为 `not_checked`, note 分别为 `skipped: store rate limited products.json (429)`, `skipped: store rate limited catalogue pages`, `skipped: store rate limited this site`. 站外查询 (RDAP, Wayback) 不受影响. 跳转到另一个主机 (例如裸域名 → www) 后才收到 429 时, 两个主机都放慢. 不换 User-Agent, 不用代理, 不并发. `products.json` 太大时改用 `limit=50` 分页, 文件名不变.

## 3. `summary.json` 主要字段

```json
{
  "schema": "store-teardown/summary/1",
  "tool_version": "0.2.3",
  "domain": "example.com",
  "captured_at_utc": "2026-09-30T08:00:00Z",
  "endpoints": {
    "meta_json": {"result": "ok|blocked|not_found|error|not_checked", "http_status": 200, "url": "..."},
    "products_json": {"result": "ok", "pages": 3, "truncated": false},
    "product_page_sample": {"result": "ok", "source": "collect|saved_files", "requested": 12,
                            "fetched": 12, "priced": 12, "population": 640, "stopped": null},
    "sitemap": {"result": "ok"}, "homepage": {"result": "ok"},
    "rdap": {"result": "ok"}, "wayback": {"result": "error", "note": "unreachable"}
  },
  "identity": {
    "status": "confirmed|probable|unconfirmed|not_shopify",
    "signals": [{"name": "meta_myshopify_domain", "value": "x.myshopify.com", "source": "raw/meta.json"}]
  },
  "shop": {"name": "", "shop_id": null, "myshopify_domain": "", "currency": "USD", "country": "", "ships_to_count": 0},
  "theme": {"name": "", "schema_name": "", "schema_version": "", "theme_store_id": null, "role": "", "source": "Shopify.theme|not_observed|null"},
  "products": {
    "count": 398, "count_source": "products_json",
    "sitemap_product_urls": 398,
    "collections_all_products_count": 2858,
    "price": {"currency": "USD", "min": 0, "p25": 0, "median": 0, "p75": 0, "max": 0},
    "price_excluding_virtual": {"currency": "USD", "count": 390, "excluded": 8,
                                "min": 0, "p25": 0, "median": 0, "p75": 0, "max": 0,
                                "in_stock": {"count": 372, "min": 0, "p25": 0, "median": 0, "p75": 0, "max": 0}},
    "price_bands": [{"from": 0, "to": 25, "count": 0}],
    "on_sale_share": 0.0, "discount_depth_median": 0.0,
    "variants": {"median": 0, "max": 0, "multi_variant_share": 0.0},
    "options_top": [["Size", 120]], "product_types_top": [["Shoes", 80]],
    "vendors_top": [["Brand", 390]], "tags_top": [["new", 40]],
    "created_first": "2018-03-01", "created_last": "2026-09-20",
    "published_by_month": {"2026-08": 12},
    "availability": {"available": 380, "sold_out": 18},
    "price_source": "products_json|product_page_sample|null",
    "price_sample_size": 398, "price_population": 398,
    "sitemap_lastmod_by_month": {"2026-08": 40},
    "unavailable_reasons": {}
  },
  "coverage": {
    "products_json": "ok|partial|rate_limited|blocked|error|not_found|not_checked",
    "product_count_source": "products_json|sitemap|null",
    "price_source": "products_json|product_page_sample|null",
    "price_sample_size": 398, "price_population": 398,
    "discount_data": true, "new_product_cadence": true,
    "rate_limited_endpoints": []
  },
  "best_selling_top": ["handle-a", "handle-b"],
  "l1_samples": [{"handle": "handle-a", "role": "entry|main|premium", "price": 98.0,
                  "basis": "closest_to_p25_excluding_virtual", "basis_value": 95.0,
                  "basis_pool": "in_stock_real_products",
                  "file": "raw/page-product-handle-a.html", "result": "ok"}],
  "offsite": {
    "rdap": {"registered": "2017-11-02", "expires": "", "registrar": ""},
    "wayback": {"first_capture": "2017-12", "captures_by_year": {"2018": 5}}
  },
  "policies": {"refund-policy": {"result": "ok", "chars": 4200, "text_path": "raw/policy-refund-policy.txt"}},
  "html_signals": {
    "apps_possible": [{"name": "Judge.me", "pattern": "judge.me"}],
    "payment_icons_observed": ["paypal", "visa"],
    "hreflang": [["en-us", "https://example.com/"]],
    "tracking_tags": ["meta_pixel", "ga4"]
  },
  "warnings": ["collections_count_mismatch"],
  "errors": []
}
```

## 4. 字段纪律

- 空值: `null` = 适用但未取得; `"not_observed"` = 看了但没出现; `"unknown"` = 看了但判断不了. **`not_observed` 不等于不存在.**
- `products.count` 只来自 `products.json` 翻页或 sitemap (`count_source` 注明是哪个; sitemap 按商品 handle 去重, 多语言子地图不重复计数). `products.count_is_lower_bound` 为 true 时 (数据截断或没翻到底), 报告必须写"至少 N 款". `collections_all_products_count` 为 0 或远大于商品数都常见, 只用于提示差异, 不得当商品量.
- `products.price` 是店铺基础币种, 不是各市场价格, 含所有商品 (运费保障, 礼品卡等虚拟商品也算在内, 最低价常被它们拉低). `products.price_excluding_virtual` 排除价格 < 5 和运费保障/礼品卡/保险/退货险等虚拟商品 (与 `l1_samples` 同一判定函数), `excluded` 为排除的款数, `in_stock` 是其中有货商品的同样统计. 报告写价格分布时优先用它, 并注明口径.
- `coverage` 说明本次数字的依据, 写报告前先看:
  - `products_json`: `ok` 完整; `partial` 只拿到部分 (截断, 没确认翻到底, 或中途被限流); `rate_limited` 店铺返回 429 (等待后仍受限); `blocked` 被其他方式拦截 (401/403/430, 验证页); `error` / `not_found` / `not_checked` 同端点状态.
  - `price_source`: `products_json` = 全店商品数据; `product_page_sample` = 只来自商品页抽样 (`price_sample_size` 款, 总体约 `price_population` 款, 取自网站地图), 报告必须写明是抽样估计 (check_report.py 会检查); `null` = 没有价格数据.
  - `discount_data` / `new_product_cadence`: 有没有折扣 (原价) 数据和上新日期. 为 false 时折扣占比, 折扣深度, 上新节奏一律写 `[查不到]`, 对应字段为 null, 原因在 `products.unavailable_reasons`.
  - `rate_limited_endpoints`: 本次收到过 429 的请求键 (例如 `products_json`, `product_page_sample`).
- 价格来自抽样时 (`price_source` = `product_page_sample`): `products.price` / `price_excluding_virtual` / `price_bands` 按每个抽样商品页上的最低价统计 (优先页面里的结构化商品数据 JSON-LD `offers`, 任意嵌套层级都会找, 例如 `mainEntity`; 页面有多个商品时用网址 (`url` / `@id` / offer `url`) 指向本商品的那个; 对不上时只有页面只描述一个商品才用它的价格, 否则改用 `og:price:amount` / `product:price:amount`, 再没有就记为没有价格, 不猜), 币种是页面显示的币种 (可能按访客地区本地化); `on_sale_share`, `discount_depth_median`, `variants`, `created_*`, `published_by_month` 为 null 或空. 不从页面猜原价.
- `products.sitemap_lastmod_by_month` 是网站地图里每个商品的**最近修改时间** (`<lastmod>`) 按月计数, 改价, 改图, 改库存都会更新它, **不是上架时间**. 报告里只能写"最近更新", 不能当上新节奏.
- `offsite.wayback.captures_by_year` 是**该年有快照的月份数** (0-12), 不是抓取次数.
- `theme.storefront_hint` 为 `headless_possible` 时 (例如检测到 Next.js 而读不到 `Shopify.theme`), 只能写 `[推测]`, 不等于 Hydrogen.
- `html_signals.apps_possible` 只表示源码里出现了该应用的字符串, 可信度一律 `possible`; 要写成"在用", 需要渲染后的挂件吻合 (L2).
- `payment_icons_observed` 只是 `[页面显示]`, 不能推出后台已开通.
- `l1_samples` 在价格来自抽样时从已抽样的商品页里挑, `basis_pool` 为 `product_page_sample`, 不会再请求这些页面.
- `l1_samples`: 入门款 ≈ P25, 主力款 ≈ 中位 (优先 best-selling 靠前且在 P25-P75 之间的), 高价款 = 最高价; 分位数按排除虚拟商品后的口径算. `basis` 写选择依据 (`closest_to_p25_excluding_virtual` / `best_selling_in_p25_p75_excluding_virtual` / `closest_to_median_excluding_virtual` / `max_excluding_virtual`), `basis_value` 是该口径下的目标价 (如 P25). `basis_pool` 为 `in_stock_real_products` 时只在有货商品里挑, `basis_value` 对应 `price_excluding_virtual.in_stock`; 有货的不足 3 款时为 `all_real_products`, 对应 `price_excluding_virtual` 本身. 手动模式没有 `_status.json` 时按文件名生成, `role` 为 null, 没有 `basis`. best-selling 是商家可控排序, 不等于销量.
- `offsite.rdap.registered` 是域名注册日期, 不是开店日期.
- 任一端点失败只记状态, 不能当作 `not_shopify` 的证据.

## 5. `products.tsv` 与 `evidence.jsonl`

`products.tsv` 列 (制表符分隔, 一行一个商品): `product_id handle url title product_type vendor tags price_min price_max compare_at_max currency variant_count options available created_at published_at source`. 价格是基础币种; `source` 为 `products_json` (来自店铺公开商品数据) 或 `product_page` (来自抽样的商品页, 此时 `product_id` `product_type` `vendor` `tags` `variant_count` `options` `created_at` `published_at` 为空, `compare_at_max` 只在页面结构化数据明确给出划线价时才有值). 只有网站地图的商品不进这张表.

`evidence.jsonl` 每行: `evidence_id` (E001...), `level` (E1/E4), `kind`, `claim` (中文简述), `source_url`, `captured_at_utc`, `locator` (对应的 raw 文件). 报告引用证据时用 `evidence_id`.

比例字段 (`on_sale_share`, `discount_depth_median`, `multi_variant_share`) 是 0-1 的小数, 写进报告时换算成百分比. `policies.*.chars` 是字符数, 不是字数.

## 6. `warnings` 取值

| 值 | 含义 | 报告里怎么写 |
|---|---|---|
| `collections_count_mismatch` | collections 的 `products_count` 与实际商品数差 >20% | 说明商品量以 products.json/sitemap 为准 |
| `sitemap_count_mismatch` | sitemap 商品数与 products.json 差 >10% | 两个数都列出, 可能有未公开或已下架商品 |
| `sitemap_products_incomplete` | 有商品子地图没取到 | sitemap 数字是下限 |
| `products_json_truncated` | products.json 内容被截断 | 商品统计基于部分数据 |
| `products_pagination_unverified` | 无法确认已翻到最后一页 | 商品数是下限 |
| `products_json_empty` | 端点正常但商品为空 | 可能关闭了公开商品或未上架商品 |
| `products_json_rate_limited` | 店铺对批量商品数据返回 429 (限流), 按要求等待后仍受限 (或中途被限流) | 正文说明"店铺限制了批量读取商品数据", 附录写明; 建议隔一段时间再试, 不要立刻重跑 |
| `prices_from_page_sample` | 价格只来自商品页抽样 | 正文写"价格基于 N 个商品页抽样 (共约 M 款)", 是估计; 折扣/上新写 `[查不到]` |
| `price_sample_small` | 抽样拿到价格的商品页不足 8 个 | 价格结论只当参考, 不写价格带结构判断 |
| `policy_near_empty:<name>` | 该政策页正文极短, 疑似默认空模板 | 信任与合规部分列为缺口 |
| `network_unavailable` | 店铺和站外查询 (网站注册记录, 网页历史存档) 都没有 HTTP 响应: 当前环境不能联网 | 换模式 B; 做不了就写 `failed` 短报告, 说明是环境不能联网, 不是网站不存在 |
| `site_unreachable` | 首页没有任何 HTTP 响应 (域名不存在或拼错), 店铺端点全部失败, 但站外查询有响应 | 走 SKILL.md "失败时": 写 `failed` 短报告并用 deliver.py 交付 |
| `json_extracted:<file>` / `json_<状态>:<file>` | 从带包裹或损坏的文本里抽取了 JSON | 注明该数据经过修复, 可信度降一级 |

`summary.json` 中还可能出现以上未列的字段 (例如 `products_json.pagination_complete`, `shop.published_products_count`, `html_signals.frameworks_possible`); 含义不明确的字段不要引用进报告.
