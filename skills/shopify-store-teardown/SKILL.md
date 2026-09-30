---
name: shopify-store-teardown
description: >-
  拆解任意公开 Shopify 独立站 (对标店/竞品店). 核实店铺身份与店龄, 全量统计商品数/价格带/折扣/上新节奏, 识别主题/应用/Markets/追踪标签, 检查政策页一致性, 交付一份大白话的中文 PDF 报告 (保留证据标签) 和一张可用 Excel 打开的商品清单. 用于: 对标店分析, 竞品独立站拆解, "这家店是真的吗/开了多久", "它靠什么卖这个价", Shopify store teardown, competitor store analysis. 只读公开页面, 不登录, 不下单.
license: MIT
metadata:
  version: "0.2.0"
  author: StellarByte
  homepage: https://github.com/harrison-ming/chuhai-skills
  status: experimental
  review_by: "2026-12-30"
---

# Shopify 对标店拆解

把一个公开的 Shopify 店铺拆成一份**普通卖家看得懂, 每条结论都有证据**的报告. 用户多半不懂技术: 你负责分析和翻译, 事实交给脚本算, 判断由你来做, 两者都要能追溯到证据.

## 边界 (先读)

- 只读公开页面和公开接口. **不登录, 不进入结账, 不提交订单, 不填任何个人信息.**
- 请求要礼貌: 同一店铺的请求之间留间隔, 不做并发轰炸.
- 本 skill 覆盖快速核验 (L0) 和深度拆解 (L1). 购物车, 手机端实测, 广告库, 页面速度等需要浏览器的检查不在范围内, 报告里如实写"没有覆盖".
- 分析结果是时间点快照, 不是尽职调查或法律意见.

## 第 0 步: 检查 Python

采集和出 PDF 要用电脑上的 Python 3.8+. 先查一次, 用第一个输出 `Python 3.x` (x ≥ 8) 的命令, 下文示例里的 `python3` 都换成它:

```bash
python --version
python3 --version
py -3 --version
```

- macOS / Linux 一般是 `python3`; **Windows 一般是 `python` 或 `py -3`, 不是 `python3`**.
- 没装 Python 的 Windows 会输出 "Python was not found; run without arguments to install from the Microsoft Store..." (退出码 9009). 这算**没有 Python**, 不是成功.
- 三个都不行时, 用大白话告诉用户, 例如:

> 这个技能要用电脑上的 Python (免费的小程序) 来抓数据和生成 PDF, 你的电脑还没装. 两种装法任选一种:
> 1. 最省事 (Windows): 开始菜单搜 "cmd" 打开命令行, 输入 `python` 回车, 会打开微软应用商店的 Python 页面, 点"获取"即可.
> 2. 到 python.org 下载安装包, 安装第一屏**务必勾选 "Add python.exe to PATH"**, 再点 Install Now.
>
> 装好后把 AI 助手 (或终端窗口) 关掉重新打开, 再让我继续.

- 用户不想装: 按 `references/manual-collection.md` 的模式 C (不能运行 Python) 做, 这时没有 PDF 和商品清单.

## 第 1 步: 明确用户想知道什么

没有问题就不要做"全面分析". 先问用户一次; 用户没说或说"你看着办", 默认深度拆解并在报告里写明.

| 用户想知道 | 档位 | 耗时 |
|---|---|---|
| 真的假的 / 是谁的 / 开了多久 / 规模多大 | L0 快速核验 | 约 10 分钟 |
| 它靠什么卖这个价 / 页面怎么推动购买 / 我能借鉴什么 | L1 深度拆解 | 约 30-60 分钟 |

输入可以是域名, 首页链接或任意商品页链接.

## 第 2 步: 采集数据 (按环境自动选模式)

脚本在本 skill 目录的 `scripts/` 下, 只用 Python 标准库, 不需要安装任何包. 下面的 `python3` 换成第 0 步查到的命令 (Windows 上一般是 `python` 或 `py -3`).

**模式 A (首选): 能运行 Python 且能联网**

```bash
python3 <skill 目录>/scripts/collect.py <店铺网址> --depth L1
```

