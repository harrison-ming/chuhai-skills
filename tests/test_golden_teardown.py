"""Golden equality: teardown CLIs produce byte-identical (normalized) outputs.

The manifest tests/golden/teardown-manifest.json was generated from the code
before scripts/_shared/ existed. Regenerate it only for an intended output
change: ``python tests/golden_teardown.py --write`` (and say why in the commit).
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import golden_teardown  # noqa: E402


class GoldenTeardownTest(unittest.TestCase):
    maxDiff = None

    def test_outputs_match_manifest(self):
        with open(golden_teardown.MANIFEST, encoding="utf-8") as fh:
            expected = json.load(fh)
        got = golden_teardown.build_manifest()
        self.assertEqual(sorted(got), sorted(expected))
        for key in sorted(expected):
            self.assertEqual(got[key], expected[key], key)


if __name__ == "__main__":
    unittest.main()
