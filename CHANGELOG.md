# Changelog

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
