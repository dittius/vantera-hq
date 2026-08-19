from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "vantera" / "static"


class HQFrontendTests(unittest.TestCase):
    def test_primary_navigation_is_owner_focused(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-view="hq"', html)
        self.assertIn('data-view="ventures"', html)
        self.assertIn('data-view="ceo"', html)
        self.assertNotIn('data-view="activity"', html)

    def test_floor_allocation_uses_role_not_reports_to(self):
        source = (STATIC / "app.js").read_text(encoding="utf-8")
        marker = "const DEPARTMENT_IDS="
        mapping_text = source.split(marker, 1)[1].split(";", 1)[0]
        self.assertEqual(
            {"ceo": "CEO", "cvo": "VENTURE", "cso": "STRATEGY", "cfo": "FINANCE",
             "sales": "SALES", "cmo": "MARKETING", "coo": "OPERATIONS", "cto": "TECHNOLOGY"},
            json.loads(mapping_text),
        )
        self.assertNotIn("reports_to", source[source.index("function departmentForAgent"):source.index("function workState")])

    def test_ceo_floor_does_not_inherit_direct_reports(self):
        source = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("w.department===key", source)
        self.assertNotIn("reports_to).toUpperCase", source)

    def test_mobile_and_pwa_safety_are_present(self):
        css = (STATIC / "app.css").read_text(encoding="utf-8")
        manifest = (STATIC / "manifest.webmanifest").read_text(encoding="utf-8")
        self.assertIn("@media(max-width:680px)", css)
        self.assertIn("env(safe-area-inset-bottom)", css)
        self.assertIn("overflow-x:hidden", css)
        self.assertIn('"display":"standalone"', manifest)
        self.assertIn('"start_url":"./"', manifest)


if __name__ == "__main__":
    unittest.main()
