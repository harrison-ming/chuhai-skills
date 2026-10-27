# 输入表: 字段, 单位, 缺失时怎么办

`inputs.csv` 是唯一输入源 (问卷和 CSV 模板都落成它). 长表, 一行一个输入:

| 列 | 说明 |
|---|---|
| `商品` | 商品或售卖形态名 (如 `宠物梳`, `宠物梳-两件装`); `全店` 表示店铺级, 对所有商品生效, 商品行覆盖全店行 |
| `项目` | 下表的中文名, 别名或英文键都认 |
| `基准` | 必填; 百分比写 15 表示 15%, 可带 `%` |
| `保守` / `乐观` | 可空, 空则沿用基准 (报告写 "未给区间") |
| `单位` | 金额必须写 `CNY` 或售价币种 (`USD` `EUR` `GBP` `CAD` `AUD`); 百分比 `%`; 其他可空 |
| `来源说明` | 自由填 (如 "1688 报价截图 10/20"), 原样进报告 |

英文表头 `product,item,base,conservative,optimistic,unit,note` 同样可用; 以 `#` 开头的行是注释. 模板: `templates/inputs-template.zh.csv` / `.en.csv` (UTF-8 BOM, Excel/WPS 可直接打开). 免费版最多算 2 个商品 (售卖形态), 多出的不算并在报告里提示.

## 字段 (问卷顺序; ★ 必填)