脚本最后会打印**运行目录**的路径, 形如 `<文档>/出海拆解报告/<域名> 对标拆解 <YYYY-MM-DD>/`. 数据都在 `<运行目录>/原始数据/` 下: `summary.json` (统计与端点状态), `products.tsv` (全部商品), `evidence.jsonl` (证据条目), `raw/` (原始页面).

- **用户没有指定保存位置时, 不要加 `--out-root` 或 `--out`**, 也不要自作主张写到当前目录: 默认会存到用户的"文档/出海拆解报告" (Windows 自动识别 OneDrive 下的文档; 找不到就用用户主目录; 云端沙箱存在 `/mnt/user-data/outputs` 时自动写到那里). 已设置环境变量 `CHUHAI_OUTPUT_DIR` 时脚本会自动用它, 同样不要加参数. 只有用户明确说了存哪, 才加 `--out-root <目录>`.
- 同一天重复拆同一家店, 文件夹会自动加 "(2)", 不会覆盖.
- 如果输出显示几乎所有端点都是网络错误 (不是 404 或 403), 说明环境不能联网, 换模式 B.

**模式 B: 能运行 Python 但不能联网, 有网页读取工具** → 自己建 `<运行目录>/原始数据/raw/`, 按 `references/manual-collection.md` 取数存文件, 再运行:

```bash
python3 <skill 目录>/scripts/summarize.py "<运行目录>/原始数据"
```

(传 `<运行目录>` 本身也可以, 脚本会自动找里面的 `原始数据/`.)

之后与模式 A 相同.

**模式 C / D: 不能运行 Python, 或没有读取工具** → 按 `references/manual-collection.md` 对应小节处理. 这时生成不了 PDF 和商品清单, 完成状态最多写 `partial`, 第 6 步改为在对话里直接给完整报告.

## 第 3 步: 读统计结果, 先处理警告

读 `<运行目录>/原始数据/summary.json`. 字段含义见 `references/data-contract.md`. 必须先看:

1. `identity.status`: 只有 `confirmed` / `probable` 才继续按 Shopify 分析. `not_shopify` 就告诉用户并跳过 Shopify 专项部分.
2. `warnings`: 每一条都要在报告里交代 (正文用大白话, 附录写原值和实际数值), 不要自行解释原因. 例如 `collections_count_mismatch` 时写出 `collections_all_products_count` 与 `products.count` 两个数, 并说明商品量以 `products.count` 为准.
3. `endpoints` 里失败的项: 写进"还有哪些没查清", **拿不到不等于不存在**.

## 第 4 步: 分析

- **L0**: 身份信号, 店名与基础币种, 店龄三方对照 (域名注册 / 网页历史存档首抓 / 首个商品 `created_at`, 有品牌自称年份也放进来), 规模速览. 规则见 `references/shopify-checks.md` §1-2 和 `references/business-analysis.md` §9.
- **L1**: 在 L0 基础上, 读首页, 1 个主要集合页, 3 个商品页 (入门/主力/高价), About 页, 5 个政策页. 模式 A 已全部存到 `raw/`: 集合页 `page-collection-<handle>.html`, 商品页 `page-product-<handle>.html`, 选了哪 3 个商品 (角色, 价格, 选择依据 `basis`) 见 `summary.json` 的 `l1_samples` (脚本按排除虚拟商品后的价格分位和 best-selling 排序挑选). 价格分布优先引用 `products.price_excluding_virtual` (样本只从有货商品挑, 对应其中的 `in_stock`). 模式 B 按同样的文件名手动保存. 按 `references/business-analysis.md` 全文分析, 平台层按 `references/shopify-checks.md` §3-9.

证据规则见 `references/evidence.md`. 最容易犯的错:

- 把 `collections.json` 的 `products_count` 当商品量.
- 看到支付图标就说"已开通 PayPal / Shopify Payments". 只能写 `[页面显示]`.
- 源码里有某应用字符串就说"在用某应用". 脚本结果只到 `possible`.
- 按页面样式猜主题名. 读不到 `Shopify.theme` 就写 `unknown`.
- 把域名注册日期当开店日期.
- 没有成本数据却估算利润率.
- 把一个国家站点的价格和政策当成全球通用.

