import getpass
import hashlib
import unittest
from pathlib import Path

from ciel_runtime_support.router_health_policy import RouterHealthPolicy
from ciel_runtime_support.router_process_lifecycle import (
    ClockPorts,
    RouterProcessConfig,
    RouterStatePorts,
    RouterTerminationPorts,
    ensure_port_available,
)
from ciel_runtime_support.workspace_router_selection import (
    select_workspace_router_port,
    workspace_identity,
)

VERSION = "0.0.0-test"
FINGERPRINT = "fingerprint"
CONFIG_DIR = Path("C:/config") if Path("C:/").drive else Path("/config")
PORT = 29500
WORKSPACE_A = str(Path("C:/work/one").resolve(strict=False))
WORKSPACE_B = str(Path("C:/work/two").resolve(strict=False))


def instance_dir(port: int, workspace: str) -> Path:
    digest = hashlib.sha256(
        workspace_identity(workspace).encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    return CONFIG_DIR / "router-instances" / f"{port}-{digest}"


def health_payload(port: int, workspace: str) -> dict:
    return {
        "ok": True,
        "version": VERSION,
        "source_fingerprint": FINGERPRINT,
        "user": getpass.getuser(),
        "workspace": workspace,
        "config_dir": str(instance_dir(port, workspace)),
        "router_port": port,
        "pid": 4242,
    }


def policy_for(port: int, workspace: str) -> RouterHealthPolicy:
    return RouterHealthPolicy(
        VERSION,
        FINGERPRINT,
        instance_dir(port, workspace),
        f"http://127.0.0.1:{port}",
        CONFIG_DIR / "router.pid",
        getpass.getuser,
        lambda: None,
        lambda: "",
    )


class RecordingTermination:
    def __init__(self) -> None:
        self.terminated: list[dict | None] = []
        self.stopped = 0

    def ports(self) -> RouterTerminationPorts:
        def terminate_health(health, quiet):
            self.terminated.append(health)
            return True

        def stop_processes(quiet):
            self.stopped += 1
            return False

        return RouterTerminationPorts(
            terminate_pid=lambda *_: False,
            terminate_pid_file=lambda *_: False,
            terminate_health=terminate_health,
            stop_processes=stop_processes,
            listener_pids=list,
        )


class WorkspaceRouterLifetimeTests(unittest.TestCase):
    """The two launch rules: replace your own router, step aside for someone else's."""

    def _config(self, port: int, workspace: str) -> RouterProcessConfig:
        return RouterProcessConfig(
            pid_path=CONFIG_DIR / "router.pid",
            router_port=port,
            router_base=f"http://127.0.0.1:{port}",
            config_dir=instance_dir(port, workspace),
        )

    def test_router_from_the_same_workspace_is_killed_and_replaced(self):
        running = health_payload(PORT, WORKSPACE_A)
        policy = policy_for(PORT, WORKSPACE_A)
        termination = RecordingTermination()
        cleared: list[str] = []

        self.assertTrue(policy.matches_current(running))
        self.assertFalse(policy.has_foreign_config(running))

        ensure_port_available(
            "prelaunch_replace",
            running,
            0.2,
            config=self._config(PORT, WORKSPACE_A),
            state=RouterStatePorts(
                health=lambda: None,
                foreign_config=policy.has_foreign_config,
                current_config=policy.config_matches_current,
                log=lambda _level, message: cleared.append(message),
            ),
            termination=termination.ports(),
            clock=ClockPorts(now=lambda: 0.0, sleep=lambda _s: None),
        )

        self.assertEqual([running], termination.terminated)
        self.assertTrue(any("router_port_clear" in message for message in cleared))

    def test_router_from_another_workspace_is_left_alone(self):
        running = health_payload(PORT, WORKSPACE_B)
        policy = policy_for(PORT, WORKSPACE_A)
        termination = RecordingTermination()

        self.assertTrue(policy.has_foreign_config(running))

        with self.assertRaises(RuntimeError) as raised:
            ensure_port_available(
                "pre_spawn",
                running,
                0.2,
                config=self._config(PORT, WORKSPACE_A),
                state=RouterStatePorts(
                    health=lambda: running,
                    foreign_config=policy.has_foreign_config,
                    current_config=policy.config_matches_current,
                    log=lambda *_: None,
                ),
                termination=termination.ports(),
                clock=ClockPorts(now=lambda: 0.0, sleep=lambda _s: None),
            )

        self.assertIn("another ciel-runtime config", str(raised.exception))
        # Someone else's router must survive the refusal.
        self.assertEqual([], termination.terminated)

    def test_launch_relocates_instead_of_fighting_for_a_foreign_port(self):
        # The refusal above is only safe because selection never targets that
        # port in the first place.
        occupied = {PORT: health_payload(PORT, WORKSPACE_B)}

        selected = select_workspace_router_port(
            PORT,
            Path(WORKSPACE_A),
            {},
            health=occupied.get,
            available=lambda port: port != PORT,
        )

        self.assertEqual(PORT + 1, selected)

    def test_launch_targets_its_own_port_so_replacement_can_happen(self):
        occupied = {
            PORT: health_payload(PORT, WORKSPACE_B),
            PORT + 1: health_payload(PORT + 1, WORKSPACE_A),
        }

        selected = select_workspace_router_port(
            PORT,
            Path(WORKSPACE_A),
            {},
            health=occupied.get,
            available=lambda _port: False,
        )

        self.assertEqual(PORT + 1, selected)


if __name__ == "__main__":
    unittest.main()
