"""Synthetic stores (all invented, .example names) served by a local HTTP
server so the tests run audit_collect.py end to end without the internet.

Each site is a dict path -> body (str) or (status, headers, body). The text
"{B}" is replaced by the server's base URL at request time.
Python 3.8+, standard library only.
"""

import datetime
import json

YEAR = datetime.date.today().year
SHOPIFY_HEAD = (
    '<link rel="stylesheet" href="//cdn.shopify.com/s/files/theme.css">'
    '<script>window.Shopify = {}; Shopify.shop = "demo.myshopify.com";'
    'Shopify.currency = {"active":"%s","rate":"1.0"};</script>'
)


def page(body, lang="en", head="", title="Clean Shop"):
    return (
        '<!doctype html><html lang="%s"><head><meta charset="utf-8"><title>%s</title>%s</head>'
        "<body>%s</body></html>" % (lang, title, head, body)
    )


def policy(title, text):
    return page(
        '<main><div class="shopify-policy__container"><h1>%s</h1>'
        '<div class="shopify-policy__body"><div class="rte">%s</div></div></div></main>'
        % (title, text)
    )


def footer(links, extra=""):
    items = "".join('<li><a href="%s">%s</a></li>' % (u, t) for u, t in links)
    return (
        '<footer class="footer"><ul>%s</ul>%s<p>&copy; %d Clean Shop</p></footer>'
        % (items, extra, YEAR)
    )


def alternates(pairs):
    return "".join(
        '<link rel="alternate" hreflang="%s" href="{B}%s">' % p for p in pairs
    )


def products_json(items):
    out = []
    for handle, title, price, extra in items:
        p = {
            "handle": handle,
            "title": title,
            "product_type": "Bag",
            "tags": [],
            "variants": [
                {
                    "price": price,
                    "available": True,
                    "requires_shipping": True,
                    "compare_at_price": None,
                }
            ],
        }
        p.update(extra or {})
        out.append(p)
    return json.dumps({"products": out})


def pdp(title, desc, cart=True):
    form = (
        (
            '<form action="/cart/add" method="post"><button type="submit" name="add">'
            "Add to cart</button></form>"
        )
        if cart
        else ""
    )
    return page(
        '<header><nav><a href="/">Home</a></nav></header><main><h1>%s</h1>'
        '<div class="product__description">%s</div>%s</main>%s'
        % (title, desc, form, footer([("/pages/contact", "Contact")])),
        head=SHOPIFY_HEAD % "USD",
    )


