# Changelog

## Unreleased

- 新增 `payments-readiness` (收款上线体检, experimental 0.1.0): 只读采集 → S/P/M/T/E 五组规则 → 手续费计算器 (随技能附带 2026-10-27 费率快照, 带官方出处, 超 90 天告警) → 可直接自检通过的报告初稿 → PDF + 整改清单.csv + 费用测算.csv; 支持离线测算与手动取数; 中英双语; 规则经 5 家真实店铺和 1 个非店铺网站校准.
- 新 skill `launch-readiness` (开店上线前体检, v0.1.0 experimental): 自己的独立站上线或投谷歌购物广告前, 查政策页齐全与内容缺项, 公告栏/商品页/常见问题/政策前后矛盾, 谷歌商家中心基本要求, 欧盟/德国/英国/加拿大/澳洲/美国的基本消费者要求 (法规类结论只标推测), 隐私与 Cookie 迹象, 多市场与翻译, 示例文字/测试商品, 收录基础. 交付大白话报告 + `整改清单.csv` (P0-P3). 店铺开着密码页时走粘贴/导出模式 (`paste_ingest.py`), 不收店铺密码; 非 Shopify 站点有界爬取 (同站, 40 页, 深度 2, 遵守抓取规则). 检查目录在 `references/rules.json` (71 条, 依据来源 S 编号, 90 天复核).
- 新增 `profit-model` 利润与定价测算 (免费版, experimental): 单品最多两种售卖形态的每单贡献, 广告后利润, 盈亏平衡 ROAS 与最高获客成本, 底线价/目标价, 三情景, 月固定与盈亏平衡单量, 启动现金与现金周期, 敏感度前 3 名; 十进制精确计算, 取整偏保守; 内置带采集日期的 Shopify / Stripe / PayPal / Amazon / TikTok 费率表 (90 天提醒, 180 天降级, unverified 条目不作默认); 汇率不内置 (用户给或取欧洲央行带日期参考汇率), 关税不猜 (缺失写明未含关税); 所有默认值进 "假设清单"; 一键入口 `scripts/profit.py`; 中英报告, PDF/HTML + 4 张 CSV. 自检新增: 结果数字必须带标签, `[已核实]` 必须带日期, 正文数字必须能追到输入或费率表, 缺关税必须声明.
- 新增 skill `tracking-audit` (广告与追踪体检, v0.1.0): 读公开页面和页面引用的公开谷歌代码容器, 按 2026-10-27 在 48 家真实店铺校准的识别规则找出 Meta, 谷歌分析, 谷歌广告, GTM, TikTok, Pinterest, Snap, Klaviyo 等追踪代码 (只认带代码上下文的命中, 同意工具清单里的平台域名不算安装); 解析 Shopify 像素登记表; 检查重复安装, 多编号, 模板编号, 旧版 UA, 隐私与同意提示 (只是提示); 结账成交与服务端回传给出后台截图清单, 用户截图按规则记 [已核实]/[有矛盾]/[店铺自称]; 自动写报告草稿, 交付 PDF + `体检清单.csv`; 支持别人的店 (`--mode other`) 和英文 (`--lang en`). 测试只用合成样本.
- 生产线: 公共代码抽到 `shared/` (采集, 交付, 自检引擎, 证据标签, 中英双语), 每个 skill 带一份 `scripts/_shared/` 副本; 新增 `skills/_template/`, `tools/new_skill.py`, `tools/sync_shared.py`, `tools/gen_marketplace.py`, `tools/run_tests.py`, `CONTRIBUTING.md`.
- shopify-store-teardown 改用 `_shared`, 输出不变 (黄金输出比对测试逐字节一致); 新增 `acceptance.json`.
- marketplace.json 改为由 SKILL.md 生成, 版本号只在 SKILL.md 维护. Release 工作流检查副本同步, marketplace 与全部测试, zip 不含模板, 测试与验收文件.

## v0.2.2 (未发布)

