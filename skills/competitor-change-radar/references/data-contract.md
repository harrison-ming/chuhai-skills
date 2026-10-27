# 数据契约

`radar.py snap` 联网写快照; `normalize.py` 离线把原始文件变成 `snapshot.json`; `radar.py diff` 离线比较两份 `snapshot.json`, 写 `diff.json` + 给你的底稿 `diff.md` + 表格数据; 你写 `report.md`; `check_report.py` 自检; `deliver.py` 出 PDF (或 HTML) 和 CSV.

## 1. 目录

交付根: `--out-root` > 环境变量 `CHUHAI_OUTPUT_DIR` > 云端沙箱输出区 > 用户 "文档/出海拆解报告" (与 shopify-store-teardown 同一个根, 英文版 `Store teardowns`). 定时任务 (Pro) 写 `~/出海拆解报告/`; 正常根下没有某店历史而那里有时, 脚本自动用那里.

```text
<根>/雷达/<域名>/                       # 英文版: radar/<domain>/
├── radar.json                          # 索引 radar/index/1, 可由快照重建
├── 快照/                               # 英文版: snapshots/
│   ├── 2026-10-20/                     # 一次快照 = 一个 UTC 日期; 同日再抓 _2
│   │   ├── snapshot.json               # radar/snapshot/1, diff 只读它
│   │   ├── snapshot-meta.json          # 来源, 抓取时间, 存档时间戳
│   │   └── 原始数据/raw/ summary.json  # 与 teardown 同构
│   ├── 2026-09-27-archive/             # 网页历史存档基线
│   └── 2026-09-30-teardown/            # 导入的对标拆解
└── 变化报告 2026-10-20 至 2026-10-27/   # 英文版: Changes <a> to <b>; 只有一份时 基线报告 <日期>
    ├── <店名> 竞品变化报告.pdf          # 无浏览器时 .html, 同样是完整交付
    ├── 价格变动.csv  上新下架.csv  全部变化.csv   (基线报告: 当前商品清单.csv)
    └── 原始数据/ diff.json (或 baseline.json) diff.md tables.json run.json report.md report.html delivery.json 说明.txt
```

- 域名统一去掉 `www.`, 同店不同写法 (带路径, 商品页链接) 落同一目录.
- 写快照时先写 `.<id>.partial/`, 完成后改名; 中断留下的 `.partial` 下次清理 (只清本工具建的).
- 保留: `snapshot.json` 永久; 第一份和最近 4 份快照保留明文 `raw/`, 更早的打包为同目录 `raw.zip` (校验通过才移除明文). 工具从不删除用户文件; 唯一会被整体重建的是演示目录 `雷达/demo-outdoors.example/`.

## 2. `snapshot.json` (radar/snapshot/1)

```json
{"schema": "radar/snapshot/1", "domain": "x.example", "snapshot_id": "2026-10-27",
 "captured_at_utc": "2026-10-27T08:00:00Z", "source": "live|archive|teardown_import",
 "status": "complete|partial|blocked|not_shopify", "identity_status": "confirmed|probable|unconfirmed|not_shopify",
 "fetch_error": "network|null",
 "coverage": {"products": "full|partial|none", "pagination_complete": true, "policies": ["refund-policy"],
              "policies_checked": {"refund-policy": "ok"}, "homepage": true, "collections": true, "meta": true},
 "shop": {"name": "", "currency": "USD", "currency_source": "meta|homepage", "country": "US", "ships_to_count": 40},
 "theme": {"name": "", "schema_name": "", "schema_version": "", "theme_store_id": null},
 "signals": {"apps_possible": [], "tracking_tags": [], "hreflang": [["en-us", "/"]],
             "localization_form": "observed|not_observed|null", "payment_icons_observed": []},
 "products": {"<product_id>": {"handle": "", "title": "", "vendor": "", "product_type": "", "tags": [],
              "created_at": "", "published_at": "", "virtual": false,
              "variants": {"<variant_id>": {"title": "", "sku": "", "options": ["M"], "price": "95.00",
                                           "compare_at_price": "120.00", "available": true}}}},
 "collections": {"<handle>": {"id": 1, "title": "", "products_count": 9}},
 "homepage": {"lines": ["..."], "promo_lines": ["..."]},
 "policies": {"refund-policy": {"text_sha256": "", "sentences": ["..."], "key_terms": {"return_days": 30}}}}
```

