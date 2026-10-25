import unittest
from pathlib import Path
import tempfile
import concurrent.futures
import os
import socket
import subprocess
import sys
import time
from unittest.mock import patch

import ciel_runtime_support.workspace_router_selection as selection

import json

from ciel_runtime_support.workspace_router_selection import select_workspace_router_port


class WorkspaceRouterSelectionTests(unittest.TestCase):
    def test_existing_workspace_router_wins_over_an_earlier_free_port(self):
        selected = select_workspace_router_port(
            9464,
            Path("C:/work/two"),
            {},
            health=lambda port: (
                {"ok": True, "workspace": "C:/work/two"}
                if port == 9465
                else None
            ),
            available=lambda port: port == 9464,
        )

        self.assertEqual(9465, selected)

    def test_registry_reserves_distinct_stable_ports_per_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "workspace-router-ports.json"
            first = select_workspace_router_port(
                9464,
                Path("C:/work/one"),
                {},
                health=lambda _port: None,
                available=lambda _port: True,
                registry_path=registry,
            )
            second = select_workspace_router_port(
                9464,
                Path("C:/work/two"),
                {},
                health=lambda _port: None,
                available=lambda _port: True,
                registry_path=registry,
            )
            first_again = select_workspace_router_port(
                9464,
                Path("C:/work/one"),
                {},
                health=lambda _port: None,
                available=lambda _port: True,
                registry_path=registry,
            )

        self.assertEqual(9464, first)
        self.assertEqual(9465, second)
        self.assertEqual(first, first_again)

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

    def test_explicit_port_cannot_overlap_an_offline_workspace_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "workspace-router-ports.json"
            first = select_workspace_router_port(
                19464,
                Path("C:/work/one"),
                {"CIEL_RUNTIME_ROUTER_PORT": "19464"},
                health=lambda _port: None,
                available=lambda _port: True,
                registry_path=registry,
            )

            with self.assertRaisesRegex(RuntimeError, "reserved by workspace"):
                select_workspace_router_port(
                    19464,
                    Path("C:/work/two"),
                    {"CIEL_RUNTIME_ROUTER_PORT": "19464"},
                    health=lambda _port: None,
                    available=lambda _port: True,
                    registry_path=registry,
                )

        self.assertEqual(19464, first)

    def test_a_new_workspace_finds_a_port_past_32_reserved_ones(self):
        # 2026-09-23: 9464-9495 were all reserved by other workspaces (only 6
        # listening) and F:\greenland could not start.
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "workspace-router-ports.json"
            registry.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "workspaces": {
                            f"w{index:02d}": {"workspace": f"c:\work\{index}", "port": 9464 + index}
                            for index in range(32)
                        },
                    }
                ),
                encoding="utf-8",
            )
            selected = select_workspace_router_port(
                9464,
                Path("F:/greenland"),
                {},
                health=lambda port: None,
                available=lambda port: True,
                registry_path=registry,
            )
            self.assertEqual(9496, selected)
            with self.assertRaisesRegex(RuntimeError, "9464-9513"):
                select_workspace_router_port(
                    9464,
                    Path("F:/other"),
                    {},
                    health=lambda port: None,
                    available=lambda port: False,
                )


class ExhaustedRouterReservationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.registry = Path(temporary.name) / "workspace-router-ports.json"
        self.records = {
            f"legacy-{index}": {"workspace": f"C:/work/old-{index}", "port": 9464 + index}
            for index in range(50)
        }
        self.save()

    def save(self):
        self.registry.write_text(json.dumps({"version": 1, "workspaces": self.records}), encoding="utf-8")

    def select(self, **kwargs):
        return select_workspace_router_port(
            9464, Path("C:/work/new"), {}, registry_path=self.registry,
            health=kwargs.pop("health", lambda _port: None),
            available=kwargs.pop("available", lambda _port: True), **kwargs,
        )

    def instance(self, index=0):
        record = self.records[f"legacy-{index}"]
        path = self.registry.parent / "router-instances" / f"{record['port']}-{selection.workspace_digest(record['workspace'])}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def test_exhausted_fifty_reclaims_first_verified_offline_reservation(self):
        instance = self.instance()
        (instance / "router.pid").write_text("20001")
        clients = instance / "router-clients"
        clients.mkdir()
        (clients / "20002.json").write_text(json.dumps({"pid": 20002}))
        with patch.object(selection, "_process_identity", return_value=""):
            self.assertEqual(9464, self.select())
        records = json.loads(self.registry.read_text())["workspaces"]
        self.assertNotIn("legacy-0", records)
        self.assertEqual(50, len(records))
        new = records[selection.workspace_digest("C:/work/new")]
        self.assertEqual("auto", new["reservation"])
        self.assertIn("expires_at", new["launch_lease"])
        self.assertTrue((clients / "20002.json").exists())

    def test_unreserved_free_port_precedes_reclamation(self):
        del self.records["legacy-49"]
        self.save()
        with patch.object(selection, "_inactive_reservation", side_effect=AssertionError("must not reclaim")):
            self.assertEqual(9513, self.select())
        self.assertIn("legacy-0", json.loads(self.registry.read_text())["workspaces"])

    def test_health_unavailable_but_bound_port_is_not_reclaimed(self):
        self.assertEqual(9465, self.select(available=lambda port: port != 9464))

    def test_live_foreign_router_is_not_reclaimed(self):
        self.assertEqual(9465, self.select(health=lambda port: {"workspace": "C:/work/old-0"} if port == 9464 else None))

    def test_live_same_workspace_wins_over_own_offline_mapping(self):
        self.records[selection.workspace_digest("C:/work/new")] = {"workspace": "C:/work/new", "port": 9464}
        self.save()
        self.assertEqual(9513, self.select(health=lambda port: {"workspace": "C:/work/new"} if port == 9513 else None))

    def test_router_pid_live_or_unknown_preserves_reservation(self):
        (self.instance() / "router.pid").write_text("20001")
        for status in ("birth-token", None):
            with self.subTest(status=status), patch.object(selection, "_process_identity", return_value=status):
                self.assertEqual(9465, self.select())
                self.save()

    def test_client_live_or_unknown_preserves_reservation(self):
        clients = self.instance() / "router-clients"
        clients.mkdir()
        (clients / "20001.json").write_text(json.dumps({"pid": 20001}))
        for status in ("birth-token", None):
            with self.subTest(status=status), patch.object(selection, "_process_identity", return_value=status):
                self.assertEqual(9465, self.select())
                self.save()

    def test_malformed_client_or_pid_is_not_proof_of_inactivity(self):
        instance = self.instance()
        (instance / "router.pid").write_text("not-a-pid")
        self.assertEqual(9465, self.select())
        self.save()
        (instance / "router.pid").unlink()
        clients = instance / "router-clients"
        clients.mkdir()
        (clients / "broken.json").write_text("{")
        self.assertEqual(9465, self.select())

    def test_legacy_owner_live_or_unknown_is_not_reclaimed(self):
        self.records["legacy-0"]["owner_pid"] = 20001
        self.save()
        for status in ("birth-token", None):
            with self.subTest(status=status), patch.object(selection, "_process_identity", return_value=status):
                self.assertEqual(9465, self.select())
                self.save()

    def test_launch_lease_is_bounded_and_conservative_on_pid_reuse(self):
        for status, expiry, selected in (
            ("birth", time.time() + 30, 9465),
            ("reused-birth", time.time() - 1, 9465),
            (None, time.time() - 1, 9465),
            ("", time.time() + 30, 9465),
            ("", time.time() - 1, 9464),
        ):
            with self.subTest(status=status, expiry=expiry):
                self.records["legacy-0"]["launch_lease"] = {"pid": 20001, "identity": "birth", "expires_at": expiry}
                self.save()
                with patch.object(selection, "_process_identity", return_value=status):
                    self.assertEqual(selected, self.select())

    def test_explicit_reservation_is_never_automatically_reclaimed(self):
        self.records["legacy-0"]["reservation"] = "explicit"
        self.save()
        self.assertEqual(9465, self.select())

    def test_new_explicit_claim_is_tagged(self):
        select_workspace_router_port(
            19464, Path("C:/work/explicit"), {"CIEL_RUNTIME_ROUTER_PORT": "19464"},
            registry_path=self.registry, health=lambda _port: None, available=lambda _port: True,
        )
        record = json.loads(self.registry.read_text())["workspaces"][selection.workspace_digest("C:/work/explicit")]
        self.assertEqual("explicit", record["reservation"])

    def test_all_explicit_reservations_fail_closed_without_registry_mutation(self):
        for record in self.records.values():
            record["reservation"] = "explicit"
        self.save()
        original = self.registry.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "9464-9513"):
            self.select()
        self.assertEqual(original, self.registry.read_bytes())

    def test_unknown_instance_access_preserves_reservation(self):
        with patch.object(Path, "stat", side_effect=PermissionError("denied")):
            self.assertFalse(selection._inactive_reservation(self.records["legacy-0"], self.registry))

    def test_second_same_workspace_launch_cannot_take_live_startup_lease(self):
        self.select()
        with patch.object(selection.os, "getpid", return_value=os.getpid() + 1000000):
            with self.assertRaisesRegex(RuntimeError, "launch already reserved"):
                self.select()

    def test_concurrent_registry_calls_keep_distinct_startup_leases(self):
        def allocate(index):
            return select_workspace_router_port(
                9464, Path(f"C:/work/concurrent-{index}"), {}, registry_path=self.registry,
                health=lambda _port: None, available=lambda _port: True,
            )
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ports = list(pool.map(allocate, range(4)))
        self.assertEqual([9464, 9465, 9466, 9467], sorted(ports))
        records = json.loads(self.registry.read_text())["workspaces"]
        for index, port in enumerate(ports):
            self.assertEqual(port, records[selection.workspace_digest(f"C:/work/concurrent-{index}")]["port"])
        self.assertFalse(self.registry.with_suffix(".json.lock").exists())

    def test_managed_router_child_keeps_auto_reservation_and_parent_lease(self):
        self.select()
        before = self.registry.read_bytes()
        with patch.object(selection.os, "getppid", return_value=os.getpid()):
            select_workspace_router_port(
                9464, Path("C:/work/new"), {
                    "CIEL_RUNTIME_ROUTER_PORT": "9464",
                    "CIEL_RUNTIME_MANAGED_ROUTER": "1",
                    "CIEL_RUNTIME_ROUTER_OWNER_PID": str(os.getpid()),
                }, registry_path=self.registry, health=lambda _port: None, available=lambda _port: True,
            )
        self.assertEqual(before, self.registry.read_bytes())

    def test_child_of_live_lease_holder_reuses_port_and_keeps_parent_lease(self):
        port = self.select()
        before = self.registry.read_bytes()
        parent = os.getpid()
        with patch.object(selection.os, "getpid", return_value=parent + 1000000), \
                patch.object(selection.os, "getppid", return_value=parent):
            self.assertEqual(port, self.select())
            # The parent may already be binding the port for its router.
            self.assertEqual(port, self.select(available=lambda _port: False))
        self.assertEqual(before, self.registry.read_bytes())

    def test_lease_holder_that_is_not_the_parent_still_refuses(self):
        self.select()
        before = self.registry.read_bytes()
        parent = os.getpid()
        with patch.object(selection.os, "getpid", return_value=parent + 1000000):
            with patch.object(selection.os, "getppid", return_value=parent + 2000000):
                with self.assertRaisesRegex(RuntimeError, "launch already reserved"):
                    self.select()
            # A reused parent PID is not the process that took the lease.
            with patch.object(selection.os, "getppid", return_value=parent), \
                    patch.object(selection, "_process_identity", return_value="another-birth"):
                with self.assertRaisesRegex(RuntimeError, "launch already reserved"):
                    self.select()
        self.assertEqual(before, self.registry.read_bytes())

    def test_explicit_port_helper_child_keeps_parent_lease(self):
        environ = {"CIEL_RUNTIME_ROUTER_PORT": "9550"}
        select_workspace_router_port(
            9550, Path("C:/work/new"), environ, registry_path=self.registry,
            health=lambda _port: None, available=lambda _port: True,
        )
        before = self.registry.read_bytes()
        parent = os.getpid()
        with patch.object(selection.os, "getpid", return_value=parent + 1000000), \
                patch.object(selection.os, "getppid", return_value=parent):
            self.assertEqual(9550, select_workspace_router_port(
                9550, Path("C:/work/new"), environ, registry_path=self.registry,
                health=lambda _port: None, available=lambda _port: True,
            ))
        self.assertEqual(before, self.registry.read_bytes())

    def test_real_child_process_shares_its_parents_launch_lease(self):
        port = self.select()
        before = self.registry.read_bytes()
        script = (
            "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]);"
            "import ciel_runtime_support.workspace_router_selection as s;"
            "print(s.select_workspace_router_port(9464, Path('C:/work/new'), {}, registry_path=Path(sys.argv[2]),"
            " health=lambda _p: None, available=lambda _p: True))"
        )
        root = str(Path(selection.__file__).resolve().parents[1])
        child = subprocess.run([sys.executable, "-c", script, root, str(self.registry)],
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(0, child.returncode, child.stderr)
        self.assertEqual(str(port), child.stdout.strip())
        self.assertEqual(before, self.registry.read_bytes())

        # A live process that is not the parent keeps the protection.
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        identity = selection._process_identity(holder.pid)
        self.assertTrue(identity)
        payload = json.loads(before)
        record = payload["workspaces"][selection.workspace_digest("C:/work/new")]
        record["launch_lease"].update(pid=holder.pid, identity=identity)
        self.registry.write_text(json.dumps(payload), encoding="utf-8")
        child = subprocess.run([sys.executable, "-c", script, root, str(self.registry)],
                               capture_output=True, text=True, timeout=60)
        self.assertNotEqual(0, child.returncode)
        self.assertIn("launch already reserved", child.stderr)

    def test_process_identity_distinguishes_current_pid_from_absent_pid(self):
        self.assertTrue(selection._process_identity(os.getpid()))
        self.assertEqual("", selection._process_identity(2147483647))

    def test_real_nonrouter_bound_socket_is_not_free(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            self.assertIsNone(selection.probe_router_health(port, timeout=0.01))
            self.assertFalse(selection.port_is_free(port))


if __name__ == "__main__":
    unittest.main()
