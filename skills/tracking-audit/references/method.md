# 方法: 广告与追踪体检

写给 agent 看. 检查项逐条见 `checks.md`, 用户截图与问答见 `user-inputs.md`, 报告结构见 `report-template.zh.md` / `.en.md`.

## 1. 能看到什么, 看不到什么

| 代号 | 来源 | 能证明 | 不能证明 |
|---|---|---|---|
| H | 公开页面源码: 首页, 1 个集合页, 1 个商品页, 空购物车页, 隐私政策 | 代码或像素登记在页面里 | 代码在正常发送; 结账里的事件 |
| G | 页面引用的公开谷歌代码容器 (代码管理器 `gtm.js`, 谷歌代码 `gtag/js`), 最多 5 个 | 容器里配置了哪些谷歌编号, 容器里有哪些平台的代码 | 这些代码真的触发了 |
| U1 | 用户提供的后台截图 / 口头回答 | 截图里的后台记录 (服务端回传, 成交, 去重) | 截图以外的东西 |

Shopify 的应用像素和自定义像素运行在沙箱里, 结账里的事件从前台源码看不到; 服务端回传 (Meta 转化接口, TikTok 事件接口, 谷歌增强型转化) 发生在服务器之间, 从外面确认不了. 这些项一律走 U1, 没提供就写 [查不到].

## 2. 识别规则 (`tracking-signatures.json`)

- 规则数据截至 `valid_as_of`, 超过 90 天 `audit.py` 会在 warnings 里写 `data_file_stale:...`, 报告附录要提.
- `status: calibrated` = 在至少 3 家真实店铺上命中且能认出厂商, 命中最高标 [页面显示]; 其余 (`uncalibrated`) 最高标 [推测], 并写依据 "识别规则还没在足够多的真实店铺上校准".
- **只认带代码上下文的命中**: 加载器调用 (`'script'` + 地址), `src=`, init/load 调用, 或 Shopify 像素登记表里的条目. Cookie 同意工具的清单 (`"Tag":"https://..."`) 和脚本拦截名单里的平台域名不算安装.
- Shopify 像素登记表 (页面脚本里的像素配置列表) 按条目解析: 应用像素按应用编号认厂商 (Meta, 谷歌, TikTok, Pinterest, Snap, Klaviyo 已校准); 自定义像素只能看到商家起的名字, 按名字猜平台最高 [推测]. 配置里的令牌等字段永不输出.
- 容器里: 谷歌编号 (G-/AW-/GT-/DC-) 已校准; 其他平台的代码 (Meta, TikTok, Pinterest, Snap, 微软) 未校准, 最高 [推测].
- 编号先宽松抓再校验: 模板文字 (`YOUR_PIXEL_ID`, `XXXX`, 空, `{{ }}`) 记 `placeholder`, 格式不符记 `bad_format`, 都进 T10 (原文放附录, [已核实] 依据 "页面源码原文").

## 3. 判定与标签

- 证据强弱: 查不到 < 推测 < 店铺自称 / 页面显示 < 已核实. 一条结论引用多个来源时取最弱的.
- 免费档公开页面能给的最高标签是 [页面显示]; 例外: T10 占位编号和 D04 旧版代码是源码原文, 可标 [已核实].
- 截图: 能看到平台记录本身和日期, 且编号与页面读到的一致 → [已核实]; 编号对不上 → [有矛盾]; 只有口头说法 → [店铺自称]; 没提供 → [查不到].
- 隐私一节全部是 "提示", 不是法律意见, 不下 "合规/不合规" 判断.

## 4. 完成状态

| 状态 | 条件 |
|---|---|
| `complete` | 首页, 商品页, 隐私政策都拿到, 没被拦, 平台识别明确. 服务端项写 [查不到] 属于范围边界, 不降级 |
| `partial` | 商品页或隐私政策没拿到, 被拦 (挑战页 / 403 / 429), 或平台只识别到 "可能是 Shopify" |
| `failed` | 首页打不开. 仍然交付一份短报告 (草稿已是短报告结构) |

## 5. summary.json 主要字段

| 字段 | 含义 |
|---|---|
| `status`, `warnings` | 完成状态; 提醒代码 (`homepage_unavailable`, `blocked:<页>`, `not_shopify`, `page_missing:<页>`, `container_missing:<编号>`, `data_file_stale:<文件>`) |
| `mode` | `own` 自家店 / `other` 别人的店 (只做公开页面观察) |
| `identity` | `status`: confirmed / probable / not_shopify / unknown; `other_platform`: 识别到的其他建站平台 |
| `tags` | 每个平台 + 编号一项: `install_via` (app_pixel / custom_pixel / theme_code / gtm_container / google_tag_script), `sources`, `calibrated`, `badge_key` |
| `problems`, `legacy_ua` | 占位/格式不对的编号原文; 旧版 UA 编号 |
| `web_pixels` | 像素登记表是否存在, 条目清单 (类型, 名称或应用编号, 运行环境, 推测平台) |
| `containers` | 下载到的容器和里面读到的编号 |
| `regions`, `consent`, `privacy_policy` | 推测面向的地区 (eu_uk / us / ca); 同意相关痕迹; 隐私政策提到的平台 |
| `checks` | 体检清单每一行 (也就是 `体检清单.csv`) |
| `user_inputs` | `needed`: 还要用户提供的 U1 编号; `answered`: 已提供的 |

## 6. 手动取数 (模式 B: 不能联网跑脚本, 但有网页读取工具)

在 `<运行目录>/原始数据/raw/` (英文 `data/raw/`) 下按名字保存页面源码, 再跑 `audit.py`:

| 文件名 | 内容 |
|---|---|
| `homepage.html` | 首页源码 (必需) |
| `product.html` | 首页上任一商品链接的页面 |
| `collection.html` | 任一集合页 (Shopify: `/collections/...`) |
| `cart.html` | `/cart` 空购物车页 |
| `privacy.html` | 隐私政策 (Shopify 一般是 `/policies/privacy-policy`) |
| `container-GTM-XXXX.js` | 页面里出现的 `GTM-` 容器: `https://www.googletagmanager.com/gtm.js?id=GTM-XXXX` |

网页读取工具常把页面转成正文摘要, 看不到脚本; 这时追踪代码基本识别不到, 报告里写明 "读取工具只返回了正文, 代码部分查不到", 完成状态最多 `partial`.

## 7. 模式 C / D (不能跑 Python)

照第 2-3 节手工判断: 在页面源码里找像素登记表 (`webPixelsConfigList`) 的条目, 加载器调用和 `GTM-` 编号, 按 `checks.md` 逐项给结论; 报告结构同模板, 完成状态最多 `partial`.

## 8. 已知坑

- 平台域名出现在同意工具清单或拦截名单里, 不是安装 (实测 3 家店). 只认代码上下文.
- 有的店把追踪代码放在 `type="text/plain"` 的脚本里, 等访客同意后才执行; 这仍算 "代码在页面里".
- 谷歌应用像素新装的店只给 `GT-` 编号, 看不出是分析还是广告, 只能标 [推测].
- Klaviyo 的应用像素加站内脚本是官方标准装法, 不算重复安装.
- Shopify.country / 币种按访问者所在地变化; 从中国或北美跑看到的是那里的市场, 地区判断一律 [推测].
- 应用编号与厂商的对应来自实测, 不是官方文档, 平台可能随时改结构; 规则数据要按 `valid_as_of` 定期复测.
