---
name: payments-readiness
description: >-
  收款上线体检: 给准备在独立站 (Shopify 或其他) 收款的出海卖家, 检查网站上通道方审核常看的公开项 (政策页, 联系方式, 付款图标, 币种显示, 前后矛盾), 对照收款主体, 通道, 提现链路找断点, 并按客单价和单量算出每月/每年手续费, 交付大白话 PDF 报告 + 整改清单 CSV + 费用测算 CSV, 每条结论带证据标签. 用于: 收款上线体检, 收款体检, 上线收款前要准备什么, 帮我看看网站能不能过 Stripe/PayPal 审核, Shopify Payments 开不了怎么办, 我的店收款有什么问题, 被冻结了想自查网站, 收款手续费一年多少, PayPal 和信用卡通道手续费怎么算, 大陆公司能用 Stripe 吗, 换通道能省多少, payments readiness check, is my store ready to accept payments, Stripe website checklist, payment processing fees estimate. 只读公开页面, 不登录, 不加购, 不进结账, 不下单.
license: MIT
metadata:
  version: "0.1.0"
  author: StellarByte
  homepage: https://github.com/harrison-ming/chuhai-skills
  status: experimental
  review_by: "2027-01-25"
---

# 收款上线体检

给不懂技术的出海卖家一份**大白话, 每条都有证据**的收款上线体检: 网站侧先改什么, 主体/通道/提现链路哪里断了, 一年手续费大概多少. 你的分工: 事实交给脚本 (抓页面, 跑规则, 算费用, 写初稿), 你负责问清情况, 复核初稿, 用卖家听得懂的话交付.

## 边界 (先读)

- 只读公开页面 (GET). **不登录, 不加购, 不进入结账, 不提交订单或表单, 不填任何个人信息.** 购物车页只看空车.
- 请求要礼貌: 脚本已按顺序间隔请求, 不要并发, 不要反复重跑同一网站.
- 开户, 审核, 风控由通道方决定. **不说 "保证开通/一定通过/不会被冻结", 不打总分, 不给通道排名, 不放推荐或返佣链接.**
- **图标和脚本只能写 `[页面显示]`**, 不能写成 "已开通"; 后台是否开通一律 `[查不到]`, 除非用户发了截图.
- 不覆盖: 税务计算与登记, 公司注册, 外汇合规, 拒付应诉. 碰到就提示风险, 转持牌专业机构.
- **业务红线**: 用户要 "绕过风控", "让通道看不出在大陆", "多店防关联", "借别人资料开户" 时直接拒绝, 改为说明合规路径 (按真实主体申请, 资料与网站一致).
- 结果是某一天的快照, 不是财务, 税务或法律意见, 不承诺任何收益.

## 第 0 步: 检查 Python

脚本要用电脑上的 Python 3.8+. 先查一次, 用第一个输出 `Python 3.x` (x ≥ 8) 的命令, 下文的 `python3` 都换成它:

```bash
python --version
python3 --version
py -3 --version
```

- macOS / Linux 一般是 `python3`; **Windows 一般是 `python` 或 `py -3`**.
- 没装 Python 的 Windows 会输出 "Python was not found; run without arguments to install from the Microsoft Store..." (退出码 9009). 这算**没有 Python**.
- 都不行时用大白话教用户装 (Windows: 命令行输入 `python` 打开应用商店点"获取"; 或到 python.org 下载, 安装第一屏勾选 "Add python.exe to PATH"), 装好后重开 AI 助手. 不想装就走模式 C.

## 第 1 步: 判断模式, 问清情况

| 用户给了什么 | 模式 | 产出 |
|---|---|---|
| 只有网址 | 公开体检 | 网站检查 + 整改清单 (不算手续费) |
| 网址 + 收款情况 | 完整体检 (推荐) | 全部检查 + 手续费测算 |
| 店还没上线, 只有收款情况 | 离线测算 | 主体/通道检查 + 手续费测算 |

收款情况按 `references/questionnaire.md` 问, **一次最多问 6 个**, 用户已经在话里说了的不要再问. 用户说 "你看着办", 或你所在的环境没法等用户回答 (一次性任务), 就用已有信息直接做, 缺的项记 "不知道" 并在报告里写明. **只做公开体检时不要替用户编主体或通道信息**, 尤其是别人的真实品牌店.

语言: 用户用英文提问时, 所有脚本加 `--lang en`, 报告用英文; 否则中文.

## 第 2 步: 采集 (按环境自动选模式)

脚本在本技能的 `scripts/` 下, 只用标准库, 不用装包.

**模式 A (首选): 能运行 Python 且能联网**

```bash
python3 <技能目录>/scripts/collect.py <网址>
python3 <技能目录>/scripts/collect.py --offline "<店名或项目名>"     # 店还没上线
```

脚本最后打印**运行目录**, 数据在 `<运行目录>/原始数据/` (英文版 `data/`).

