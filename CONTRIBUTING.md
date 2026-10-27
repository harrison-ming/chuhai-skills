# 开发新 skill: 生产线说明

本仓库的每个 skill 都走同一条生产线. 目标是**快, 但不降质量底线**: 公共代码只写一次, 每个 skill 只写自己的方法, 脚本和样本.

## 1. 仓库结构

| 路径 | 内容 |
|---|---|
| `shared/` | 公共库唯一原文 (只用标准库, Python 3.8+): `fetch` 礼貌请求与证书兜底, `output` 交付目录, `render` Markdown → HTML → PDF 与 CSV, `checks` 报告自检引擎, `evidence` 证据等级与徽章, `i18n` 中英文本, `datafile` 数据文件时效 |
| `skills/<name>/scripts/_shared/` | `shared/` 的提交副本, 保证每个 zip 自包含. **不要手改**, 改 `shared/` 后运行 `python tools/sync_shared.py` |
| `skills/_template/` | 新 skill 的模板. 不发布, 不进 marketplace, 不参与遍历真实 skill 的测试 (有自己的有效性测试) |
| `skills/<name>/tests/` | 该 skill 的合成样本测试, 不打进 zip |
| `skills/<name>/acceptance.json` | 验收用例 (固定 prompt + 合格标准), 不打进 zip |
| `tests/` | 仓库级测试: 公共库, 黄金输出比对, SKILL.md 规范, 模板有效性 |
| `tools/` | `new_skill.py`, `sync_shared.py`, `gen_marketplace.py`, `run_tests.py`; `tools/acceptance/` 是验收矩阵自动化 (另行建设) |

## 2. 完整路径

每一步都有出口条件, 没达到不进下一步.

1. **立项卡**: 写清结果, 目标用户, 不做什么, 复用来源, 免费/Pro 边界, 进 catalog 为 `planned`. 出口: 能用一句话说出交付物 (例: "一份 PDF + 一张检查清单 CSV").
2. **模板生成**:
   ```bash
   python tools/new_skill.py payments-readiness --title-zh 收款上线体检 --title-en "Payments readiness check"
   ```
   可选 `--root-zh` / `--root-en` 指定交付根目录名 (默认 `出海体检报告` / `Store checks`). 出口: `python tools/run_tests.py` 全绿.
3. **方法与脚本**: 先写 `references/method.md` (检查项, 数据来源, 判定, 标签), 再改 `scripts/`:
   - `skill_config.py`: 名称, 交付目录, 报告名, CSV 表, 额外禁用词, 可插拔规则, 数据文件.
   - `collect.py` 只联网存原始文件; `compute.py` 只离线计算. 两者分离, 模式 B (网页读取工具存文件 + 离线脚本) 才能成立.
   - 需要用户输入数字的 skill, 在 `BADGE_KEYS` 加 `user_input` (`[你提供的]` / `[You provided]`); 汇总结论用 `evidence.weakest()` 取最弱一环.
   - 随 skill 附带的费率等数据用 JSON, 顶层写 `valid_as_of`, 登记到 `DATA_FILES`, 超过 90 天自动告警.
4. **合成样本测试**: 至少覆盖 正常 / 被拦 / 非目标平台 三类, 样本全部虚构 (域名用 `.example`). 真实网站的抓取结果只放 `~/STELLAR_OUTPUT/_tmp/`, 不进仓库.
5. **check**: 报告必须过 `check_report.py`. 结论类禁用词 (如 "合规" "已开通") 用 `FORBIDDEN_TERMS` 或 `checks.claim_badge_rule()` 写成规则, 并配测试.
6. **验收矩阵**: 填 `acceptance.json` (结构见第 4 节), 用 `tools/acceptance/` 在前 4 个 agent 环境自动跑, 结果写入 catalog `testedAgents`.
7. **双语**: 中英两份报告模板 (`report-template.zh.md` / `.en.md`), 脚本都接受 `--lang zh|en`, 英文徽章与 CSV 表头由 `shared/` 提供. SKILL.md 正文用中文, 描述里同时写中英触发说法.
8. **真实样张**: 用真实网站跑一份, 匿名化后放到商品页约定位置.
9. **打包**: `python tools/gen_marketplace.py` 更新 marketplace; 版本号只改 SKILL.md 的 `metadata.version`. Pro 版用私有仓库 `build.py --skill <name>` 构建.
10. **发布 gates**: `sync_shared.py --check`, `gen_marketplace.py --check`, `run_tests.py` 在 Python 3.8 上全绿 (Release 工作流强制); 打 tag 等对外动作需 Harrison 确认.

## 3. 质量底线 (9 项, 出货前全部满足)

