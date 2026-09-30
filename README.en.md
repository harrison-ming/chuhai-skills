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

**Claude.ai / Claude Desktop**: download `shopify-store-teardown-<version>.zip` from [Releases](https://github.com/harrison-ming/chuhai-skills/releases) and upload it on the Skills page in settings (code execution must be on).

**Other SKILL.md-compatible tools**: copy `skills/shopify-store-teardown/` into the tool's skills directory, or run `npx skills add harrison-ming/chuhai-skills`.

## Requirements

Python 3.8+, standard library only (nothing to `pip install`). macOS usually has it; **most Windows PCs do not**, and there `python` / `python3` only print "Python was not found; run without arguments to install from the Microsoft Store". The skill checks first and walks you through installing it, either:

1. Type `python` in a Command Prompt, which opens the Microsoft Store page for Python, and click Get; or
2. Download the installer from [python.org](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"** on the first screen.

Restart your AI assistant or terminal afterwards. On Windows the command is `python` or `py -3`, not `python3`. Without Python the skill still works in chat, but produces no PDF or product list.

PDF output uses the Edge or Chrome already on your computer. Works best with network access; without it the skill falls back to your agent's web-fetch tool or to pasted content.

Known limitations: when the assistant runs as a background service, the browser may be unable to print, and you get an HTML report instead (print it to PDF from the browser). On a freshly installed Windows, the first HTTPS request to some sites can fail certificate checks until Windows downloads the root certificate; the collector triggers that download once and retries, with verification always on.

Verified: macOS (Claude Code) and Windows 11 Chinese edition (ARM64, clean install + Chrome, Python 3.14 and 3.8), 2026-09-30.

## Boundaries

Reads public pages and public endpoints only. No login, no checkout, no orders, no personal data. Reports are point-in-time snapshots of public information, not due diligence, investment or legal advice, and carry no promise of sales or conversion outcomes.

## More

The free skill fully covers single-store quick checks and deep teardowns. A Pro pack (multi-store comparison, scorecard with anchors, gap list against your own store, browser checks) is in preparation. For a done-for-you teardown, [contact StellarByte](https://stellarbyte.ca/contact/?ref=skill-teardown).

## License

[MIT](LICENSE). Maintained by [StellarByte](https://stellarbyte.ca/?ref=skill-teardown).