- 价格存字符串 (两位小数), 比较用十进制, 不用浮点. 不收录更新时间, 图片地址, 商品描述.
- `status`: `complete` = 商品翻页到底且确认是 Shopify; 存档来源最多 `partial`. `fetch_error: network` = 所有请求都没拿到任何网络回应 (域名解析失败, 沙箱不能联网): 报告要写 "连不上这个网址, 可能网址不存在也可能环境不能联网", 不能写成店铺被拦.
- 没有商品编号的数据 (手动或存档) 用 `handle:<网址>` 作键.

## 3. `diff.json` (radar/diff/1)

```json
{"schema": "radar/diff/1", "domain": "x.example", "shop_name": "", "currency": "USD",
 "from": {"snapshot": "2026-10-20", "captured_at_utc": "...", "source": "live", "status": "complete", "products": 8, "pagination_complete": true},
 "to": {"snapshot": "2026-10-27", "...": "..."},
 "interval_days": 7.0, "status": "complete|partial|failed",
 "comparable": {"products": true, "prices": true, "removals": true, "policies": ["refund-policy"], "homepage": true, "collections": true, "markets": true},
 "counts": {"product_added": 2, "product_removed": 0, "price_down": 2, "products_price_down": 1, "...": 0},
 "cadence": {"added": 2, "added_per_week": 2.0, "published_by_week": {"2026-W43": 1}, "published_by_month": {"before": {}, "after": {}}},
 "promo_share": {"before": 14.29, "after": 20.0},
 "price_changes": [{"product_id": "1001", "title": "Rain Shell", "before": "110.00", "after": "95.00", "delta_pct": -13.64, "on_sale_after": true, "event_id": "R003"}],
 "events": [{"event_id": "R003", "type": "price_down", "severity": "high|medium|low",
             "product_id": "1001", "handle": "rain-shell", "title": "Rain Shell", "variant_id": "2001", "variant": "M",
             "field": null, "before": "110.00", "after": "95.00", "delta_pct": -13.64, "currency": "USD", "virtual": false,
             "badge": "verified|page_shown|store_claim", "details": {},
             "evidence": {"from_locator": "快照/2026-10-20/原始数据/raw/products-*.json",
                          "to_locator": "快照/2026-10-27/原始数据/raw/products-*.json", "url": "https://x.example/products/rain-shell"}}],
 "warnings": [], "errors": []}
```

- 事件类型与判定见 `method.md` 第 3 节. `counts` 不含虚拟商品; `price_down`/`price_up` 按规格计, `products_price_down`/`products_price_up` 按商品计.
- 价格事件按规格记; 折扣, 库存事件按商品记. 政策事件的 `field` 是关键条款名 (`return_days` 等) 或 `text`.
- 确定性: 同样输入得到逐字节相同的输出 (事件按 重要程度, 类型, 商品编号, 规格编号 排序后编号; 键排序; 两位小数; 不读系统时间).
- 字段只增不改; 语义变化升 `radar/diff/2`.
- 维度不可比写进 `comparable`, 不得当 "没变化".

## 4. `warnings` 取值

| 值 | 含义 | 报告怎么写 |
|---|---|---|
| `mass_removal_suspect` | 一次少了三成以上商品 | 不判下架, 建议过一两天再抓 |
| `baseline_incomplete` | 旧快照没读全, 新增过半 | "多出来" 的商品部分可能早就在卖 |
| `archive_baseline` | 旧的一份来自存档 | 不判下架, 最高 [页面显示] |
| `prices_skipped_currency_changed` | 基础币种变了 | 这一期不比价格 |
| `currency_source_mismatch` | 两次币种来源不同 | 币种是否变了无法确认 |
| `interval_short` / `interval_long` | 间隔 < 20 小时 / > 45 天 | 变化少属正常 / 中间来回变化看不到 |
| `ambiguous_identity` | 有商品对不上号 | 没有强行配对 |

## 5. 其他文件

- `run.json`: `lang`, `kind` (`diff` / `baseline`), `domain`, `shop_name`, `status`, `demo`, `from`, `to`, `next_due`.
- `tables.json`: CSV 行 (`price_changes`, `product_changes`, `all_events`; 基线报告 `current_products`). 列见 `scripts/skill_config.py` 的 `CSV_TABLES`.
- `baseline.json` (radar/baseline/1): 单份快照的商品清单, 近 30/90 天发布数, 在打折占比, 首页促销, 政策要点.
- `radar.json` (radar/index/1): `domain`, `cadence_days` (默认 7), `next_due`, `snapshots[]` (`id`, `captured_at_utc`, `source`, `status`, `products_count`, `pagination_complete`), `reports[]`. 损坏时 `radar.py status <店> --rebuild` 从快照重建.
