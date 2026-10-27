# 方法: 开店上线前体检

写给 agent 看. 检查目录 (编号, 级别, 市场, 依据来源, 怎么改) 的唯一原文是 `rules.json`; 本文只讲数据从哪来, 怎么判, 标什么, 哪里容易误判.

## 1. 流程与文件

| 步骤 | 脚本 | 产出 (在 `原始数据/` 下, 英文 `data/`) |
|---|---|---|
| 采集 (模式 A) | `audit_collect.py` | `raw/` 页面文件, `raw/pages.json` 索引, `raw/_status.json` 请求记录 |
| 粘贴 (模式 P) | `paste_ingest.py` | 同上结构, `mode: paste` |
| 判定 | `audit.py` | `audit.json` (每条检查的结果), `summary.json`, `claims.jsonl` (抽取到的说法), `evidence.jsonl`, `report.draft.md` |
| 清单 | `export_fixlist.py` | `整改清单.csv` (`--drop` 去掉复核后的误判) |
| 交付 | `check_report.py`, `deliver.py` | PDF 或 HTML, CSV, `delivery.json` |

`raw/` 结构: `homepage.html`, `policies/<handle>.html` (7 类: refund-policy, shipping-policy, privacy-policy, terms-of-service, contact-information, legal-notice, subscription-policy), `pages/<序号>-<路径>.html` (联系, 常见问题, 关于, 非 Shopify 的政策页), `pdp/*.html` (抽样商品页: 入门 ~P25, 主力 ~中位, 高价 = 最高), `markets/<代码>/homepage.html` `product.js` `refund-policy.html`, `products.json`, `meta.json`, `robots.txt`, `sitemap.xml`.

## 2. 结果与标签

`audit.json` 的 `findings[]`: `id, result, level, badge, where, evidence[], note, sources[]`.

| result | 含义 | 去处 |
|---|---|---|
| `fail` | 发现问题 | 整改清单 + 报告对应一节 |
| `manual` | 需要用户自己核对 (后台, 浏览器) | 整改清单 (状态"待核对") |
| `pass` | 查了, 没发现问题 | 附录明细 |
| `unknown` | 没查到 (被拦, 超时, 粘贴模式看不到线上页面) | "还有哪些没查清", 标 `[查不到]` |
| `skip` | 不适用 (不卖该市场, 不投广告, 单一市场, 非 Shopify) | 附录明细 |
| `same` / `info` | 与另一条重复 / 仅提示 | 附录明细 |

- 页面事实: 模式 A `[已核实]`, 模式 P `[店铺自称]` (来源: 你提供的文本), 模式 B 同模式 A 但完成状态最多 `partial`.
- 对照法规或官方要求的判断 (EU-, UK-, CA-, AU-, US-, CON-05, GMC-06, PRV-02): `[推测]` + 依据 (rules.json 的来源 S 编号).
- 冲突 (CNS-, GMC-08, DE-01): `[有矛盾]`, 两段原文都要引用.
- **级别**: P0 只能由已核实来源支撑; 经验项 (`exp`) 自动降到 P1 (TRU-01 例外: 一眼像假店). CON-06 对 AU/EU/UK 是 P0, 只卖美国时 P1. 政策页存在但页脚没链接, 从 P0 降到 P1.
- **"缺页"的口径**: 政策网址返回找不到 **且** 首页页脚/页头没有相应链接 → `fail`. 被拦截或超时 → `unknown`. 页脚有链接但那页读不到 (例如要 JavaScript 才显示) → `unknown`.
- 结论: P0 为 0 → 可以上线; 1-2 条 → 改完 P0 再上线; 3 条以上 → 先别上线; 首页没读到或只有密码页 → 无法判断. 结论本身是判断, 报告里标 `[推测]`.

## 3. 目标市场

`--markets` 给了就用; 没给用"页面上能看到的市场" (多语言网址标注里的地区, 国家/币种选择器里的国家) 加美国, 报告第一节写明. 法规类检查只对目标市场生效. 语言检查 (GMC-04) 只看用户指定的市场, 或有独立语言版本的市场, 避免选择器里上百个国家都要求翻译.

## 4. 完成状态

- `failed`: 模式 A 首页打不开或被拦截.
- `partial`: 密码页, 粘贴模式, 手动模式, 非 Shopify, 首页基本靠 JavaScript 渲染, 关键页面 (退货/运费/隐私政策, 商品数据) 被拦截.
- `complete`: 其余.

## 5. 手动取数 (模式 B)

在 `<运行目录>/原始数据/raw/` 下按文件名保存 (没有 `pages.json` 时 `audit.py` 按文件名推断角色):

| 文件 | 内容 |
|---|---|
| `homepage.html` | 首页 (含页头页脚); 只有文本也行, 但页脚链接类检查会变 `unknown` |
| `policies/refund-policy.html` 等 | `<网址>/policies/<handle>` 的正文 |
| `pages/contact.html`, `pages/faq.html` | 联系页, 常见问题页 |
| `pdp/<任意>.html` | 1-3 个商品页 |
| `markets/<语言-地区>/homepage.html` | 其他市场版本的首页 |
| `robots.txt`, `sitemap.xml` | 原样保存 |

网页读取工具返回的是截断或转写过的内容, 报告附录要注明, 完成状态写 `partial`.

## 6. 模式 C (人工核对)

按 `rules.json` 的顺序逐条看: 先 POL (政策页齐不齐), CON (内容够不够), CNS (公告栏/商品页/常见问题/政策的退货天数, 送达时长, 退货运费, 包邮门槛是否一致), 再按目标市场看 EU/DE/UK/CA/AU/US, 最后 LOC, TRU, SEO. 标签规则同第 2 节, 完成状态最多 `partial`.

## 7. 已知误判与对策

- 质保期 ("30-day warranty", "30 天质保") 不算退货期: 抽取时已排除含质保字样且不含退货字样的句子.
- 退款到账时长 ("processed within 10 business days") 不算退货期.
- 同一来源内的多个数字不算矛盾 (例如会员 60 天, 普通 30 天); 只比较不同来源的首个说法. 仍可能误判: 一处说美国, 一处说国际. **CNS 每条都要读两段原文**.
- 送达时长区间有重叠不算矛盾 (5-10 天 vs 7-12 天).
- 主体名: 平台和服务商名称 (Shopify, PayPal 等) 不参与比较; 一个名字包含另一个不算矛盾.
- "No refunds" 若在礼品卡, 定制品, 贴身用品, 运费, 关税相关句子里, 不算一律不退.
- Cookie 同意组件只按脚本特征识别, 看不出实际效果 (PRV-04 永远 `unknown`, Pro 版浏览器实测).
- 英文占比 (LOC-05) 按常见英文词比例估算, 内容少于 30 个词不判.