首轮跨 agent 自动验收 (Claude / Codex) 暴露的问题修复.

- 触发: description 加入用户原话式短问句 ("xx.com 是 Shopify 店吗", "这个网站用什么建站的", "is xx.com a Shopify store" 等), 只问一句也会用这个 skill.
- 失败路径: 网站打不开时 `summary.json` 新增警告 `site_unreachable`, 采集摘要直接提示写短报告; 店铺和站外查询都没响应时改报 `network_unavailable` (环境不能联网, 不说成网站不存在, 优先换模式 B); 报告模板新增"失败短报告模板"; SKILL.md 写明打不开或不是 Shopify 店也要交付 `failed` / `partial` 短报告, 不只在对话里回答, 也不要停下来提问.
- 采集更稳更快: 传输中断 (IncompleteRead, 连接重置, 对端断开) 带退避重试 (最多 3 次, 单次等待不超过 8 秒), 重试时请求不压缩的正文; 正文比声明长度短时不再当成完整页面. 域名解析失败立即结束且不再重复请求同一主机, 不存在的域名从约 77 秒降到约 2 秒.
- 交付位置: 新增 `collect.py --init-only` (不联网, 只在交付根目录建运行目录), 模式 B 不再把目录建到当前目录; SKILL.md 写明所有模式的文件只进交付根目录.
- 无浏览器时生成 `.html` 即算交付完成, SKILL.md 写明不要另找 PDF 工具.
- 自检: 全角标点整份报告合并成一条提示 (之前每行一条, 首轮一份报告 29 条全是它); SKILL.md 要求每条提示要么改掉, 要么在附录写一行保留原因.

## v0.2.1 (2026-09-30)

- 修复: Windows 上证书错误触发 PowerShell 补根证书并重试成功后, 控制台不再误打印 "证书校验失败" 提示; 只有请求最终仍失败时才提示. `_status.json` 的 note 仍记录 `windows root certificate refresh triggered, retried once` 以便追溯.
- 文档核查修正: 手动取数兜底说明, 店铺身份判定口径, 价格口径, 附录前技术词清单, 证据等级说明, 云端沙箱输出路径; 英文 README 与中文版同步.
- Release 说明只包含当前版本的 CHANGELOG 段落, 不再附带全部历史.

## v0.2.0 (2026-09-30)

面向不懂技术的卖家重做交付和报告写法.

