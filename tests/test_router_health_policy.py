import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.router_health_policy import RouterHealthPolicy


class RouterHealthPolicyTests(unittest.TestCase):
    def policy(
        self,
        root: Path,
        health=None,
    ) -> RouterHealthPolicy:
        return RouterHealthPolicy(
            version="1.0",
            source_fingerprint="source",
            config_dir=root / "config",
            router_base="http://127.0.0.1:3000",
            pid_path=root / "router.pid",
            current_user=lambda: "user",
            health=lambda: health,
            connectivity_summary=lambda: "tcp=down",
        )

    def test_identity_match_requires_version_source_user_and_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = self.policy(root)
            health = {
                "version": "1.0",
                "source_fingerprint": "source",
                "user": "user",
                "config_dir": root / "config",
            }

            self.assertTrue(policy.matches_current(health))
            self.assertFalse(
                policy.matches_current({**health, "version": "2.0"})
            )

    def test_summary_projects_up_and_down_states(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = self.policy(root)

            self.assertIn("health=down", policy.summary())
            self.assertIn(
                "health=ok",
                policy.summary({"pid": 10, "version": "1.0"}),
            )

    def test_foreign_config_uses_normalized_path_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = self.policy(root)

            self.assertFalse(
                policy.has_foreign_config(
                    {"config_dir": root / "config"}
                )
            )
            self.assertTrue(
                policy.has_foreign_config(
                    {"config_dir": root / "other"}
                )
            )


if __name__ == "__main__":
    unittest.main()
