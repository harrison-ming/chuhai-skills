# 收款问卷

全部可选, 不知道就跳过 (记 "不知道", 不要替用户猜). **一次最多问 6 个**, 优先问会改变结论的: 主体在哪, 用哪些通道及各占多少, 客单价和单量, Shopify 套餐, 提现到哪.

## 1. 大白话问法 (按需挑)

1. 收款用的公司 (或个人) 注册在哪? 中国大陆, 香港, 美国, 加拿大, 英国, 其他?
2. 打算用哪些收款方式, 大概各占多少订单? 例: PayPal 四成, 信用卡通道六成. 信用卡通道是哪家, 合同费率多少?
3. 平均每单多少钱 (哪种货币)? 每月大概多少单?
4. 用的是 Shopify 哪个套餐 (Basic / Grow / Advanced / Plus), 还是别的建站平台?
5. 钱最后提到哪: 国内银行, WorldFirst, Payoneer, PingPong, 连连, Airwallex, 美国银行? 每月提几次? 换汇费率知道吗?
6. 主要卖到哪些国家, 各占多少? 卖什么品类? 有没有订阅或预售?

## 2. 字段 (`questionnaire.py --set 字段=值`)

| 字段 | 取值 |
|---|---|
| `entity_country` | `CN` `HK` `US` `CA` `UK` `OTHER` (也认 大陆/香港/美国 等) |
| `entity_type` | `company` `sole_proprietor` `individual` |
| `payout_account` | `cn_bank` `worldfirst` `payoneer` `pingpong` `lianlian` `airwallex` `mercury` `us_bank` `other` `none` |
| `shopify_plan` | `basic` `grow` `advanced` `plus` `none` (不是 Shopify 填 none) |
| `mor` | 记录卖方 (代收代缴): `no` `shopify_managed_markets` `stripe_managed_payments` `other` |
| `aov`, `aov_currency` | 客单价数字, 币种 (USD, CAD...); 币种没说按 USD 计并写进假设 |
| `monthly_orders` | 每月单量 |
| `market_mix` | `US:70,CA:30` |
| `intl_card_share`, `fx_share` | 境外卡占比, 需要换汇的订单占比 (百分数) |
| `withdrawals_per_month`, `payout_fx_pct` | 每月提现次数, 提现换汇费率 (百分数) |
| `payout_currency` | 提现币种 |
| `refund_rate`, `dispute_rate` | 退款率, 拒付率 (百分数, 可选) |
| `category` | 品类原话 |
| `sells_to_cn`, `subscription`, `preorder` | yes / no |
| `ship_days` | 发货天数 |

通道用 `--gateway 通道:占比[:费率+固定费[:每次提现费]]`, 通道名: `shopify_payments` `stripe` `paypal` `airwallex` `card_other` (其他信用卡通道) `manual` (货到付款, 银行转账). 多条通道写多次 `--gateway`, 会整体替换上一次的通道列表.

## 3. 报告里怎么用

- 问卷内容标 `[你提供的]`, 正文写 "你提供的: ...", 不升级成 `[已核实]`; 用户发了后台截图或账单, 截图上直接可见的字段才能写 `[已核实]`, 文件存进 `原始数据/user/`.
- 问卷与页面冲突 (例: 页面挂 Amex 图标, 你说只用 PayPal) 标 `[有矛盾]`, 请用户确认, 不替用户判断谁对.
- 用户没回答的项不追问超过一轮; 报告 "还有哪些没查清" 写明缺什么, 补了能多算什么.