- 交付改为 PDF 报告 + `商品清单.csv` (中文表头, Excel/WPS 直接打开). 找不到 Edge/Chrome 时生成 HTML, 可在浏览器里打印为 PDF. 新增 `deliver.py`, 完成后自动打开文件夹.
- 统一存到 "文档/出海拆解报告/<域名> 对标拆解 <日期>/" (Windows 自动识别 OneDrive 的文档, 云端沙箱写到输出区), 可用 `--out-root` 或 `CHUHAI_OUTPUT_DIR` 指定位置; 不再散落在 agent 的工作目录.
- 原始数据 (raw/, summary.json, products.tsv, evidence.jsonl, report.md, report.html, 说明.txt) 收进子文件夹 "原始数据/".
- 报告改为大白话写法: 开头 "一页看懂" 摘要卡片, 正文以问题为标题, 结论先行, 技术细节和证据表移到附录.
- 证据纪律保留, 正文标签换成 `[已核实]` `[店铺自称]` `[第三方说法]` `[页面显示]` `[推测]` `[有矛盾]` `[查不到]` 并渲染为彩色徽章; 附录仍用 E1-E6 等级. 对照表见 `references/evidence.md`.
- 可迁移策略改为 "直接学 / 改一改再学 / 先小范围试 / 别学", 列为 做法 / 为什么有用 / 适合什么情况 / 花多少功夫 / 怎么看效果 / 风险.
- 标签宁低勿高: 分析判断一律 `[推测]`, 混合说法拆开标, 不凭记忆引用外部事实, `[有矛盾]` 只用于两条冲突证据 (`references/evidence.md` §2.1).
- L1 采集补上 1 个主要集合页和 3 个样本商品页 (入门/主力/高价, 排除运费保障, 礼品卡等虚拟商品), 选择结果写进 `summary.json` 的 `l1_samples`.
- 新增 `check_report.py` 报告自检闸门 (纯标准库): 查缺 "一页看懂"/附录/固定结尾, 附录前的技术词, 旧标签, `[推测]` 缺依据 (硬错误); 查带判断用语或混入店铺自称的 `[已核实]` (疑似标高, 要求逐条复查). SKILL.md 第 5 步改为写完必须自检; `deliver.py` 生成前自动检查, 有错误照常生成并提醒, `--strict` 时不生成.
- 价格口径统一: `summary.json` 新增 `products.price_excluding_virtual` (排除价格 < 5 和运费保障/礼品卡/保险等虚拟商品, 与样本选择同一函数); 其中 `in_stock` 为有货商品的同口径统计; `l1_samples` 每项写明 `basis` / `basis_value` / `basis_pool`, 入门款价格与全店 P25 不一致 (样本只从有货商品挑) 时可追溯口径. 报告价格分布优先用排除虚拟商品后的口径.
- `summarize.py` 可直接传运行目录; 商品清单 "款式数" 改为 "规格数".
- SKILL.md 新增 "第 0 步: 检查 Python": 依次试 `python` / `python3` / `py -3`, 识别 Windows "Python was not found ... Microsoft Store" (退出码 9009) 为没装; 没装时用大白话教用户装 (命令行输入 `python` 跳应用商店, 或 python.org 安装包勾选 "Add python.exe to PATH"), 不愿装走模式 C. 命令说明覆盖 Windows 的 `python` / `py -3`. README 运行要求同步.
- 修复: Python 3.13+ 在 Windows 上 HTTPS 证书失败. ① 新装 Windows 的根证书按需下载, OpenSSL 不会触发: `collect.py` 在 Windows 上遇证书错误时, 每个主机名最多一次调用 PowerShell 对站点根 URL 发 HEAD 请求让系统补齐根证书 (超时 20 秒, 没有 PowerShell 就跳过), 重建 SSL context 后重试一次, 记入 `_status.json` 的 note. ② 3.13+ 默认的 `VERIFY_X509_STRICT` 会拒绝部分公网证书链 ("Basic Constraints of CA cert not marked critical"): 只清这一个标志, 证书链校验和主机名校验保持开启. 证书错误提示按 macOS / Windows / Linux 分别给大白话建议.
- `deliver.py`: 浏览器非零退出 (如 Edge 在后台服务/SYSTEM 会话里退出码 1002) 时改用大白话提示 "浏览器在当前运行方式下没法打印 PDF (常见于后台服务)", 并照常给网页版报告.
- 实测: Windows 11 中文版 (ARM64, 裸系统 + Chrome), Python 3.14 与 3.8, 采集, PDF, 商品清单, 自动打开文件夹均通过.
- 修复: PDF 里 `__NEXT_DATA__`, `raw/products-*.json` 等被误渲染成粗体/斜体; 证据表编号和日期不再被挤成多行.

## v0.1.1 (2026-09-30)

- 修复: SKILL.md 的 description 在严格 YAML 解析器下报错 (普通标量里含 ": "), 导致 `npx skills` 等工具找不到 skill. 改为块标量, 并加回归测试.

## v0.1.0 (2026-09-30)

首个公开版本.

- 新增 `shopify-store-teardown`: 单店 L0 (身份与店龄核验) 与 L1 (转化拆解) 两档.
- `collect.py` 联网采集, `summarize.py` 离线统计, 仅用 Python 标准库, 支持 3.8+.
- 四种取数模式: 脚本联网 / 网页读取 + 离线脚本 / 手工 / 用户粘贴.
- 证据分级 E1-E6 与结论标签, 报告固定附"本报告没有覆盖的"一节.
