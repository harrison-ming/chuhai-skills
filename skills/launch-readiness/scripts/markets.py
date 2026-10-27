"""Target markets: parsing user input, detecting markets shown on the site,
and the per-country language / currency expectations used by the checks.

Only the markets this skill reasons about are listed (US, CA, UK, AU and the
EU member states); other countries are kept as plain codes and skipped by
market-specific rules.
Python 3.8+, standard library only.
"""

import re

# EU member state -> (main storefront languages, currency)
EU = {
    "AT": (("de",), "EUR"),
    "BE": (("nl", "fr", "de"), "EUR"),
    "BG": (("bg",), "EUR"),
    "HR": (("hr",), "EUR"),
    "CY": (("el",), "EUR"),
    "CZ": (("cs",), "CZK"),
    "DK": (("da",), "DKK"),
    "EE": (("et",), "EUR"),
    "FI": (("fi", "sv"), "EUR"),
    "FR": (("fr",), "EUR"),
    "DE": (("de",), "EUR"),
    "GR": (("el",), "EUR"),
    "HU": (("hu",), "HUF"),
    "IE": (("en",), "EUR"),
    "IT": (("it",), "EUR"),
    "LV": (("lv",), "EUR"),
    "LT": (("lt",), "EUR"),
    "LU": (("fr", "de"), "EUR"),
    "MT": (("en", "mt"), "EUR"),
    "NL": (("nl",), "EUR"),
    "PL": (("pl",), "PLN"),
    "PT": (("pt",), "EUR"),
    "RO": (("ro",), "RON"),
    "SK": (("sk",), "EUR"),
    "SI": (("sl",), "EUR"),
    "ES": (("es",), "EUR"),
    "SE": (("sv",), "SEK"),
}
OTHER = {
    "US": (("en",), "USD"),
    "GB": (("en",), "GBP"),
    "CA": (("en", "fr"), "CAD"),
    "AU": (("en",), "AUD"),
    "NZ": (("en",), "NZD"),
    "CH": (("de", "fr", "it"), "CHF"),
    "NO": (("nb", "no"), "NOK"),
}
ALIASES = {
    "UK": "GB",
    "USA": "US",
    "ENGLAND": "GB",
    "BRITAIN": "GB",
    "GERMANY": "DE",
    "FRANCE": "FR",
    "CANADA": "CA",
    "AUSTRALIA": "AU",
    "美国": "US",
    "英国": "GB",
    "德国": "DE",
    "法国": "FR",
    "加拿大": "CA",
    "澳洲": "AU",
    "澳大利亚": "AU",
    "欧盟": "EU",
    "欧洲": "EU",
    "奥地利": "AT",
    "意大利": "IT",
    "西班牙": "ES",
    "荷兰": "NL",
}
# Shown in the report when the user said "EU" without countries.
EU_DEFAULT = "DE"


def norm_country(token):
    t = (token or "").strip()
    t = ALIASES.get(t.upper(), ALIASES.get(t, t.upper()))
    if t == "EU" or t in EU or t in OTHER:
        return t
    return t if re.match(r"^[A-Z]{2}$", t) else None


def parse_markets(text):
    """'US, UK, de' or 'EU and 澳洲' -> ordered country codes ('EU' kept)."""
    out = []
    for tok in re.split(r"[\s,;/+]+|和|及|与|\band\b", text or ""):
        code = norm_country(tok)
        if code and code not in out:
            out.append(code)
    return out


def groups(countries):
    """Rule tokens for a list of countries: DE -> {DE, EU}; GB -> {UK}."""
    out = set()
    for c in countries or []:
        if c == "EU":
            out.add("EU")
        elif c in EU:
            out.update((c, "EU"))
        elif c == "GB":
            out.add("UK")
        elif c:
            out.add(c)
    return out


def applies(rule_markets, countries):
    """True when a rule limited to ``rule_markets`` touches the targets."""
    if not rule_markets:
        return True
    return bool(set(rule_markets) & groups(countries))


def languages(country):
    if country == "EU":
        country = EU_DEFAULT
    return (EU.get(country) or OTHER.get(country) or ((), None))[0]


def currency(country):
    if country == "EU":
        return "EUR"
    return (EU.get(country) or OTHER.get(country) or ((), None))[1]


def label(country, lang="zh"):
    if lang == "zh":
        names = {
            "US": "美国",
            "GB": "英国",
            "CA": "加拿大",
            "AU": "澳洲",
            "EU": "欧盟",
            "DE": "德国",
            "FR": "法国",
            "AT": "奥地利",
            "IT": "意大利",
            "ES": "西班牙",
            "NL": "荷兰",
        }
        return names.get(country, country)
    return {"GB": "UK", "EU": "EU"}.get(country, country)


def localization_countries(src):
    """Country codes offered by a Shopify localization form, if any."""
    low = src or ""
    m = re.search(r'(?is)<form[^>]+(?:id="localization_form|/localization")', low)
    if not m:
        return []
    seg = low[m.start() : m.start() + 80000]
    end = seg.find("</form>")
    seg = seg[: end if end > 0 else len(seg)]
    found = re.findall(r'(?:data-value|value)="([A-Z]{2})"', seg) + re.findall(
        r'data-country(?:-code)?="([A-Z]{2})"', seg
    )
    out = []
    for c in found:
        if c not in out:
            out.append(c)
    return out


def hreflang_countries(pairs):
    out = []
    for code, _href in pairs or []:
        parts = (code or "").split("-")
        if len(parts) >= 2 and re.match(r"^[A-Za-z]{2}$", parts[-1]):
            c = parts[-1].upper()
            c = "GB" if c == "UK" else c
            if c not in out:
                out.append(c)
    return out


def default_markets(user_text, seen):
    """User markets when given; else markets seen on the site plus US."""
    chosen = parse_markets(user_text) if user_text else []
    if chosen:
        return chosen, "user"
    out = []
    for c in list(seen or []) + ["US"]:
        if (c in EU or c in OTHER) and c not in out:
            out.append(c)
    return out, "site"


def region_of(code):
    """'en-gb' -> 'GB', 'de-DE' -> 'DE', 'es' or 'x-default' -> None."""
    parts = (code or "").split("-")
    if len(parts) < 2 or parts[0].lower() == "x":
        return None
    r = parts[-1].upper()
    if not re.match(r"^[A-Z]{2}$", r):
        return None
    return "GB" if r == "UK" else r
