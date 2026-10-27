# 方法: 收款上线体检

写给 agent 看. 检查规则原文在 `checks.md`, 费用算法在 `fees.md`, 问卷在 `questionnaire.md`, 报告结构在 `report-template.zh.md` / `.en.md`.

## 1. 脚本分工

| 脚本 | 联网 | 作用 |
|---|---|---|
| `collect.py <网址>` / `--offline <名称>` | 是 / 否 | 建运行目录, 只读抓公开页面存 `raw/`, 每个请求记进 `raw/_status.json`; 结束后自动跑 `compute.py` |
| `questionnaire.py <运行目录> --set ... --gateway ...` | 否 | 校验并写入 `questionnaire.json`, 自动重算 |
| `compute.py <运行目录>` | 否 | 读 `raw/` + 问卷, 跑规则 (`pr_checks.py`), 算费用 (`fees.py`), 写 `summary.json`, `findings.json`, `fees.json`, `evidence.jsonl`, `report-draft.md` |
| `check_report.py <运行目录>` | 否 | 报告自检 (技术词, 标签, 禁用说法, "图标写成已开通") |
| `deliver.py <运行目录>` | 否 | 报告转 PDF (无浏览器时 HTML), 写两张 CSV 和 `delivery.json` |

采集范围 (只 GET, 顺序请求, 默认间隔 0.8 秒): 首页, 店铺公开信息 (`meta.json`), 商品数据第一页 (最多 250 款), 5 个政策页 (`/policies/...`, 不在这里就从首页链接里找), 空购物车页 `/cart`, 最多 3 个商品页 (最便宜, 中位, 最贵), 联系页, FAQ 页. 不碰结账: 被跳转进结账的页面不保存.

## 2. summary.json 字段

| 字段 | 含义 |
|---|---|
| `status` | `complete` 全部取到 / `partial` 部分没取到, 不是 Shopify, 平台没确认, 或离线测算 / `failed` 首页都没取到 |
| `mode` | `online` 脚本联网 / `manual` 手动存页面 (模式 B) / `offline` 没有网站 |
| `warnings` | 见下表 |
| `identity` | `confirmed` / `probable` / `not_shopify` / `unconfirmed` / `unknown` |
| `visitor_market` | 本次访问被识别成的国家和看到的币种 (店面按访问地区显示, 报告要写 "从 XX 访问看到的版本") |
| `counts` | 各优先级问题条数, `unknown` 为没查清的项数 |
| `fees` | 费用合计摘要; 明细在 `fees.json` |
| `fix_list` / `fee_rows` | 两张 CSV 的行 |
| `rates` | 费率快照日期与是否过期 |

| warning | 报告里怎么说 |
|---|---|
| `homepage_unavailable`, `blocked` | 网站没打开或拦截了自动访问; 拿不到不等于网站有问题 |
| `endpoints_failed:<项>` | 这些页面没取到, 列进 "还有哪些没查清" |
| `not_shopify` / `platform_unconfirmed` | 不是 (或不确定是) Shopify, Shopify 专项和平台附加费跳过 |
| `offline_no_store` | 网站没上线, 只做了主体通道检查和费用测算 |
| `manual_collection` | 页面是手动保存的, 可能不全 |
| `no_questionnaire` | 没填收款情况, 没算手续费, 主体通道没查 |
| `fees_missing_inputs:<项>` | 费用缺这些输入没算 (客单价, 单量, 通道, 各通道占比) |
| `rates_stale` | 费率快照超过 90 天, 以官网和合同为准 |

`findings.json` 每条: `id`, `group`, `severity` (P0-P3 / info), `result` (`issue` 要改 / `ok` / `unknown` 没查清), `badge`, `check`, `finding`, `basis` (推测的依据), `fix`, `who`, `where` (证据文件或官方来源编号 S*).

## 3. 手动取数 (模式 B)

先 `collect.py --offline "<域名>"` 建运行目录, 再把网页读取工具拿到的内容存进 `<运行目录>/原始数据/raw/`. 后缀可以是 `.html`, `.md` 或 `.txt` (网页读取工具常把页面转成 markdown, 照存即可, 报告注明 "手动保存").

| 文件名 | 内容 |
|---|---|
| `homepage.html` | 首页 (必需; 没有就是 `failed`) |
| `meta.json` | `<网址>/meta.json` (Shopify 店才有) |
| `products-1.json` | `<网址>/products.json?limit=250` |
| `policy-refund-policy.html` 等 5 个 | `/policies/refund-policy`, `shipping-policy`, `privacy-policy`, `terms-of-service`, `contact-information`; 非 Shopify 站用首页上对应链接 |
| `cart.html` | `/cart` (只看空车) |
| `page-product-<名>.html` | 1-3 个商品页 |
| `page-contact.html`, `page-faq.html` | 联系页, FAQ 页 |

网页读取工具通常看不到脚本和图标源码, 所以模式 B 的图标/脚本检查常是 "查不到", 如实写.

## 4. 已知坑

- 店铺按访问者地区显示币种和内容; 报告写明 "从 XX 访问看到的版本", 不要当成所有市场都一样.
- 空购物车多半不显示结账按钮, 这不是问题; 本工具不加购, 结账按钮记为没查清.
- 支付图标在 Shopify 上通常随后台开通的付款方式显示, 但仍只能写 `[页面显示]`; 脚本痕迹可能是卸载应用后的残留.
- 国内常见聚合通道 (Asiabill, Oceanpayment, PingPong 等) 的脚本特征还没用真实样本校准, v1 不识别, 不要凭印象补.
- 官方费率页按访问者 IP 跳转地区版本, 所以费率不在运行时抓, 用随技能附带的 `rates.json` 快照 (写明取数日期), 用户合同费率优先.
