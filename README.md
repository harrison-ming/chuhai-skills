# chuhai-skills: 出海电商 Agent Skills

[English](README.en.md)

给想出海的卖家用的 AI 技能包. 装进你常用的 AI 助手 (Claude, Codex, Cursor, WorkBuddy, Qoder, TRAE 等) 后, 直接用中文说需求就能用.

所有技能遵循同一套纪律: **只读公开信息, 事实交给脚本算, 每条结论标明证据来源 (已核实 / 店铺自称 / 推测 ...), 查不到就写"查不到".**

## 技能列表

| 技能 | 做什么 | 状态 |
|---|---|---|
| [shopify-store-teardown](skills/shopify-store-teardown/) | 拆解任意公开 Shopify 独立站: 身份与店龄核验, 全量商品/价格带/上新节奏统计, 主题/应用/Markets 识别, 政策一致性检查 | 可用 (experimental) |

## 对标店拆解: 能回答什么

- 这家店是真的 Shopify 店吗? 开了多久? (域名注册, 历史快照, 首个商品上架时间三方对照)
- 卖多少款, 价格带怎么分布, 多少在打折, 最近一年每月上新多少?
- 用的什么主题, 装了哪些应用和追踪标签, 做了哪些市场?
- 它靠什么卖这个价, 首页和商品页怎么推动购买, 政策页有没有前后矛盾?
- 哪些做法值得借鉴, 哪些别照搬?

用法示例 (直接对 AI 助手说, 不用懂技术):

```text
帮我拆解一下 allbirds.com, 我想知道它的定价为什么能成立
这家店 example.com 是真的吗, 开了多久
```

## 你会拿到什么

- **一份 PDF 报告**: 第一页"一页看懂"直接给结论和最值得借鉴的 3 件事; 正文用大白话按问题展开 (卖什么卖给谁, 价格怎么定, 页面怎么说服人下单, 政策有没有矛盾, 你能学什么); 每条关键结论带彩色标签, 告诉你它是"已核实", "店铺自称"还是"推测". 技术细节和证据清单放在最后的附录.
- **一张商品清单** (`商品清单.csv`): 这家店公开在售的全部商品, 价格, 原价, 是否有货, 上架时间, 用 Excel 或 WPS 直接打开.

文件统一存在你电脑的 **文档 → 出海拆解报告** 文件夹 (Windows 用 OneDrive 同步文档的, 就在 OneDrive 的文档里), 每拆一次一个文件夹, 做完会自动打开:

```text
文档/
└── 出海拆解报告/
    └── allbirds.com 对标拆解 2026-09-30/
        ├── Allbirds 对标拆解报告.pdf    ← 双击就能看
        ├── 商品清单.csv                  ← 用 Excel / WPS 打开
        └── 原始数据/                     ← 采集的原始页面和统计, 一般不用管
```

想存到别的地方, 直接告诉 AI 助手"报告存到 D 盘的某某文件夹"即可. 在网页版等云端环境里, 文件会出现在对话的下载区. 电脑上没有 Edge 或 Chrome 时报告是网页文件 (.html), 双击用浏览器打开, 需要 PDF 可在浏览器里"打印 → 另存为 PDF".

报告的写法见 [report-template.md](skills/shopify-store-teardown/references/report-template.md).

## 安装

**Claude Code**

```text
/plugin marketplace add harrison-ming/chuhai-skills
/plugin install shopify-store-teardown@chuhai-skills
```

