# 方法: 开店上线前体检

<方法原文. 每个检查项一节: 查什么, 去哪查, 怎么判定, 标什么标签. 写给 agent 看, 不是写给用户看.>

## 1. 检查项

| 编号 | 检查项 | 数据来源 | 判定 | 标签 |
|---|---|---|---|---|
| C1 | 首页能打开 | `raw/homepage.html` | 抓到即通过 | `[已核实]` |
| C2 | 页面标题 | 首页 `<title>` | 有内容即通过 | `[已核实]` |

## 2. summary.json 字段

| 字段 | 含义 |
|---|---|
| `status` | `complete` 全部取到 / `partial` 部分取到或不是目标平台 / `failed` 首页都没取到 |
| `is_target` | 是否目标平台; 首页没取到时为 `"unknown"` |
| `checks` | 每个检查项一行, 也是交付的表格内容 |
| `endpoints` | 每个请求的结果: `ok` / `blocked` / `not_found` / `error` |
| `warnings` | `homepage_unavailable`, `not_target_platform`, `data_file_stale:<文件>` |
| `data_files` | 随技能附带的数据文件的截至日期和是否过期 |

## 3. 手动取数 (模式 B)

| 文件名 | 内容 |
|---|---|
| `raw/homepage.html` | 首页源码 (网页读取工具返回的内容被截断或转成了 markdown, 照样保存并在报告里注明) |

## 4. 已知坑

- <踩过的坑写在这里, 每条一句结论 + 一句怎么避免.>
