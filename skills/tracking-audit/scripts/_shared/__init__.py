"""chuhai-skills shared library.

The source of truth lives in ``<repo>/shared/``. Every skill ships a committed
copy at ``skills/<name>/scripts/_shared/`` so each zip is self-contained;
``python tools/sync_shared.py`` refreshes the copies and
``python tools/sync_shared.py --check`` (run in CI) fails when they drift.
Never edit a ``_shared/`` copy by hand.

Modules:
    i18n      language codes and the zh/en text tables
    evidence  evidence levels E1-E6, plain-language badges, status values
    fetch     polite HTTP fetching with TLS / certificate fallbacks
    output    delivery folders, file names, browser lookup, open-folder
    render    report markdown -> HTML -> PDF (HTML fallback), Excel CSV
    checks    configurable report self-check engine (pluggable rules)
    datafile  JSON data files with valid_as_of dates, age warnings

Scripts import it as a package next to them::

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _shared import fetch, output, render, checks

Python 3.8+, standard library only.
"""

SHARED_VERSION = "1.0.0"