| 项 | 要求 |
|---|---|
| 脚本 | 至少一个标准库 Python 脚本完成采集或计算, Python 3.8+, 采集与计算分离 |
| 证据 | 正文用大白话证据标签, 宁低勿高; 空值区分 null / not_observed / unknown |
| 自检 | 报告过 `check_report` 规则包 |
| 测试 | 合成样本 unittest, 至少覆盖 正常 / 被拦 / 非目标平台 |
| 验收 | 验收矩阵前 4 个 agent 环境通过, 结果写入 catalog `testedAgents` |
| 双语 | 中英两版报告模板, `--lang` 全链路可用 |
| 分档 | 免费版独立完成核心结果, 不阉割; Pro 是增量 (多对象, 评分卡, 浏览器实测) |
| 篇幅 | SKILL.md 软 250 / 硬 500 行, 公共内容放 references |
| 网络 | 商品页 "会访问哪些网络地址" 取自发布扫描 |

## 4. 约定

**命名**: 目录名 = frontmatter `name`, 小写英文单词用 `-` 连接, 至少两个词 (`tracking-audit`). Pro 版名为 `<name>-pro`.

**SKILL.md frontmatter**: 顶层只有 `name`, `description` (`>-` 块标量), `license`, `metadata`; `metadata` 只有 `version`, `author: StellarByte`, `homepage`, `status`, `review_by`. 普通标量里不能出现 ": " 或 " #" (严格 YAML 解析器会丢掉整个 skill).

**SKILL.md 正文**: 第 0 步 Python 检测 (`python` / `python3` / `py -3`, Windows 退出码 9009 视为没有 Python); 模式 A 脚本联网 → B 网页读取工具 + 离线脚本 → C 手工 → D 用户粘贴; 边界; 固定结尾. **不写任何特定 agent 的工具名**, 只说 "网页读取工具".

**报告**: 第一节 `## 一页看懂` (`## At a glance`), 附录以 `## 附录` (`## Appendix`) 开头并写完成状态 `complete` / `partial` / `failed`, 末尾固定结尾原样保留. 徽章一字不差:

| 中文 | 英文 | 证据等级 |
|---|---|---|
| `[已核实]` | `[Verified]` | E1-E4 |
| `[店铺自称]` | `[Store claims]` | E2 |
| `[第三方说法]` | `[Third-party]` | E5 |
| `[页面显示]` | `[Shown on page]` | E1 (仅表面) |
| `[推测]` + 依据 | `[Inferred]` + Basis | E6 |
| `[有矛盾]` | `[Conflicting]` | - |
| `[查不到]` | `[Not found]` | - |
| `[你提供的]` (可选) | `[You provided]` | 用户输入 |

强弱顺序 (取最弱一环): 查不到 < 推测 < 你提供的 < 店铺自称 / 页面显示 < 已核实.

**交付**: `<根>/<对象> <标签> <日期>/` 顶层放 PDF (无浏览器时 .html) 和 CSV (UTF-8 BOM, 中文表头), 其余进 `原始数据/` (英文 `data/`); `deliver.py` 另写 `delivery.json` 供验收脚本读取. 根目录顺序: `--out-root` > `CHUHAI_OUTPUT_DIR` > 云端沙箱 > 用户文档目录.

**acceptance.json** (结构固定, `tools/acceptance/` 依赖它):

```json
{"skill": "<name>", "cases": [{"id": "normal", "prompt": "...", "lang": "zh", "needs_network": true,
  "expect": {"deliverables": ["*.pdf|*.html", "*.csv"], "status_in": ["complete", "partial"],
             "must_not_contain": ["products.json"], "must_contain_any": ["[已核实]"], "fixed_ending": true}}]}
```

**代码与文档**: 只用标准库, 3.8 语法; 代码和注释用英文, 文档用中文; 所有文件用英文半角标点 (测试会扫描). commit message 用中文; pre-commit 跑 gitleaks + ruff, 不许 `--no-verify`.

## 5. 篇幅预算

`.doc-budget.json`: Markdown 软 250 行 / 硬 500 行 (CHANGELOG 除外). references 会被加载进模型上下文, 篇幅就是 token 成本; 超软预算先问能不能拆到另一个 reference 或删掉重复内容.

## 6. 改公共库

1. 只改 `shared/`, 加 `tests/test_shared.py` 用例.
2. `python tools/sync_shared.py` 同步所有副本.
3. `python tools/run_tests.py`: 其中 `tests/test_golden_teardown.py` 比对 teardown 全部输出, 不一致说明改动影响了已发布的 skill. 确属有意改变时运行 `python tests/golden_teardown.py --write` 并在 commit 里说明原因.
