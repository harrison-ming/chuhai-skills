# chuhai-skills: 出海电商 Agent Skills

[English](README.en.md)

给想出海的卖家用的 AI 技能包. 装进你常用的 AI 助手 (Claude, Codex, Cursor, WorkBuddy, Qoder, TRAE 等) 后, 直接用中文说需求就能用.

所有技能遵循同一套纪律: **只读公开信息, 事实交给脚本算, 每条结论标明证据等级, 查不到就写"未知".**

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

用法示例 (直接对 AI 说):

```text
帮我拆解一下 allbirds.com, 我想知道它的定价为什么能成立
这家店 example.com 是真的吗, 开了多久
```

报告示例结构见 [report-template.md](skills/shopify-store-teardown/references/report-template.md).

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
| Claude Code (macOS) | 脚本联网 | L0 / L1 完整报告 | 2026-09-30 |
| Claude Code, 禁用采集脚本 | 网页读取 + 离线脚本 | L0 报告 (商品全量统计受读取工具截断限制) | 2026-09-30 |
<!-- VERIFIED:END -->

未列出的环境理论上兼容 (格式是开放标准), 但未经实测. 欢迎在 Issues 反馈.

## 运行要求

- Python 3.8+, 只用标准库, 无需安装任何包.
- 能联网时效果最好; 不能联网时按 [manual-collection.md](skills/shopify-store-teardown/references/manual-collection.md) 降级.
- 在中国大陆使用时, 部分站外数据源 (如 web.archive.org) 可能访问不了, 报告会标为"未知", 不影响其他结论.

## 原则与边界

- 只读公开页面和公开接口. 不登录, 不进入结账, 不下单, 不填个人信息.
- 请求带间隔, 不做高频抓取. 请遵守目标网站的服务条款.
- 报告是时间点快照, 只基于公开信息, 不构成尽职调查, 投资或法律意见.
- 不保证分析结论能带来任何销量或转化提升.

## 进阶

免费版完整覆盖单店的 L0 / L1 拆解, 不做功能阉割. 如果你需要更多:

| | 内容 | 获取 |
|---|---|---|
| **Pro 版** | 3-8 家店横向对比, 带锚点的评分卡, 对照你自己店铺的差距清单, 浏览器档 (购物车/移动端/弹窗/评价挂件, 需本地运行), CSV 导出, 持续更新 | 准备中 |
| **人工深度拆解** | 由 StellarByte 完成 1 家对标店的完整拆解, 对照你的店铺给出改造建议, 含 30 分钟通话 | [联系 StellarByte](https://stellarbyte.ca/zh/contact/?ref=skill-teardown) |

## 反馈与贡献

发现结论有误, 或在某个 AI 工具里跑不通, 请开 [Issue](https://github.com/harrison-ming/chuhai-skills/issues), 附上工具名称, 店铺网址和报错.

## 许可

[MIT](LICENSE). 由 [StellarByte](https://stellarbyte.ca/?ref=skill-teardown) 维护.
