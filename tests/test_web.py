from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vantera.db import Database
from vantera.web import Dashboard, STATIC


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "web.db")
        self.db.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_hq_state_is_real_and_zero_money_by_default(self):
        state = Dashboard(self.db).state()
        self.assertEqual(0, state["money"]["verified_revenue_cents"])
        self.assertEqual(0, state["money"]["spend_limit_cents"])
        self.assertEqual([], state["workers"])
        self.assertEqual("NONE", state["owner_action_required"])

    def test_pwa_assets_exist_and_are_installable(self):
        for name in ("index.html", "app.css", "app.js", "sw.js", "manifest.webmanifest", "icon.svg"):
            self.assertTrue((STATIC / name).is_file(), name)
        manifest = (STATIC / "manifest.webmanifest").read_text()
        self.assertIn('"display":"standalone"', manifest)
        self.assertIn('"start_url":"/"', manifest)

    def test_path_traversal_is_rejected(self):
        status = []
        result = Dashboard(self.db)({"PATH_INFO": "/../web.py"}, lambda s, h: status.append(s))
        self.assertEqual("404 Not Found", status[0])
        self.assertEqual([b"Not found"], result)


if __name__ == "__main__":
    unittest.main()