**Claude 网页版 / 桌面版**: 在 [Releases](https://github.com/harrison-ming/chuhai-skills/releases) 下载 `shopify-store-teardown-<版本>.zip`, 到设置里的 Skills 页面上传. 需要开启代码执行. 个人版沙箱不能访问任意网站, 技能会自动改用网页读取工具取数.

**其他支持 SKILL.md 的工具** (Codex, Cursor, Gemini CLI, WorkBuddy, Qoder, TRAE, Kimi Code 等): 把 `skills/shopify-store-teardown/` 整个文件夹复制到该工具的 skills 目录; 或使用

```bash
npx skills add harrison-ming/chuhai-skills
```

**扣子 (Coze) 等云端平台**: 上传 Releases 里的 zip.

## 已验证环境

<!-- VERIFIED:BEGIN -->
| 环境 | 取数模式 | 结果 | 验证日期 |
|---|---|---|---|
| Claude Code (macOS) | 脚本联网 | 快速核验 / 深度拆解完整报告 (v0.1.x) | 2026-09-30 |
| Claude Code, 禁用采集脚本 | 网页读取 + 离线脚本 | 快速核验报告 (商品全量统计受读取工具截断限制, v0.1.x) | 2026-09-30 |
| Windows 11 中文版 (ARM64, 裸系统 + Chrome), Python 3.14 与 3.8 | 脚本联网 | 采集, PDF (微软雅黑), 商品清单, 自动打开文件夹均通过; 需先装 Python | 2026-09-30 |
<!-- VERIFIED:END -->

安装方式实测 (2026-09-30): `/plugin marketplace add` 安装, `npx skills add` 安装, Releases zip 下载均通过.

未列出的环境理论上兼容 (格式是开放标准), 但未经实测. 欢迎在 Issues 反馈.

## 运行要求

- **电脑上要有 Python 3.8 或更新版本** (免费, 装一次就行; 只用自带功能, 不用再装别的包). Mac 一般已经有. **Windows 电脑通常没有**, AI 助手会先帮你检查, 没有的话两种装法任选:
  1. 最省事: 开始菜单搜 "cmd" 打开命令行, 输入 `python` 回车, 会打开微软应用商店的 Python 页面, 点"获取".
  2. 到 [python.org](https://www.python.org/downloads/) 下载安装包, 安装第一屏**务必勾选 "Add python.exe to PATH"**.

  装好后把 AI 助手 (或终端窗口) 关掉重新打开再用. 实在不想装也能用, 但只能在对话里看报告, 没有 PDF 和商品清单.
- 能联网时效果最好; 不能联网时按 [manual-collection.md](skills/shopify-store-teardown/references/manual-collection.md) 降级.
- 生成 PDF 用电脑自带的 Edge 或 Chrome (Windows 一般自带 Edge); 都没有时改为网页文件.

### 已知限制

- 在中国大陆使用时, 部分站外数据源 (如网页历史存档 web.archive.org) 可能访问不了, 报告会标为"查不到", 不影响其他结论.
- AI 助手以后台服务方式运行时 (例如远程或计划任务), 浏览器可能没法打印 PDF, 这时会改为生成网页版报告, 双击用浏览器打开, 需要 PDF 可在浏览器里"打印 → 另存为 PDF".
- 刚装好的 Windows 第一次访问某些网站可能报证书错误, 脚本会自动让系统补齐证书再试一次; 仍不行就先用浏览器打开一次该网站, 或等几分钟再试.

## 原则与边界

- 只读公开页面和公开接口. 不登录, 不进入结账, 不下单, 不填个人信息.
- 请求带间隔, 不做高频抓取. 请遵守目标网站的服务条款.
- 报告是时间点快照, 只基于公开信息, 不构成尽职调查, 投资或法律意见.
- 不保证分析结论能带来任何销量或转化提升.

## 进阶

免费版完整覆盖单店的快速核验和深度拆解, 不做功能阉割. 如果你需要更多:

| | 内容 | 获取 |
|---|---|---|
| **Pro 版** | 3-8 家店横向对比, 带锚点的评分卡, 对照你自己店铺的差距清单, 浏览器档 (购物车/移动端/弹窗/评价挂件, 需本地运行), 持续更新 | 准备中 |
| **人工深度拆解** | 由 StellarByte 完成 1 家对标店的完整拆解, 对照你的店铺给出改造建议, 含 30 分钟通话 | [联系 StellarByte](https://stellarbyte.ca/zh/contact/?ref=skill-teardown) |

## 反馈与贡献

发现结论有误, 或在某个 AI 工具里跑不通, 请开 [Issue](https://github.com/harrison-ming/chuhai-skills/issues), 附上工具名称, 店铺网址和报错.

## 许可

[MIT](LICENSE). 由 [StellarByte](https://stellarbyte.ca/?ref=skill-teardown) 维护.