CLEAN_REFUND = (
    "You can return items within 30 days from the date you receive them. "
    "Items must be unused and in their original packaging. To start a return, email "
    "support@cleanshop.example with your order number. Refunds are processed within 10 "
    "business days after we receive the item. Return shipping costs are paid by the "
    "customer, except for defective or damaged items. Customers in the EU and UK have a "
    "14-day right of withdrawal from delivery without giving any reason; you may use the "
    "model withdrawal form at the end of this page. Model withdrawal form: To Clean Shop "
    "GmbH, I hereby give notice that I withdraw from my contract."
)
CLEAN_SHIPPING = (
    "Orders are processed within 1-3 business days. Delivery takes 5-10 business days "
    "to the US, UK and Germany. Standard shipping is $5; free shipping on orders over $50. "
    "Every order gets a tracking number by email. For EU and UK orders all duties and "
    "taxes are included in the price (DDP), so nothing is due on delivery."
)
CLEAN_PRIVACY = (
    "Clean Shop GmbH is the controller of your personal data; contact "
    "privacy@cleanshop.example. We use your personal information to fulfil orders and, "
    "with your consent, for marketing. The legal basis is the contract and your consent. "
    "We share data with service providers such as payment processors and shipping "
    "carriers. Some providers process data outside the EEA under standard contractual "
    "clauses. We retain order data for as long as tax law requires, then delete it. "
    "You have the right to access, correct and delete your data and to object."
)
CLEAN_TERMS = (
    "These terms of service govern purchases from Clean Shop GmbH. By placing an order "
    "you agree to these terms. Prices include VAT where applicable. We may update these "
    "terms; the version at the time of your order applies. Questions: "
    "support@cleanshop.example. Defective goods are handled under statutory warranty."
)
CLEAN_LEGAL = (
    "Impressum. Clean Shop GmbH, Musterstrasse 1, 10115 Berlin, Germany. Represented by "
    "the managing director Erika Example. Email: info@cleanshop.example. Phone: "
    "+49 30 1234 5678. Commercial register: Amtsgericht Berlin HRB 123456."
)
CLEAN_CONTACT = (
    "Clean Shop GmbH, Musterstrasse 1, 10115 Berlin, Germany. Email "
    "support@cleanshop.example, phone +49 30 1234 5678, Monday to Friday 9am-5pm CET. "
    "We answer every message within one business day, in English or German."
)
DE_TEXT = (
    "Willkommen bei Clean Shop. Unsere Taschen werden in Berlin entworfen und "
    "nachhaltig hergestellt. Kostenloser Versand ab 50 Euro innerhalb Deutschlands. "
    "Alle Preise inklusive Mehrwertsteuer. Bei Fragen erreichen Sie unseren "
    "Kundendienst per E-Mail oder Telefon von Montag bis Freitag. Entdecken Sie "
    "unsere neue Kollektion aus recyceltem Material und finden Sie Ihre Lieblingstasche."
)
CLEAN_DESC = (
    "Tote bag, 40 cm x 35 cm (16 in x 14 in), 0.4 kg (0.9 lb). Tax included. Shipping "
    "calculated at checkout. Manufacturer: Clean Shop GmbH, Musterstrasse 1, 10115 Berlin, "
    "info@cleanshop.example. Returns within 30 days of delivery."
)
POLICY_LINKS = [
    ("/policies/refund-policy", "Refund policy"),
    ("/policies/shipping-policy", "Shipping policy"),
    ("/policies/privacy-policy", "Privacy policy"),
    ("/policies/terms-of-service", "Terms of service"),
    ("/policies/legal-notice", "Impressum"),
    ("/pages/contact", "Contact"),
]
CLEAN_ALTS = [("en-us", "/"), ("en-gb", "/en-gb"), ("de-de", "/de"), ("x-default", "/")]
LOCALIZATION = (
    '<form id="localization_form" action="/localization" method="post">'
    '<a data-value="US">US</a><a data-value="GB">UK</a><a data-value="DE">DE</a></form>'
)