| 组 | 中文项目 | 英文键 | 单位 | 缺失时 |
|---|---|---|---|---|
| 市场 | 销售市场 ★ | `market` | US / UK / CA / AU / EU-DE ... | 阻断 |
| | 售价币种 | `sale_currency` | - | 按市场推出 `[推测]` |
| | 汇率 ★ | `fx_cny_per_unit` | 1 单位售价币种 = ? 元 | 联网取欧洲央行参考汇率 (带日期, `[已核实]`, 状态 partial); 取不到则阻断 |
| | 汇率日期 / 汇率来源 | `fx_date` `fx_source` | - | 写 "未注明日期" |
| 收款 | 收款方式 ★ | `payment_provider` | Shopify Payments / Stripe / PayPal 中国账户 / PayPal 美国账户 / PayPal 欧洲账户 / 其他 | 给了收款费率时记 "未说明" `[推测]`; 都没给则阻断 (中国大陆主体能否开 Shopify Payments 因主体而异, 不替用户假设) |
| | 收款主体 | `seller_entity` | 中国大陆 / 香港 / 美国 | 不填; 中国大陆 + Shopify Payments 且没给费率时阻断 (费率只在后台可见) |
| | Shopify 套餐 | `shopify_plan` | Basic / Grow / Advanced / Plus / 不用 Shopify | Basic `[推测]` |
| | 收款费率, 收款每笔固定费 | `payment_pct` `payment_fixed` | %, 币种 | 取费率表 (美国 Shopify Payments 按美国主体标准卡费率, 记 `[推测]` 且状态 partial); 表里没有则阻断 |
| | 跨境卡附加费 | `cross_border_pct` | % | 0 `[推测]`, 附费率表参考值 |
| | 结汇换汇价差 | `fx_conversion_pct` | % | 0 `[推测]`, 附费率表参考值 |
| | Shopify 第三方交易费 | `third_party_txn_pct` | % | Shopify Payments: 0 (`[已核实]` 不收); Stripe / PayPal 且用 Shopify: 按套餐取费率表; 通道未知: 0 `[推测]` |
| 商品 | 售价 ★ | `price` | 币种 (这个售卖形态的标价, 两件装填套装价; 欧盟填含税价) | 阻断 |
| | 每单件数 | `units_per_order` | 件 | 1 |
| | 平均折扣 | `discount_pct` | % | 0 |
| | 向买家收的运费 | `shipping_charged` | 币种 | 0 (包邮) |
| | 增值税率 | `vat_pct` | % | 欧盟 / 英国阻断; 美国等不适用 |
| 货品 | 采购单价 ★ | `product_cost` | CNY 或币种 | 阻断 |
| | 包装 | `packaging` | CNY 或币种 | 0 |
| | 头程 (每件) | `first_mile_per_unit` | CNY 或币种 | 0 (直发小包可为 0) |
| 关税 | 关税合计税率 | `duty_rate_pct` | % | **不给默认值**: 按 "未含关税" 计, 标 `[查不到]`, 另出 "每 +10 个百分点" 一行, 状态 partial |
| | 关税计税基数 | `duty_basis` | 按货值 (批量进仓) / 按售价 (直邮) | 按货值 `[推测]` |
| | 每单固定税费 | `duty_fixed_per_order` | 币种 | 0 |
| 履约 | 拣货打包 (每单) | `fulfil_per_order` | 币种 | 0 |
| | 仓储 (每件) | `storage_per_unit` | 币种 | 0 |
| | 尾程运费 ★ | `last_mile_per_order` | 币种 | 阻断 (直邮填全程运费) |
| 退货 | 退货率 | `return_rate_pct` | % | 试算档 保守 20 / 基准 10 / 乐观 5 `[推测]`, 状态 partial |
| | 每次退货运费 | `return_ship_per_return` | 币种 | = 尾程运费 `[推测]` |
| | 退回货可再售比例 | `resale_pct` | % | 0 `[推测]` |
| | 退货处理方式 | `return_mode` | 退款 / 补发 | 退款 |
| 其他 | 其他每单成本 | `other_variable_per_order` | 币种 | 0 |
| 广告 | 获客成本 或 广告 ROAS | `cpa` / `roas` | 币种 / 倍 | 不给默认值: 只出盈亏平衡值, 月单量按获客成本 = 0, 50%, 100% x 最高获客成本三档 |
| | 目标利润率 | `target_margin_pct` | % | 15 `[推测]`, 只用于目标价 |
| 月固定 | 套餐月费 | `plan_fee_month` | 币种 | 美国站按套餐取费率表; 其他市场 `[查不到]` |
| | 插件月费, 海外仓月最低消费, 其他月固定成本 | `apps_month` `3pl_min_month` `other_fixed_month` | 币种 | 0 |
| 启动 | 首批数量, 最小起订量 | `moq` `min_moq` | 件 | 对应路线的货款等项 `[查不到]` |
| | 打样费, 保证金, 海外仓入仓费, 测款广告预算 | `sample_cost` `deposit` `inbound_3pl_fee` `launch_ad_budget` | CNY 或币种 | `[查不到]` (不计入合计) |
| | 回本前月数 | `runway_months` | 个月 | 3 `[推测]` |
| | 生产天数, 运输天数, 工厂定金比例, 回款天数, 日销件数 | `production_days` `transit_days` `supplier_deposit_pct` `payout_delay_days` `daily_units` | 天 / % / 件 | 现金周期只列已知项 |
| 定价 | 竞品最低价, 竞品最高价 | `competitor_price_low` `competitor_price_high` | 币种 | 不抓取, 不写对照 |

Pro 版另认: 销售占比 `mix_pct`, 亚马逊佣金率, FBA 配送费, 亚马逊月费, TikTok 佣金率, 达人佣金率, 站内广告占比, 复购次数, 复购间隔天数, 月销件数, 手头现金.

## 校验

| 级别 | 规则 | 处理 |
|---|---|---|
| 阻断 | 必填缺失; 金额不是数字; 售价 ≤ 0; 采购价 < 0; 金额没写币种或币种既不是 CNY 也不是售价币种 | 该商品不算, 写进 "需要你补的" |
| 错误 → 视为缺失 | 百分比 < 0 或 ≥ 100; 件数不是正整数; 天数 < 0; 选项不认识 | 按 "缺失时" 处理, "请核对" 写原值与处理 |
| 警告 | 百分比填了 0 到 1 的小数 (疑似把 8% 写成 0.08); 汇率在 USD 5-9 / EUR 6-10 之外; 保守比基准还乐观; 售价低于落地货本; 同一项目重复; 不认识的项目 | 照算, "请核对" 逐条列出 |

"保守" 方向按字段固定: 成本, 费率, 退货率, 折扣取高; 售价, 汇率, 可再售比例取低. 填反时警告但尊重原值.
