# Shopify Store Teardown

Analyze any public Shopify store from its public data. Give Claude a store URL and a short question; the skill verifies whether the site runs on Shopify, estimates store age, counts the catalog, maps price bands and discounts, detects the theme, apps, Markets and tracking tags, and checks policy pages for contradictions. It delivers a plain-language PDF report in which every claim carries an evidence label, plus a product spreadsheet you can open in Excel.

Built for cross-border e-commerce sellers who want to understand a competitor or a benchmark store before they launch or reprice.

## Example prompts

- "Is allbirds.com a Shopify store? How big is it?"
- "Tear down this competitor store: price bands, discounts and how often they launch new products."
- "Which theme and apps does this store use, and is it selling in multiple Markets?"

## What it accesses

- Runs locally with Python 3.8+ using only the standard library. No packages to install, no API key, no account.
- Reads only public pages of the store you name (storefront HTML, public product JSON, policy pages), plus public domain records (RDAP) and the Internet Archive index to estimate store age.
- Does not log in, place orders, or send your data anywhere. Reports are written to your machine.
- Requests are paced and limited; it does not crawl unrelated sites.

## More

Report output is in Chinese by default, with English triggers supported. More reviewed e-commerce skills: [moveset.io](https://moveset.io/en?ref=claude-directory). Privacy policy: https://moveset.io/en/legal/privacy

License: MIT.
