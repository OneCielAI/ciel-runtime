import json
import unittest
from pathlib import Path

import ciel_runtime


class VersionSyncTests(unittest.TestCase):
    def test_python_version_matches_package_json(self):
        package_path = Path(__file__).resolve().parents[1] / "package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        self.assertEqual(package["version"], ciel_runtime.VERSION)


if __name__ == "__main__":
    unittest.main()
