# Changelog

## v0.1.1 (2026-09-30)

- 修复: SKILL.md 的 description 在严格 YAML 解析器下报错 (普通标量里含 ": "), 导致 `npx skills` 等工具找不到 skill. 改为块标量, 并加回归测试.

## v0.1.0 (2026-09-30)

首个公开版本.

- 新增 `shopify-store-teardown`: 单店 L0 (身份与店龄核验) 与 L1 (转化拆解) 两档.
- `collect.py` 联网采集, `summarize.py` 离线统计, 仅用 Python 标准库, 支持 3.8+.
- 四种取数模式: 脚本联网 / 网页读取 + 离线脚本 / 手工 / 用户粘贴.
- 证据分级 E1-E6 与结论标签, 报告固定附"本报告没有覆盖的"一节.