def clean_shopify():
    head = (
        SHOPIFY_HEAD % "USD"
        + alternates(CLEAN_ALTS)
        + (
            '<link rel="canonical" href="{B}/"><link rel="icon" href="/favicon.png">'
            '<script src="https://consent.cookiebot.com/uc.js"></script>'
            "<script>fbq('init','1');</script><script src=\"https://connect.facebook.net/x.js\"></script>"
        )
    )
    home = page(
        '<div class="announcement-bar">Free shipping on orders over $50</div>'
        '<header><nav><a href="/collections/all">Shop</a></nav></header>'
        "<main><h1>Clean Shop</h1><p>Recycled tote bags designed in Berlin.</p>"
        '<a href="/products/tote-small">Small tote</a></main>'
        + footer(POLICY_LINKS, LOCALIZATION),
        head=head,
    )

    def market(prefix, lang, cur, body):
        h = (
            SHOPIFY_HEAD % cur
            + alternates(CLEAN_ALTS)
            + '<link rel="canonical" href="{B}%s">' % prefix
        )
        return page(
            "<main>%s</main>" % body + footer(POLICY_LINKS, LOCALIZATION),
            lang=lang,
            head=h,
        )

    site = {
        "/": home,
        "/robots.txt": "User-agent: *\nDisallow: /cart\nSitemap: {B}/sitemap.xml\n",
        "/sitemap.xml": "<sitemapindex><sitemap><loc>{B}/sitemap_products_1.xml</loc></sitemap></sitemapindex>",
        "/meta.json": json.dumps(
            {"name": "Clean Shop", "currency": "USD", "country": "DE"}
        ),
        "/products.json": products_json(
            [
                ("tote-small", "Small tote", "19.00", None),
                ("tote-medium", "Medium tote", "29.00", None),
                ("tote-large", "Large tote", "49.00", None),
            ]
        ),
        "/policies/refund-policy": policy("Refund policy", CLEAN_REFUND),
        "/policies/shipping-policy": policy("Shipping policy", CLEAN_SHIPPING),
        "/policies/privacy-policy": policy("Privacy policy", CLEAN_PRIVACY),
        "/policies/terms-of-service": policy("Terms of service", CLEAN_TERMS),
        "/policies/legal-notice": policy("Impressum", CLEAN_LEGAL),
        "/policies/contact-information": policy("Contact", CLEAN_CONTACT),
        "/pages/contact": page(
            "<main><h1>Contact</h1><p>%s</p></main>" % CLEAN_CONTACT
            + footer(POLICY_LINKS),
            head=SHOPIFY_HEAD % "USD",
        ),
        "/en-gb": market(
            "/en-gb",
            "en",
            "GBP",
            "<p>Recycled tote bags designed in Berlin, "
            "delivered across the UK with all duties included.</p>",
        ),
        "/de": market("/de", "de", "EUR", "<p>%s</p>" % DE_TEXT),
        "/en-gb/policies/refund-policy": policy("Refund policy", CLEAN_REFUND),
        "/de/policies/refund-policy": policy("Widerruf", CLEAN_REFUND),
    }
    for h in ("tote-small", "tote-medium", "tote-large"):
        site["/products/%s" % h] = pdp(h.replace("-", " ").title(), CLEAN_DESC)
        site["/en-gb/products/%s.js" % h] = json.dumps({"handle": h, "price": 1900})
        site["/de/products/%s.js" % h] = json.dumps({"handle": h, "price": 1900})
    return site


BAD_REFUND = (
    "Returns are accepted within 14 days from the purchase date. A 15% restocking fee "
    "applies to every return. Customers are responsible for return shipping costs. "
    "All sales are final on clearance items. To start a return contact "
    "shop.help@gmail.com and send the item to our warehouse in Shenzhen, China."
)
BAD_ALTS = [("en-us", "/"), ("en-UK", "/en-gb"), ("de-de", "/de")]


