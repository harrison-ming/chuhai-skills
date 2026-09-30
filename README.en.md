# chuhai-skills: Agent Skills for cross-border e-commerce

[中文](README.md)

Agent Skills for sellers going global. Install them into the AI assistant you already use (Claude, Codex, Cursor, Gemini CLI and other SKILL.md-compatible tools) and ask in plain language.

Every skill follows the same discipline: **public information only, facts computed by scripts, every conclusion labelled with its evidence level, and "unknown" when the evidence is not there.** Reports are written in the language you ask in.

## Skills

| Skill | What it does | Status |
|---|---|---|
| [shopify-store-teardown](skills/shopify-store-teardown/) | Tear down any public Shopify store: identity and store age checks, full catalog / price band / launch cadence stats, theme / app / Markets detection, policy consistency | Available (experimental) |

## Install

**Claude Code**

```text
/plugin marketplace add harrison-ming/chuhai-skills
/plugin install shopify-store-teardown@chuhai-skills
```

**Claude.ai / Claude Desktop**: download `shopify-store-teardown-<version>.zip` from [Releases](https://github.com/harrison-ming/chuhai-skills/releases) and upload it on the Skills page in settings (code execution must be on).

**Other SKILL.md-compatible tools**: copy `skills/shopify-store-teardown/` into the tool's skills directory, or run `npx skills add harrison-ming/chuhai-skills`.

## Requirements

Python 3.8+, standard library only. Works best with network access; without it the skill falls back to your agent's web-fetch tool or to pasted content.

## Boundaries

Reads public pages and public endpoints only. No login, no checkout, no orders, no personal data. Reports are point-in-time snapshots of public information, not due diligence, investment or legal advice, and carry no promise of sales or conversion outcomes.

## More

The free skill fully covers single-store L0 / L1 teardowns. A Pro pack (multi-store comparison, scorecard with anchors, gap list against your own store, browser checks, CSV export) is in preparation. For a done-for-you teardown, [contact StellarByte](https://stellarbyte.ca/contact/?ref=skill-teardown).

## License

[MIT](LICENSE). Maintained by [StellarByte](https://stellarbyte.ca/?ref=skill-teardown).
