# 没法跑采集脚本时怎么取数据

适用于: 沙箱不能联网 (例如 Claude.ai 网页端/桌面端的个人版), 或者 agent 根本不能执行 Python.

## 模式 B: 用网页读取工具取数, 再跑离线脚本

前提: 你有网页读取/抓取工具, 并且能在沙箱里运行 Python (只是不能联网).

1. 建目录 `<运行目录>/原始数据/raw/`. 运行目录按 `data-contract.md` §1 的约定: `<文档>/出海拆解报告/<域名> 对标拆解 <YYYY-MM-DD>/` (云端沙箱放在 `/mnt/user-data/outputs/出海拆解报告/` 下; 用户指定了位置就用用户的).
2. 依次读取下面的 URL, 把返回内容**原样**存成对应文件名 (完整列表见 `data-contract.md` §2):

| 顺序 | URL | 存为 |
|---|---|---|
| 1 | `https://<domain>/meta.json` | `raw/meta.json` |
| 2 | `https://<domain>/products.json?limit=250&page=1`, 然后 page=2, 3 ... 直到返回 `{"products":[]}` | `raw/products-1.json`, `raw/products-2.json` ... |
| 2a (可选) | `https://<domain>/collections.json?limit=250&page=<N>`, 翻到空数组为止 | `raw/collections-1.json`, `raw/collections-2.json` ... |
| 3 | `https://<domain>/sitemap.xml`, 以及其中 `sitemap_products_1.xml` 等子地图 | `raw/sitemap.xml`, `raw/sitemap-products-1.xml` ... |
| 4 | `https://<domain>/` (要 HTML 源码, 不要转成 markdown 的正文) | `raw/homepage.html` |
| 4a (可选) | `https://<domain>/collections/all?sort_by=best-selling` (HTML 源码) | `raw/best-selling.html` |
| 5 | `https://rdap.org/domain/<domain>` | `raw/rdap.json` |
| 6 | `https://web.archive.org/cdx/search/cdx?url=<domain>&output=json&fl=timestamp&collapse=timestamp:6` | `raw/wayback.json` |
| 7 (L1) | `https://<domain>/policies/refund-policy` 等 5 个政策页 | `raw/policy-<name>.html` |
| 8 (L1) | `https://<domain>/pages/about` (或 `about-us`), `https://<domain>/collections/all` | `raw/page-about.html`, `raw/page-collection-all.html` |
| 9 (L1) | 3 个商品页 `https://<domain>/products/<handle>`: 入门款 (价格约在四分之一分位), 主力款 (中位附近, 优先排序靠前的), 最贵的款; 不选运费保障, 礼品卡, 保险类商品 | `raw/page-product-<handle>.html` |

3. 运行 `python3 <skill 目录>/scripts/summarize.py "<运行目录>/原始数据"`.
4. 之后流程与模式 A 相同: 写 `<运行目录>/原始数据/report.md`, 再运行 `deliver.py "<运行目录>"` 生成 PDF 和商品清单.

注意:
- **先预期好: 模式 B 通常只能稳定完成 L0.** 很多读取工具会截断长内容, 或者只返回摘要而不是原文, 这时全量商品统计做不了. 报告里写明原因, 并建议用户在能联网运行 Python 的环境 (模式 A) 重跑.
- 读取工具返回的 JSON 如果太长被截断, 改用 `limit=50` 分页重取; 实在取不全, 就不要保存残缺文件, 在 `raw/_status.json` 里记一笔, 报告里写明"商品统计缺失".
- 商品数的兜底来源: `meta.json` 里的 `published_products_count` (店铺自报的公开商品数). 用它时注明"店铺自报, 未与 products.json 交叉核对".
- 读取工具如果只给 markdown 正文拿不到源码, 主题, 应用, 追踪代码, hreflang 就写 `[查不到]`, 不要猜.
- `rdap.org` 被拦截时, 改用注册局的官方 RDAP, 例如 `.com` / `.net` 用 `https://rdap.verisign.com/com/v1/domain/<domain>`. 它返回的是标准 RDAP 格式, 可以照样存为 `raw/rdap.json`.
- Wayback CDX 访问不了时, 可以改读 `https://archive.org/wayback/available?url=<domain>&timestamp=19900101`, 它只返回最早一次快照. **不要把它存成 `raw/wayback.json`** (脚本只认 CDX 格式, 存了也不会被解析); 把首次存档日期直接写进报告, 并注明来源是这个接口.
- 读取工具如果返回的是模型转述而不是原文, 引用时可信度降一级, 并在报告里注明.
- 某个 URL 取不到 (404, 被拦截, 网络不通), 跳过, 不要停.

## 模式 C: 不能运行 Python

1. 按模式 B 的顺序读取同样的 URL, 但不存文件, 直接阅读.
2. 手工完成 L0 核心判断: 身份信号 (≥2 个), 店名与币种, 商品大致数量 (products.json 页数 x 每页条数, 最后一页按实际计), 首个商品的 `created_at`, 域名注册日期, Wayback 首次抓取.
3. 价格统计只能抽样时, 明确写"抽样, 非全量", 列出抽样方法.
4. 报告完成状态最多写 `partial`. 这种模式生成不了 PDF 和商品清单, 直接在对话里给用户完整报告 (按 `report-template.md`), 并说明原因.

## 模式 D: 用户自己粘贴

如果你既不能联网也没有读取工具: 把上面第 1-6 行的 URL 列给用户, 请用户在浏览器里逐个打开, 全选复制粘贴给你 (或另存为文件上传). 收到后按模式 B (能跑 Python) 或模式 C 继续.

JSON 页面在浏览器里打开后, Chrome/Edge 可能显示为格式化视图, 请用户勾选"原始"或直接 Ctrl+A 复制.
