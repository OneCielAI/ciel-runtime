import unittest
from pathlib import Path

from ciel_runtime_support.workspace_router_selection import select_workspace_router_port


class WorkspaceRouterSelectionTests(unittest.TestCase):
    def test_same_workspace_reuses_its_port_for_replacement(self):
        health = {
            9464: {"ok": True, "workspace": str(Path("C:/work/one"))},
            9465: {"ok": True, "workspace": str(Path("C:/work/two"))},
        }

        selected = select_workspace_router_port(
            9464,
            Path("C:/work/two"),
            {},
            health=health.get,
            available=lambda _port: False,
        )

        self.assertEqual(9465, selected)

    def test_different_workspace_gets_next_free_port(self):
        selected = select_workspace_router_port(
            9464,
            Path("C:/work/two"),
            {},
            health=lambda port: {"ok": True, "workspace": "C:/work/one"} if port == 9464 else None,
            available=lambda port: port == 9465,
        )

        self.assertEqual(9465, selected)

    def test_legacy_router_without_workspace_is_not_assumed_to_match(self):
        selected = select_workspace_router_port(
            9464,
            Path("C:/work/two"),
            {},
            health=lambda port: {"ok": True} if port == 9464 else None,
            available=lambda port: port == 9465,
        )

        self.assertEqual(9465, selected)

    def test_explicit_port_is_never_reselected(self):
        selected = select_workspace_router_port(
            19464,
            Path("C:/work/two"),
            {"CIEL_RUNTIME_ROUTER_PORT": "19464"},
            health=lambda _port: self.fail("explicit ports must not be probed"),
            available=lambda _port: self.fail("explicit ports must not be scanned"),
        )

        self.assertEqual(19464, selected)


if __name__ == "__main__":
    unittest.main()