def conflicts_missing():
    head = SHOPIFY_HEAD % "USD" + alternates(BAD_ALTS)
    links = [
        ("/policies/refund-policy", "Refund policy"),
        ("/policies/privacy-policy", "Privacy policy"),
        ("/policies/terms-of-service", "Terms of service"),
        ("/pages/faq", "FAQ"),
        ("/pages/contact", "Contact"),
        ("https://ec.europa.eu/consumers/odr", "Online dispute resolution"),
    ]
    home = page(
        '<div class="announcement-bar">60-day returns, no questions asked!</div>'
        '<header><nav><a href="/">Home</a></nav></header>'
        "<main><h2>Talk about your brand</h2><p>Lorem ipsum dolor sit amet, consectetur.</p></main>"
        + footer(links),
        head=head,
    )
    site = {
        "/": home,
        "/robots.txt": "User-agent: *\nDisallow: /cart\n",
        "/sitemap.xml": "<urlset><url><loc>{B}/products/linen-shirt</loc></url></urlset>",
        "/meta.json": json.dumps({"name": "Bad Shop", "currency": "USD"}),
        "/products.json": products_json(
            [
                ("linen-shirt", "Linen shirt", "39.00", None),
                ("linen-pants", "Linen pants", "45.00", None),
                ("linen-dress", "Linen dress", "59.00", None),
                ("test-product", "Test product", "0.01", None),
            ]
        ),
        "/policies/refund-policy": policy("Refund policy", BAD_REFUND),
        "/policies/privacy-policy": policy(
            "Privacy policy", CLEAN_PRIVACY.replace("Clean Shop GmbH", "Bad Shop Ltd")
        ),
        "/policies/terms-of-service": policy(
            "Terms", CLEAN_TERMS.replace("Clean Shop GmbH", "Bad Shop Ltd")
        ),
        "/pages/faq": page(
            "<main><h1>FAQ</h1><p>Do you offer free returns? Yes! Free returns "
            "on every order, we pay return shipping.</p></main>" + footer(links),
            head=SHOPIFY_HEAD % "USD",
        ),
        "/pages/contact": page(
            "<main><p>Email shop.help@gmail.com</p></main>" + footer(links),
            head=SHOPIFY_HEAD % "USD",
        ),
        "/en-gb": page(
            "<main><p>UK store</p></main>",
            head=SHOPIFY_HEAD % "GBP"
            + alternates([("en-us", "/"), ("en-UK", "/en-gb")]),
        ),
        "/de": page(
            "<main><p>Translation missing: de.general.cart.add. Shop our new linen "
            "collection, free shipping on all orders and easy returns for you.</p></main>",
            lang="de",
            head=SHOPIFY_HEAD % "USD",
        ),
    }
    desc = "Soft linen shirt. 纯棉面料 快速发货. Size 70 cm."
    for h in ("linen-shirt", "linen-pants", "linen-dress"):
        site["/products/%s" % h] = pdp(h.replace("-", " ").title(), desc)
    return site


def password_protected():
    pw = page(
        '<main><form action="/password" method="post"><input type="password" '
        'name="password"></form><p>Opening soon</p></main>',
        head=SHOPIFY_HEAD % "USD",
        title="Opening soon",
    )
    return {"/password": pw, "*": (302, {"Location": "{B}/password"}, "")}


def non_shopify():
    links = [
        ("/returns/", "Returns"),
        ("/privacy-policy/", "Privacy Policy"),
        ("/contact/", "Contact"),
        ("/shipping-info/", "Shipping"),
    ]
    home = page(
        '<header><nav><a href="/shop/">Shop</a></nav></header>'
        "<main><p>Handmade ceramics from Lisbon.</p>"
        '<a href="/product/blue-mug/">Blue mug</a></main>'
        '<link rel="stylesheet" href="/wp-content/themes/x.css">' + footer(links),
        title="Ceramics Lisboa",
    )
    return {
        "/": home,
        "/robots.txt": "User-agent: *\nDisallow: /wp-admin/\n",
        "/returns/": page(
            "<main><h1>Returns</h1><p>You may return any item within 30 days "
            "of delivery. Items must be unused. Contact hello@ceramics.example "
            "to start a return; refunds within 14 days.</p></main>"
        ),
        "/privacy-policy/": page(
            "<main><h1>Privacy</h1><p>We use your personal data to "
            "ship orders and share it with carriers.</p></main>"
        ),
        "/contact/": page(
            "<main><p>Email hello@ceramics.example, Rua Exemplo 10, "
            "1100-001 Lisboa.</p></main>"
        ),
        "/shipping-info/": page(
            '<main><div id="app"></div></main><script src="/app.js"></script>'
        ),
        "/shop/": page("<main><p>Shop</p></main>"),
        "/product/blue-mug/": page(
            "<main><h1>Blue mug</h1><p>Blue mug 350 ml. Add to cart.</p></main>"
        ),
    }


def blocked():
    body = (
        "<html><head><title>Just a moment...</title></head><body>cf-chl</body></html>"
    )
    return {"*": (403, {}, body)}


SITES = {
    "F1": clean_shopify,
    "F2": conflicts_missing,
    "F3": password_protected,
    "F4": non_shopify,
    "F5": blocked,
}
