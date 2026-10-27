---
name: tracking-audit
description: >-
  广告与追踪体检: 帮出海卖家 (Shopify 独立站为主, 也支持 WooCommerce 等) 查清网站上装了哪些广告和分析追踪代码 (Meta 像素, 谷歌分析 GA4, 谷歌广告, 谷歌代码管理器 GTM, TikTok, Pinterest, Snap, Klaviyo), 哪里重复装了, 哪些是模板编号或旧版代码, 成交和服务端回传要去哪个后台确认, 隐私同意有哪些提示; 交付大白话 PDF 报告 + Excel 可打开的体检清单. 用于: 帮我体检广告追踪, 查一下像素装得对不对, 我的 Meta 像素装好了吗, 网站装了哪些追踪代码, 像素是不是装了两遍, ROAS 对不上/广告后台和店铺订单对不上, 转化回传有没有问题, GA4 有没有装, GTM 里有什么, 对标店用了哪些广告平台; audit my tracking, check my pixels, is my Meta pixel installed, which tracking tags does this site use, duplicate pixel, conversion tracking check. 只读公开页面, 不登录, 不进结账, 不下单, 不替你发测试事件.
license: MIT
metadata:
  version: "0.1.0"
  author: StellarByte
  homepage: https://github.com/harrison-ming/chuhai-skills
  status: experimental
  review_by: "2027-01-25"
---

# 广告与追踪体检

告诉卖家: 每个广告平台现在能不能看到他网站的浏览, 加购, 成交; 哪里重复算了, 哪里断了, 哪些只能他自己去后台确认; 再给一张按优先级排好的整改清单. 读者多半不懂技术. 分工: 事实交给脚本 (它读公开页面并写好报告草稿), 你负责问清情况, 读截图, 把草稿改成好懂的大白话, 每条结论都能追溯到证据.

## 边界 (先读)

- 只读公开页面和公开的谷歌代码容器. **不登录任何后台, 不进入结账, 不下单, 不填个人信息, 不替用户发送测试事件, 不碰用户的访问令牌, 不改店铺代码.**
- 请求要礼貌: 脚本一次只发一个请求, 间隔 1 秒, 不要并发重复跑.
- **代码在页面里不等于在正常发送.** 结账里的事件和服务端回传从外面看不到, 只能靠用户的后台截图 (见 `references/user-inputs.md`).
- 隐私与同意部分只是提示, 不是法律意见, 不下 "合规/不合规" 结论.
- 结果是某一天的快照.

## 第 0 步: 检查 Python

脚本要用电脑上的 Python 3.8+. 先查一次, 用第一个输出 `Python 3.x` (x ≥ 8) 的命令, 下文示例里的 `python3` 都换成它:

```bash
python --version
python3 --version
py -3 --version
```

- macOS / Linux 一般是 `python3`; **Windows 一般是 `python` 或 `py -3`, 不是 `python3`**.
- 没装 Python 的 Windows 会输出 "Python was not found; run without arguments to install from the Microsoft Store..." (退出码 9009). 这算**没有 Python**, 不是成功.
- 三个都不行时, 用大白话告诉用户怎么装 (Windows: 命令行输入 `python` 打开应用商店点 "获取"; 或到 python.org 下载, 安装第一屏勾选 "Add python.exe to PATH"), 装好后重开 AI 助手再继续. 用户不想装: 走模式 C.

## 第 1 步: 弄清是谁的店, 在哪投广告

- **自家店 (默认)**: 先跑公开检查, 不必等用户回答. 跑完后问一句 "你现在在哪些平台投广告?", 并把脚本生成的 "请你提供" 截图清单转告用户 (见第 4 步).
- **别人的店** (用户说对标店, 竞品, "这家店用了什么"): 采集时加 `--mode other`. 报告标题会变成 "公开页面观察", 结账和服务端整节写 [查不到], 不向用户要截图.
- 只给了店名没给网址: 问一次网址. 用户说 "你看着办" 时按自家店处理并在报告里写明.

