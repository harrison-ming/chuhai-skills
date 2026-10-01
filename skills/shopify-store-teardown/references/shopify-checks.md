# Shopify 平台层检查

本文件只管 Shopify 平台层. 商业判断见 `business-analysis.md`.

> Shopify 的套餐, 限制, Markets, 支付和结账能力会变. 报告里写到具体平台能力时, 查当天的官方资料 (help.shopify.com, shopify.dev) 并记下访问日期.

## 1. 公开数据端点 (L0 第一步)

每个端点都记下 URL, HTTP 状态, 访问时间. 端点可能被店铺关掉或被防火墙拦截: **缺失只记状态, 不能当作"不是 Shopify"的证据.**

| 端点 | 能拿到什么 | 用法与纪律 |
|---|---|---|
| `/meta.json` | 店名, shop id, `myshopify_domain`, 基础币种, 国家, `ships_to_countries` | 身份信号. shop id 偏小暗示开店早, 只算 `[推测]` 弱证据 |
| `/products.json?limit=250&page=N` | 全部公开商品: 价格, 变体, 选项, tags, 类型, `created_at` / `published_at` | 商品量, 价格带, 上新节奏, 变体策略. 翻到空数组为止; page x limit 上限 25000. 价格是**基础币种**, 各市场价要另查 `/<market>/products/<handle>.js` |
| `/sitemap.xml` 的商品子地图 | 商品 URL 数 | 与 products.json 交叉核对商品量 |
| `/collections.json` | 集合列表 | 只用来看分类方式. **其中的 `products_count` 常常虚高 (可达真实商品数的数倍), 不得当商品量** |
| `/collections/all?sort_by=best-selling` | 按"畅销"排序的商品顺序 | 只能当相对排名的线索, 标 `[推测]`: 排序由商家配置, 不是销量数据 |
| 首页源码中的 `Shopify.theme` / `Shopify.shop` | 主题名, `schema_name`, `theme_store_id`, myshopify 域名 | 主题的 E1 证据, 身份信号 |

## 2. 身份确认

`summarize.py` 按**信号个数**判定, 不区分来源: ≥2 个为 `confirmed`, 1 个为 `probable`. 脚本认的信号有 4 个:

- `/meta.json` 返回 `myshopify_domain`
- `/products.json` 返回 Shopify 结构 (products 数组, 含 variants)
- 首页源码含 `Shopify.shop`, `Shopify.theme`, `cdn.shopify.com` 或 `myshopify.com` (同一页面里出现几个都只算 1 个信号)
- sitemap 含 `sitemap_products_*` 子地图

`meta.json` 和 `products.json` 同属 Shopify 的公开数据接口. 如果只有这两个信号, 脚本也会给 `confirmed`, 但 agent 写 `[已核实]` 前要再找一个非接口信号佐证: 首页源码里的 `Shopify.shop` / `cdn.shopify.com`, 或 sitemap 的商品子地图. 品牌或官方资料明确说明使用 Shopify, 也可以作为佐证.

**不能单独作为依据**: 页面长得像某个 Shopify 主题; 有 Shop Pay 图标; 某个技术识别网站的单次结果; 历史缓存里出现过 Shopify 脚本.

输出:

- `confirmed`: ≥2 个信号.
- `probable`: 1 个信号.
- `not_shopify`: 0 个信号, 且首页正常取得, meta.json / products.json / 首页 / sitemap 都没有被截断, 每个都是正常返回或 404 (404 也算可访问).
- `unconfirmed`: 其余情况 (0 个信号, 但有端点被拦, 出错, 被截断, 或首页没取到).

## 3. Storefront 类型

区分: 传统主题 / Online Store 2.0 主题 / Headless / Hydrogen / 无法确认. **Headless 不等于 Hydrogen**, 没有足够技术信号就不贴 Hydrogen 标签.

## 4. 主题

- 优先读源码 `Shopify.theme`: `schema_name` 就是主题来源.
- `schema_name` 是自定义名称且 `theme_store_id` 为 null: `[推测]` 这是经过 Git 连接定制的主题.
- 读不到这个对象, 又没有其他直接证据: 写 `unknown`, **不按样式猜主题名**.
- 用免费主题不是负面结论, 重点是信息架构和执行质量.

## 5. 商品, 选项与变体

- 看商品, 集合和 tags 怎么组织; 选项名和变体组合; 售罄, 预售, 定制字段.
- 变体数量上限等平台限制会更新, 引用时查当天官方文档.
- 集合在后台是自动还是手工, 从前端**推不出来**, 不要当事实写.

## 6. 应用与第三方脚本

- 源码里出现某应用的字符串, 可能是残留, 延迟加载或主题自带功能. 同一个商品页同时出现三家评价应用的痕迹并不罕见.
- 可信度分四档: `confirmed` (渲染出的挂件与该应用吻合, 需要浏览器) / `probable` (多个独立迹象) / `possible` (只有源码字符串) / `unknown`.
- 本 skill 的脚本只能给到 `possible`.

## 7. 结账与支付

前端能看到的很有限, 只能这样写:

- `[页面显示]` 页面显示了某支付图标或快捷按钮.
- `[查不到]` 商户后台是否开通, 是否所有市场可用, 交易能否成功.

**禁止**仅凭图标断言 Shopify Payments 已开通, PayPal 已连接, 或某支付方式在所有地区可用. 不进入结账, 不提交订单, 不用虚假信息测试.

## 8. Markets 与本地化

- 看: 主域名 / 子域名 / 地区子目录; 国家和语言选择器; `hreflang` 与 canonical; 市场专属内容和政策.
- 平台**支持**某能力, 不等于店铺**配置好了**. 源码里有 hreflang 才能说"输出了 hreflang".
- 公开页面只能确认展示结果, 确认不了后台结算安排.

## 9. 政策与运营配置

- 查 Shipping, Returns/Refund, Privacy, Terms, Contact 五类政策页: 在不在, 是不是平台默认模板 (正文极短), 公司名称和地址是否写明.
- L1 必做**政策一致性对照**: 至少比对 ① 发货/处理天数 ② 免费退货的适用范围 ③ 退货条件 ④ 站内评价数 vs 独立评价平台 ⑤ About 的创始人故事 vs 站外资料. 商品页或首页的说法与政策原文不一致, 标 `[有矛盾]`.

## 10. 输出字段

报告附录的"技术细节"至少给出 (正文只用一两句大白话概括, 例如"用的是自己定制的店铺模板"):

```text
shopify_identity: confirmed|probable|unconfirmed|not_shopify (+ 信号列表)
storefront_type: theme_os20|theme_legacy|headless_hydrogen|headless_other|unknown
theme_claim: {value, confidence, evidence}
apps_or_services: [{name, confidence, evidence}]
payment_surfaces_observed: [...]   # 仅 [页面显示]
markets / languages / currencies observed: [...]
unknowns: [...]
```

这些字段描述公开可见的店面, 不代表拿到了商家后台权限.
