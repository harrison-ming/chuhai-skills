"""Data files shipped inside a skill (rates, fee tables, thresholds).

Convention: a JSON object with a top-level ``valid_as_of`` date
(``YYYY-MM-DD``) and optionally ``source`` (where the numbers came from).
``freshness`` tells how old the data is so reports can say so plainly and
the agent can warn when it is older than the limit (90 days by default).
Python 3.8+, standard library only.
"""

import datetime
import json

from . import i18n

DEFAULT_MAX_AGE_DAYS = 90

MESSAGES = {
    "zh": {
        "fresh": "数据截至 %s (%d 天前).",
        "stale": "数据截至 %s, 已经 %d 天, 超过 %d 天, 费率可能已变, 请以官网为准.",
        "undated": "数据文件没有写截至日期 (valid_as_of), 无法判断是否过期.",
    },
    "en": {
        "fresh": "Data as of %s (%d days ago).",
        "stale": (
            "Data as of %s is %d days old, over %d days; rates may have "
            "changed, check the official source."
        ),
        "undated": "The data file has no valid_as_of date, so its age is unknown.",
    },
}


def load_json(path):
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def parse_date(value):
    try:
        return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def age_days(valid_as_of, today=None):
    """Days since valid_as_of, or None when it is missing / malformed."""
    day = parse_date(valid_as_of)
    if day is None:
        return None
    today = today or datetime.date.today()
    return (today - day).days


def freshness(data, max_age_days=DEFAULT_MAX_AGE_DAYS, today=None, lang=None):
    """dict(valid_as_of, age_days, stale, note) for a loaded data object.

    ``stale`` is True when older than ``max_age_days`` and None when undated.
    """
    msg = MESSAGES[i18n.norm_lang(lang)]
    valid = data.get("valid_as_of") if isinstance(data, dict) else None
    age = age_days(valid, today)
    if age is None:
        return {
            "valid_as_of": valid,
            "age_days": None,
            "stale": None,
            "note": msg["undated"],
        }
    stale = age > max_age_days
    note = (
        msg["stale"] % (valid, age, max_age_days)
        if stale
        else msg["fresh"] % (valid, age)
    )
    return {"valid_as_of": valid, "age_days": age, "stale": stale, "note": note}