语言: 用户用英文提问时, 所有脚本加 `--lang en`, 报告按 `references/report-template.en.md`; 否则用中文和 `references/report-template.zh.md`.

## 第 2 步: 采集与计算 (按环境自动选模式)

脚本在本技能目录的 `scripts/` 下, 只用 Python 标准库, 不需要装任何包.

**不管遇到什么情况, 都走完整流程: 采集 → 写报告 → 交付文件.** 不要先用别的方式 (命令行下载工具, 网页读取工具) 看一眼就在对话里回答. 网站打不开, 当前环境不能联网, 网页读取工具只拿到正文拿不到源码, 不是 Shopify 店, 都照样从 `report.draft.md` 写 `report.md`, 跑 check_report.py 和 deliver.py 交付文件 (完成状态按实际写 `partial` 或 `failed`, 见"失败时"). 对话里的回答只是已交付报告的摘要, 不能代替报告; 同样只用大白话, 不出现技术词.

**模式 A (首选): 能运行 Python 且能联网**

```bash
python3 <技能目录>/scripts/collect.py <网址> [--mode other] [--lang en]
```

它抓首页, 1 个集合页, 1 个商品页, 空购物车页, 隐私政策, 以及页面引用的公开谷歌代码容器 (最多 5 个), 然后自动运行 `audit.py`. 最后打印**运行目录**; 数据在 `<运行目录>/原始数据/` (英文 `data/`).

- **用户没有指定保存位置时, 不要加 `--out-root` 或 `--out`, 也不要写到当前目录**: 默认存到用户的 "文档/出海体检报告"; 已设置环境变量 `CHUHAI_OUTPUT_DIR` 时自动用它, **不要自己设置或改写这个变量**. 只有用户明确说了存哪, 才加 `--out-root <目录>`.
- 输出里有 `network_unavailable` (网站和其他网站都没响应): 说明当前环境不能联网, 不代表网站打不开. 有网页读取工具就换模式 B, 没有就按"失败时"交付短报告, 说明是环境不能联网.
- 输出里有 `site_unreachable` (只有这个网站没响应): 网址可能拼错或网站暂时打不开. 不要改用别的工具绕过去, 按"失败时"交付 `failed` 短报告.

**模式 B: 能运行 Python, 不能联网, 有网页读取工具** → 先用 `collect.py <网址> --no-audit` 建好运行目录 (它会记录网络失败, 目录仍在交付根目录下); 再用网页读取工具按 `references/method.md` 第 6 节把页面源码存进 `<运行目录>/原始数据/raw/`, 然后:

```bash
python3 <技能目录>/scripts/audit.py "<运行目录>" [--mode other] [--lang en]
```

网页读取工具也读取失败, 或只给出页面正文 (看不到 `<script>` 代码) 时: **不要把正文当源码存进 `raw/`** (会被误判成 "没装"), 照样运行上面的 `audit.py` (`raw/` 里只有网络失败记录), 它会写好 `failed` 草稿, 再按"失败时"交付. 正文里看到的线索 (例如隐私政策写了用谷歌分析) 可以补进报告, 标 [店铺自称], 不能写成 [页面显示].

**模式 C: 不能运行 Python, 有网页读取工具** → 按 `references/method.md` 第 7 节和 `references/checks.md` 手工判断, 在对话里给完整报告 (同样按模板), 完成状态最多 `partial`.

**模式 D: 什么工具都没有** → 请用户粘贴页面源码 (浏览器右键 "查看网页源代码" 全选复制) 或后台截图, 同样方法分析, 完成状态最多 `partial`.

不论哪种模式, 文件都只写在运行目录 (交付根目录下), 不写当前目录.

## 第 3 步: 读结果, 先处理警告

读 `原始数据/summary.json` (字段见 `references/method.md` 第 5 节):

