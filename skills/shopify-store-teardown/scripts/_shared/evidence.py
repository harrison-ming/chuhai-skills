"""Evidence levels, plain-language badges and status values.

Reports show badges in the body and E1-E6 levels only in the appendix. The
mapping follows references/evidence.md of shopify-store-teardown: label low
rather than high, an analysis is always "inferred" plus a basis.
Python 3.8+, standard library only.
"""

from . import i18n

# Stable keys, in display order. CSS classes are shared by both languages.
# DEFAULT_BADGE_KEYS is the original store-teardown set; a skill opts into
# more (e.g. "user_input" for numbers the user typed in) by passing its own
# key tuple to the renderer and the check engine.
DEFAULT_BADGE_KEYS = (
    "verified",
    "self_claim",
    "third_party",
    "on_page",
    "inferred",
    "conflicting",
    "not_found",
)
ALL_BADGE_KEYS = DEFAULT_BADGE_KEYS + ("user_input",)
BADGE_KEYS = DEFAULT_BADGE_KEYS
BADGE_CLASS = {
    "user_input": "user",
    "verified": "ok",
    "self_claim": "self",
    "third_party": "third",
    "on_page": "page",
    "inferred": "guess",
    "conflicting": "conflict",
    "not_found": "unknown",
}
BADGE_LABELS = {
    "zh": {
        "verified": "已核实",
        "self_claim": "店铺自称",
        "third_party": "第三方说法",
        "on_page": "页面显示",
        "inferred": "推测",
        "conflicting": "有矛盾",
        "not_found": "查不到",
        "user_input": "你提供的",
    },
    "en": {
        "verified": "Verified",
        "self_claim": "Store claims",
        "third_party": "Third-party",
        "on_page": "Shown on page",
        "inferred": "Inferred",
        "conflicting": "Conflicting",
        "not_found": "Not found",
        "user_input": "You provided",
    },
}
# The word that must follow an "inferred" badge in the same line / cell.
BASIS_WORD = {"zh": "依据", "en": "Basis"}  # 依据

# Pre-v0.2 Chinese labels -> replacement hint (reports must not use them).
OLD_LABELS_ZH = {
    "事实": "[已核实]",  # 事实
    "品牌自述": "[店铺自称]",  # 品牌自述
    "第三方声称": "[第三方说法]",
    "观察": "[页面显示]",  # 观察
    "推断": "[推测] + 依据",  # 推断
    "存疑": "[有矛盾]",  # 存疑
    "未知": "[查不到]",  # 未知
}

EVIDENCE_LEVELS = ("E1", "E2", "E3", "E4", "E5", "E6")
LEVEL_DESC = {
    "zh": {
        "E1": "当前页面的直接证据",
        "E2": "品牌自己的一手说法",
        "E3": "平台官方资料",
        "E4": "可追溯的第三方记录",
        "E5": "未经验证的第三方说法",
        "E6": "分析推断",
    },
    "en": {
        "E1": "direct evidence on the current page",
        "E2": "the brand's own first-hand statement",
        "E3": "official platform documentation",
        "E4": "traceable third-party record",
        "E5": "unverified third-party statement",
        "E6": "analytical inference",
    },
}
# Safest (lowest) badge for a level. E2 is a claim, never "verified".
LEVEL_TO_BADGE = {
    "E1": "verified",
    "E2": "self_claim",
    "E3": "verified",
    "E4": "verified",
    "E5": "third_party",
    "E6": "inferred",
}
# Levels a badge may stand for (empty: no supporting level at all).
BADGE_LEVELS = {
    "verified": ("E1", "E2", "E3", "E4"),
    "self_claim": ("E2",),
    "third_party": ("E5",),
    "on_page": ("E1",),
    "inferred": ("E6",),
    "conflicting": (),
    "not_found": (),
    "user_input": (),
}
# Evidence strength for "weakest link" summaries: a conclusion built from
# several inputs is only as strong as its weakest one.
# [查不到] < [推测] < [你提供的] < [店铺自称] / [页面显示] < [已核实].
# Third-party sayings rank with user input; a conflict ranks lowest.
STRENGTH = {
    "conflicting": 0,
    "not_found": 0,
    "inferred": 1,
    "user_input": 2,
    "third_party": 2,
    "self_claim": 3,
    "on_page": 3,
    "verified": 4,
}

# Run / report completion status (summary.json "status", report appendix).
STATUSES = ("complete", "partial", "failed")
# Empty-value discipline: null = applicable but not obtained, not_observed =
# looked and absent (NOT the same as false), unknown = looked, undecidable.
EMPTY_VALUES = (None, "not_observed", "unknown")


def check_keys(keys):
    """Validate a badge key tuple (None -> the default set)."""
    keys = tuple(DEFAULT_BADGE_KEYS if keys is None else keys)
    unknown = [k for k in keys if k not in BADGE_CLASS]
    if unknown:
        raise ValueError("unknown badge keys: %s" % ", ".join(unknown))
    return keys


def labels(lang=None, keys=None):
    """Badge labels (without brackets) in display order."""
    table = BADGE_LABELS[i18n.norm_lang(lang)]
    return tuple(table[k] for k in check_keys(keys))


def label(key, lang=None):
    return BADGE_LABELS[i18n.norm_lang(lang)][key]


def badge(key, lang=None):
    """``[label]`` exactly as it must appear in a report."""
    return "[%s]" % label(key, lang)


def key_of(label_text):
    """Badge key for a label in any language, or None."""
    for table in BADGE_LABELS.values():
        for key, text in table.items():
            if text == label_text:
                return key
    return None


def class_map(lang=None, keys=None):
    """[(label, CSS class)] for the renderer."""
    table = BADGE_LABELS[i18n.norm_lang(lang)]
    return [(table[k], BADGE_CLASS[k]) for k in check_keys(keys)]


def weakest(keys_or_labels):
    """The weakest badge key among keys or labels (any language), or None."""
    keys = []
    for item in keys_or_labels:
        item = str(item).strip("[]")
        key = item if item in STRENGTH else key_of(item)
        if key in STRENGTH:
            keys.append(key)
    if not keys:
        return None
    return min(keys, key=lambda k: (STRENGTH[k], ALL_BADGE_KEYS.index(k)))


def badge_for_level(level):
    return LEVEL_TO_BADGE.get(str(level).upper())


def combine_status(statuses):
    """Overall status of several parts.

    All parts failed -> failed; any part failed or partial -> partial;
    all complete -> complete; nothing known -> None.
    """
    found = [s for s in statuses if s in STATUSES]
    if not found:
        return None
    if all(s == "failed" for s in found):
        return "failed"
    if any(s != "complete" for s in found):
        return "partial"
    return "complete"
