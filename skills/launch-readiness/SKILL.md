---
name: launch-readiness
description: >-
  开店上线前体检: <一句话说清给谁, 解决什么, 交付什么 (PDF 报告 + Excel 表格)>. 用于: <3-6 个中文触发说法>, <2-3 English trigger phrases>. 只读公开信息, 不登录, 不下单.
license: MIT
metadata:
  version: "0.1.0"
  author: StellarByte
  homepage: https://github.com/harrison-ming/chuhai-skills
  status: experimental
  review_by: "2027-01-25"
---

# 开店上线前体检

<两三句: 这个技能交付什么结果, 读者是谁 (不懂技术的出海卖家), 你的分工 (事实交给脚本算, 判断由你来做, 都要能追溯到证据).>

## 边界 (先读)

- 只读公开页面和公开接口. **不登录, 不进入结账, 不提交订单, 不填任何个人信息.**
- 请求要礼貌: 同一网站的请求之间留间隔, 不做并发.
- <本技能覆盖什么, 不覆盖什么; 不覆盖的在报告里如实写"没有覆盖".>
- 结果是时间点快照, 不是尽职调查, 法律或财务意见.

## 第 0 步: 检查 Python

脚本要用电脑上的 Python 3.8+. 先查一次, 用第一个输出 `Python 3.x` (x ≥ 8) 的命令, 下文示例里的 `python3` 都换成它:

```bash
python --version
python3 --version
py -3 --version
```

- macOS / Linux 一般是 `python3`; **Windows 一般是 `python` 或 `py -3`, 不是 `python3`**.
- 没装 Python 的 Windows 会输出 "Python was not found; run without arguments to install from the Microsoft Store..." (退出码 9009). 这算**没有 Python**, 不是成功.
- 三个都不行时, 用大白话告诉用户怎么装 (Windows: 命令行输入 `python` 打开应用商店点"获取"; 或到 python.org 下载, 安装第一屏勾选 "Add python.exe to PATH"), 装好后重开 AI 助手再继续.
- 用户不想装: 走模式 C, 这时没有 PDF 和表格.

## 第 1 步: 明确用户想知道什么

<列出用户常见问题和对应的检查范围. 没说清就问一次; 用户说"你看着办"时按默认范围做并在报告里写明.>

语言: 用户用英文提问时, 下面所有脚本加 `--lang en`, 报告按 `references/report-template.en.md` 写; 否则用中文和 `references/report-template.zh.md`.

## 第 2 步: 采集数据 (按环境自动选模式)

脚本在本技能目录的 `scripts/` 下, 只用 Python 标准库, 不需要安装任何包.

**模式 A (首选): 能运行 Python 且能联网**

```bash
python3 <技能目录>/scripts/collect.py <网址>
```

脚本最后打印**运行目录**, 数据在 `<运行目录>/原始数据/` (英文版是 `data/`) 下: `summary.json`, `evidence.jsonl`, `raw/`.

- **用户没有指定保存位置时, 不要加 `--out-root` 或 `--out`**, 也不要写到当前目录: 默认存到用户的"文档/出海体检报告". 已设置环境变量 `CHUHAI_OUTPUT_DIR` 时脚本自动用它. 只有用户明确说了存哪, 才加 `--out-root <目录>`.
- 输出显示几乎全是网络错误 (不是 404/403), 说明环境不能联网, 换模式 B.

**模式 B: 能运行 Python, 不能联网, 有网页读取工具** → 自己建 `<运行目录>/原始数据/raw/`, 按 `references/method.md` 的"手动取数"把页面存成同名文件, 再运行:

```bash
python3 <技能目录>/scripts/compute.py "<运行目录>"
```

**模式 C: 不能运行 Python, 有网页读取工具** → 按 `references/method.md` 手工检查, 直接在对话里给完整报告.

**模式 D: 什么工具都没有** → 请用户把页面内容或数据粘贴过来, 按同样的方法分析, 完成状态最多 `partial`.

## 第 3 步: 读结果, 先处理警告

读 `summary.json` (字段见 `references/method.md`). 先看:

1. `status`: `complete` / `partial` / `failed`, 报告附录必须写同一个值.
2. `warnings`: 每一条都要在报告里用大白话交代, 不要自行解释原因.
3. `endpoints` 里失败的项写进"还有哪些没查清". **拿不到不等于不存在.**

## 第 4 步: 分析

按 `references/method.md` 的检查项逐条判断. 证据规则:

- 正文承重结论加标签, 一字不差: `[已核实]` `[店铺自称]` `[第三方说法]` `[页面显示]` `[推测]` (后跟"依据: ...") `[有矛盾]` `[查不到]`; 英文版 `[Verified]` `[Store claims]` `[Third-party]` `[Shown on page]` `[Inferred]` (后跟 "Basis: ...") `[Conflicting]` `[Not found]`.
- 宁低勿高: 分析判断一律 `[推测]`; 混合说法拆开分别标; 没抓到来源的外部事实不写.
- 空值分三种: 没取到 (null), 看了没有 (not_observed), 看了判断不了 (unknown). 没看到不等于没有.

## 第 5 步: 写报告并自检

按模板写到 `<运行目录>/原始数据/report.md`. 第一节必须是 `## 一页看懂` (英文 `## At a glance`), 附录以 `## 附录` (英文 `## Appendix`) 开头, 附录里写 `完成状态: <status>` (英文 `Status: <status>`), **末尾原样附上模板的固定结尾**.

```bash
python3 <技能目录>/scripts/check_report.py "<运行目录>"
```

有"错误" (退出码 1) 就按"怎么改"改完重跑, 直到没有错误; "疑似标高"逐条复查.

## 第 6 步: 生成交付文件, 告诉用户去哪看

```bash
python3 <技能目录>/scripts/deliver.py "<运行目录>"
```

生成 PDF (没有 Edge/Chrome 时是 .html) 和表格 .csv, 并自动打开文件夹 (不想打开加 `--no-open`). 然后**用大白话告诉用户文件在哪**, 再给 5-8 行摘要: 结论, 最该先做的事, 没查清的事.

模式 C / D: 直接在对话里给出完整报告 (同样按模板), 并说明当前环境不能运行脚本, 所以没有 PDF 和表格.

## 失败时

- 网站打不开: 仍写一份 `failed` 状态的短报告并交付, 写清卡在哪一步.
- 证书错误: 脚本已自动兼容. 仍失败时把脚本打印的提示用大白话转告用户. **不要关闭证书校验.**
- 被限流 (429): 加大 `--delay` 重试, 不要并发.
- `deliver.py` 失败: 报告仍在 `原始数据/report.md`, 告诉用户位置, 在对话里给摘要.