## 第 5 步: 写报告

按 `references/report-template.md` 的对应模板和"写作要求", 用 Markdown 写到 `<运行目录>/原始数据/report.md`. 要点:

- 读者是不懂技术的卖家. 先结论后理由, 短句, 少用英文; 正文不出现证据等级代号和技术词, 技术细节放附录.
- 第一节必须是 `## 一页看懂`; 附录标题以 `## 附录` 开头; 这两个标题会被特殊排版, 不要改名.
- 承重结论加标签, 必须一字不差: `[已核实]` `[店铺自称]` `[第三方说法]` `[页面显示]` `[推测]` (后跟"依据: ...") `[有矛盾]` `[查不到]`. 宁可标低不要标高: 分析判断用 `[推测]`, 混合说法拆开分别标, 没抓到来源的外部事实不写, 细则见 `references/evidence.md` §2.1, "一页看懂"同样适用.
- 数字带单位和币种, 比例写百分比.
- **报告末尾必须原样附上模板里的"固定结尾".**
- 用户用什么语言提问, 报告就用什么语言.

**写完必须自检, 通过后才能进第 6 步:**

```bash
python3 <skill 目录>/scripts/check_report.py "<运行目录>"
```

- 有"错误" (退出码 1): 按每条的"怎么改"改完 report.md, 重新运行, 直到没有错误.
- 有"疑似标高": 逐条复查. 是判断就拆开: 事实部分留 `[已核实]`, 判断部分改 `[推测]` 并写 "依据: ...". 例: "同款各颜色同价 [已核实]. 定价核心是一款一价 [推测] 依据: 同款各颜色价格一致". 确认是纯事实的, 删掉判断用语 (核心, 基本, 所以等).
- 其他"提示" (技术词带解释, 全角标点) 能改就改.

## 第 6 步: 生成交付文件, 告诉用户去哪看

```bash
python3 <skill 目录>/scripts/deliver.py "<运行目录>"
```

它会先跑一遍同样的自检 (有错误时打印出来, 仍照常生成, 并在最后提醒; 加 `--strict` 则有错误就不生成), 然后在运行目录顶层生成 `<店名> 对标拆解报告.pdf` 和 `商品清单.csv` (中文表头, Excel/WPS 直接打开), 并自动打开文件夹 (不想打开加 `--no-open`). 电脑上找不到 Edge/Chrome, 或浏览器在当前运行方式下没法打印 (常见于后台服务) 时, 生成的是 `.html`: 双击用浏览器打开, 需要 PDF 就在浏览器里"打印 → 另存为 PDF".

然后**用大白话告诉用户文件在哪**, 不要只甩一个路径. 例如:

> 报告已生成: 文档 → 出海拆解报告 → allbirds.com 对标拆解 2026-09-30, 双击 PDF 就能看; 商品清单.csv 可以用 Excel 打开. 原始数据在里面的"原始数据"文件夹, 一般不用管.

接着给 5-8 行摘要: 结论, 最值得借鉴的点, 主要没查清的事. 云端沙箱里说明文件在输出区, 可直接下载.

模式 C / D: 没有文件可交付, 直接在对话里给出完整报告 (同样按模板), 并说明"当前环境不能运行脚本, 所以没有生成 PDF 和商品清单; 换成能运行 Python 的环境可以拿到完整文件".

## 失败时

- 店铺打不开或身份无法确认: 仍写一份 `failed` 状态的短报告并交付, 写清卡在哪一步, 建议用户换一个网址 (例如带 `https://` 的首页) 或稍后重试.
- 证书错误: 脚本已自动兼容 (Windows 上会先让系统补一次根证书再重试). 仍失败时把脚本打印的"提示"用大白话转告用户: macOS 上 python.org 版 Python 运行一次 "Install Certificates.command"; Windows 先用浏览器打开一次该网站, 或等几分钟再试; Linux 更新系统证书. **不要关闭证书校验.**
- 被限流 (429): 加大 `--delay` 重试, 不要并发.
- `deliver.py` 失败: 报告仍在 `原始数据/report.md`, 告诉用户这个位置, 并在对话里给出摘要.
