# chuhai-skills: Agent Skills for cross-border e-commerce

[中文](README.md)

Agent Skills for sellers going global. Install them into the AI assistant you already use (Claude, Codex, Cursor, Gemini CLI and other SKILL.md-compatible tools) and ask in plain language.

Every skill follows the same discipline: **public information only, facts computed by scripts, every conclusion labelled with where its evidence comes from, and "not found" when the evidence is not there.** Reports are written in the language you ask in.

## Skills

| Skill | What it does | Status |
|---|---|---|
| [shopify-store-teardown](skills/shopify-store-teardown/) | Tear down any public Shopify store: identity and store age checks, full catalog / price band / launch cadence stats, theme / app / Markets detection, policy consistency | Available (experimental) |

## What you get

- **A PDF report** written in plain language for sellers: a one-page summary up front ("一页看懂": conclusions plus the 3 things most worth copying), question-led sections, and a colour badge on every key claim (verified / store's own claim / third-party / inference ...). Technical details and the evidence table live in the appendix.
- **A product list** (`商品清单.csv`) of every public product with price, compare-at price, stock and launch date, ready for Excel.

Files are saved under your **Documents → 出海拆解报告** folder (OneDrive Documents on Windows when present), one folder per run, opened automatically when done:

```text
Documents/
└── 出海拆解报告/
    └── allbirds.com 对标拆解 2026-09-30/
        ├── Allbirds 对标拆解报告.pdf
        ├── 商品清单.csv
        └── 原始数据/        (raw pages and statistics)
```

Use `--out-root <dir>` or the `CHUHAI_OUTPUT_DIR` environment variable to save elsewhere. Without Edge or Chrome the report is an `.html` file you can print to PDF from the browser.

## Install

**Claude Code**

```text
/plugin marketplace add harrison-ming/chuhai-skills
/plugin install shopify-store-teardown@chuhai-skills
```

**Claude.ai / Claude Desktop**: download `shopify-store-teardown-<version>.zip` from [Releases](https://github.com/harrison-ming/chuhai-skills/releases) and upload it on the Skills page in settings (code execution must be on). The personal-plan sandbox cannot reach arbitrary websites, so the skill switches to the web-fetch tool automatically.

**Other SKILL.md-compatible tools**: copy `skills/shopify-store-teardown/` into the tool's skills directory, or run `npx skills add harrison-ming/chuhai-skills`.

**Coze and other cloud platforms**: upload the zip from Releases.

## Requirements

Python 3.8+, standard library only (nothing to `pip install`). macOS usually has it; **most Windows PCs do not**, and there `python` / `python3` only print "Python was not found; run without arguments to install from the Microsoft Store". The skill checks first and walks you through installing it, either:

1. Type `python` in a Command Prompt, which opens the Microsoft Store page for Python, and click Get; or
2. Download the installer from [python.org](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"** on the first screen.

Restart your AI assistant or terminal afterwards. On Windows the command is `python` or `py -3`, not `python3`. Without Python the skill still works in chat, but produces no PDF or product list.

PDF output uses the Edge or Chrome already on your computer. Works best with network access; without it the skill falls back to your agent's web-fetch tool or to pasted content.

### Known limitations

- From mainland China, some off-site sources (such as the web archive at web.archive.org) may be unreachable. The report marks them as not found; other conclusions are unaffected.
- When the assistant runs as a background service (for example remotely or as a scheduled task), the browser may be unable to print a PDF. You get an HTML report instead; open it in a browser and print to PDF if needed.
- On a freshly installed Windows, the first HTTPS request to some sites can fail certificate checks until Windows downloads the root certificate. The collector triggers that download once and retries, with verification always on; if it still fails, open the site once in a browser or try again a few minutes later.

## Verified environments

<!-- VERIFIED:BEGIN -->
| Environment | Data collection | Result | Date |
|---|---|---|---|
| Claude Code (macOS), v0.2.0 | Scripts with network access | Plain-language PDF + product list, report self-check passed | 2026-09-30 |
| Claude Code (macOS) | Scripts with network access | Full quick-check / deep-teardown reports (v0.1.x) | 2026-09-30 |
| Claude Code, collector script disabled | Web-fetch tool + offline script | Quick-check report (full catalog stats limited by fetch-tool truncation, v0.1.x) | 2026-09-30 |
| Windows 11 Chinese edition (ARM64, clean install + Chrome), Python 3.14 and 3.8 | Scripts with network access | Collection, PDF (Microsoft YaHei), product list and auto-open folder all passed; Python must be installed first | 2026-09-30 |
<!-- VERIFIED:END -->

Install methods tested on 2026-09-30: `/plugin marketplace add`, `npx skills add` and the Releases zip all work.

Environments not listed should work (the format is an open standard) but have not been tested. Please report results in Issues.

## Boundaries

Reads public pages and public endpoints only. No login, no checkout, no orders, no personal data. Reports are point-in-time snapshots of public information, not due diligence, investment or legal advice, and carry no promise of sales or conversion outcomes.

## More

The free skill fully covers single-store quick checks and deep teardowns, with nothing held back. If you need more:

| | What | How |
|---|---|---|
| **Pro** | Comparison across 3-8 stores, anchored scorecard, gap list against your own store, browser checks (cart / mobile / popups / review widgets, run locally), ongoing updates | In preparation |
| **Done-for-you teardown** | StellarByte tears down 1 benchmark store end to end, compares it with your store and gives improvement suggestions, including a 30-minute call | [Contact StellarByte](https://stellarbyte.ca/contact/?ref=skill-teardown) |

## Feedback

Found a wrong conclusion, or the skill does not run in some AI tool? Open an [Issue](https://github.com/harrison-ming/chuhai-skills/issues) with the tool name, the store URL and the error.

## License

[MIT](LICENSE). Maintained by [StellarByte](https://stellarbyte.ca/?ref=skill-teardown).