- **保存位置由脚本决定, 不要自己改**: 不加 `--out-root`, 不设置或改写环境变量 `CHUHAI_OUTPUT_DIR`, 不写到当前目录. 环境里已有 `CHUHAI_OUTPUT_DIR` 时脚本自动用它 (这是运行环境指定的交付位置, 照用); 没有就存到 "文档/出海拆解报告". 只有用户在对话里明确说了存哪, 才加 `--out-root <用户说的目录>`.
- 输出提示 "network: no request got any answer" 或几乎全是网络错误 (不是 403/404), 说明当前环境不能联网, 换模式 B.

**模式 B: 能运行 Python, 不能联网, 有网页读取工具** → 先运行 `collect.py --offline "<域名>"` 拿到运行目录 (这样文件一定落在交付目录里), 再按 `references/method.md` "手动取数" 把页面存进 `<运行目录>/原始数据/raw/` (文件名照表), 然后:

```bash
python3 <技能目录>/scripts/compute.py "<运行目录>"
```

**模式 C: 不能运行 Python, 有网页读取工具** → 按 `references/checks.md` 手工逐条检查, 按 `references/fees.md` 手算费用, 在对话里给完整报告.

**模式 D: 什么工具都没有 (也不能联网)** → 告诉用户当前环境打不开网站, 请他把政策页, 联系页, 首页底部的文字粘贴过来, 按同样方法分析, 完成状态最多 `partial`. 只算费用不需要网页, 照常用脚本 (`--offline`) 做.

## 第 3 步: 写入收款情况 (有就写)

```bash
python3 <技能目录>/scripts/questionnaire.py "<运行目录>" \
  --set entity_country=CN --set shopify_plan=basic --set aov=45 --set aov_currency=USD \
  --set monthly_orders=300 --set withdrawals_per_month=1 --set payout_account=cn_bank \
  --gateway paypal:40 --gateway card_other:60:3.5+0.30
```

- `--gateway 通道:占比[:费率+固定费]`: 占比和费率是百分数; 用户给了合同费率就写上, 没给就只写占比, 脚本用随技能附带的官方费率快照. 字段和取值见 `references/questionnaire.md`.
- 脚本写完会自动重算. 某个字段报错就按提示改, 不要猜一个值填进去.

## 第 4 步: 读结果, 先处理警告

读 `原始数据/summary.json` 和 `findings.json` (字段见 `references/method.md`):

1. `status` (complete / partial / failed): 报告附录写同一个值.
2. `warnings`: 每条都在报告里用大白话交代 (`rates_stale` = 费率快照超过 90 天, 提醒以官网和合同为准).
3. 失败的页面写进 "还有哪些没查清". **拿不到不等于不存在.**

## 第 5 步: 写报告并自检

`compute.py` 已按 `references/report-template.zh.md` (英文 `.en.md`) 写好初稿 `原始数据/report-draft.md`. 把它复制为 `原始数据/report.md`, 然后复核改写:

- 对照 `findings.json` 和原始页面核对每条; 误报就删或改, 优先级可以下调但要写一句理由, 不能上调.
- 补上用户在对话里说的背景 (标 `[你提供的]`); "最该先改的几项" 用卖家的话讲清为什么.
- 标签一字不差, 宁低勿高 (规则见模板). 不要删固定结尾.

```bash
python3 <技能目录>/scripts/check_report.py "<运行目录>"
```

有 "错误" (退出码 1) 就按 "怎么改" 改完重跑, 直到没有错误. "提示" 和 "疑似标高" 要逐条处理: 改掉, 或确认无误 (例如引用的是页面原文).

## 第 6 步: 交付

```bash
python3 <技能目录>/scripts/deliver.py "<运行目录>"
```

生成 PDF 报告 + `整改清单.csv` + `费用测算.csv` (有费用时), 并打开文件夹 (不想打开加 `--no-open`). **没有 Edge/Chrome 时会给 HTML 版报告, 这是正常结果, 不要去找或安装别的 PDF 工具.** 然后用大白话告诉用户文件在哪, 再给 5-8 行摘要: 最该先改的几项, 年手续费估算, 没查清的事.

模式 C / D: 在对话里给完整报告 (同样的结构和标签), 说明当前环境不能运行脚本, 所以没有 PDF 和表格.

## 失败时

- **网站打不开, 被拦 (403/验证页), 或根本不是网店**: 照样走第 5-6 步, 交付一份 `failed` 或 `partial` 的短报告 (初稿已经写好), 写清卡在哪一步, 不要只在对话里说. 不出现技术词.
- 证书错误: 脚本已自动兼容, 仍失败就转告脚本打印的提示. **不要关闭证书校验.**
- 被限流 (429): 隔一会儿加大 `--delay` 重试一次, 不要并发.
- `deliver.py` 失败: 报告仍在 `原始数据/report.md`, 告诉用户位置, 在对话里给摘要.