1. `status`: `complete` / `partial` / `failed`, 报告附录必须写同一个值. `failed` (或 `warnings` 里有 `network_unavailable` / `site_unreachable`) 时直接按"失败时"写短报告并交付, 不做后面的分析.
2. `warnings`: 每一条都要在报告里用大白话交代 (例: `not_shopify` → 不是 Shopify 店, Shopify 专项不适用; `blocked:...` → 页面被网站拦了; `page_missing:...` → 哪个页面没拿到; `data_file_stale:...` → 识别规则超过 90 天没复测).
3. 拿不到不等于不存在, 没看到不等于没装.

## 第 4 步: 用户输入 (自家店)

把 `原始数据/user-inputs/请你提供.md` (英文 `what-we-need.md`) 的内容用大白话转给用户: 每项说清在哪点, 截什么, 证明什么 (点击路径见 `references/user-inputs.md`). 提醒先打码账户名和邮箱. **不要等用户**: 先交付当前报告, 告诉用户提供截图后可以再出一版.

用户给了截图或回答时: 截图放进 `原始数据/user-inputs/`, 按 `references/user-inputs.md` 第 3 节写 `answers.json` (只写截图里真实看到的, 看不清写 `null`), 再重跑 `audit.py`. 标签: 截图 → [已核实]; 编号对不上 → [有矛盾]; 口头 → [店铺自称]; 没提供 → [查不到].

## 第 5 步: 写报告并自检

`audit.py` 已把完整草稿写在 `原始数据/report.draft.md`. 复制为 `原始数据/report.md`, 然后改:

- 用你自己的大白话重写 "一页看懂" 的结论和 "最该先做的 3 件事", 结合用户说的投放平台; 其余各节按需润色.
- 保留每个标签和它后面的 "依据: ...", 不要把 [页面显示] 改成 [已核实]; 不要把编号或代码写进正文.
- 附录里的 `完成状态` 与 `summary.json` 一致; **末尾固定结尾原样保留**.

```bash
python3 <技能目录>/scripts/check_report.py "<运行目录>"
```

有 "错误" (退出码 1) 就按 "怎么改" 改完重跑, 直到没有错误. "提示" (例如疑似标高) 逐条复查: 要么改掉, 要么确认无误并在报告对应位置写清依据.

## 第 6 步: 生成交付文件, 告诉用户去哪看

```bash
python3 <技能目录>/scripts/deliver.py "<运行目录>"
```

生成报告 (PDF; 环境里没有 Edge/Chrome 时是 .html, **HTML 就可以, 不要去找或安装别的 PDF 工具**) 和 `体检清单.csv`, 并自动打开文件夹 (不想打开加 `--no-open`). 然后用大白话告诉用户文件在哪, 再给 5-8 行摘要: 看到了哪些平台, 最该先做的事, 还需要他提供什么.

模式 C / D: 直接在对话里给出完整报告, 并说明当前环境不能运行脚本, 所以没有 PDF 和表格.

## 失败时

- **网站打不开 (`site_unreachable`), 当前环境不能联网 (`network_unavailable`), 或网页读取工具拿不到源码**: `audit.py` 已把 `failed` 短报告写好在 `report.draft.md`. 复制为 `report.md`, 用大白话写清卡在哪一步 (例如 "这次运行的环境连不上网, 网站本身可能是正常的") 和下一步怎么做, 技术原因只放附录; 照常跑 check_report.py 和 deliver.py 交付文件. 完成状态: 首页没拿到写 `failed`, 被拦或只拿到部分页面写 `partial`. 不要改用其他工具绕过去, 也不要只在对话里回答.
- **不是 Shopify 店**: 不算失败, 照常走完第 3-6 步; 报告写明 Shopify 专项不适用.
- 证书错误: 脚本已自动兼容. 仍失败时把脚本打印的提示用大白话转告用户. **不要关闭证书校验.**
- 被限流 (429): 过几分钟用更大的 `--delay` 重试一次, 不要并发.
- `deliver.py` 失败: 报告仍在 `原始数据/report.md`, 告诉用户位置, 在对话里给摘要.
