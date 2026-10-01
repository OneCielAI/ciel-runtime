import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support import github_runtime_source as source
from ciel_runtime_support.package_lifecycle import (
    GitHubSourcePorts,
    SelfUpdateLifecycle,
    SelfUpdatePorts,
)

NIGHTLY = "e7062c776a795ab66707f57301b41c4dfece845c"
MAIN = "e74376efae0000c6db5876e4e64556ca6db893a6"
# Shape of GitHub's git-upload-pack advertisement (pkt-lines, capabilities after NUL).
REFS = (
    "001e# service=git-upload-pack\n0000"
    f"0155{MAIN} HEAD\x00multi_ack thin-pack side-band symref=HEAD:refs/heads/main\n"
    f"003d{MAIN} refs/heads/main\n"
    f"0040{NIGHTLY} refs/heads/nightly\n"
    f"0045{'1' * 40} refs/heads/nightly-old\n"
    "0000"
).encode()


class GitHubRuntimeSourceTests(unittest.TestCase):
    def test_branch_head_is_read_from_the_ref_advertisement(self):
        self.assertEqual(NIGHTLY, source.branch_head_from_refs(REFS, "nightly"))
        self.assertEqual(MAIN, source.branch_head_from_refs(REFS, "main"))
        self.assertEqual("", source.branch_head_from_refs(REFS, "missing"))

    def test_remote_head_failure_is_empty(self):
        def failing(*_args, **_kwargs):
            raise OSError("offline")

        self.assertEqual("", source.remote_branch_head("nightly", urlopen=failing))

    def test_remote_head_reads_the_public_refs_url(self):
        seen = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        def urlopen(request, timeout):
            seen.append((request.full_url, timeout))
            return Response(REFS)

        self.assertEqual(NIGHTLY, source.remote_branch_head("nightly", urlopen=urlopen))
        self.assertEqual(source.REFS_URL, seen[0][0])

    def test_channel_and_commit_come_from_the_marker_or_the_nightly_version(self):
        nightly = "0.2.51-nightly.20261001-164554.e7062c7"
        self.assertEqual("nightly", source.channel_branch(nightly, {}))
        self.assertEqual("main", source.channel_branch("0.2.51", {}))
        self.assertEqual("nightly", source.channel_branch("0.2.51", {"branch": "nightly"}))
        self.assertEqual("nightly", source.channel_branch("0.2.51", {}, "nightly"))
        self.assertEqual("main", source.channel_branch("0.2.51", {"branch": "main"}, "nightly"))
        self.assertEqual("e7062c7", source.installed_commit(nightly, {}))
        self.assertEqual("", source.installed_commit("0.2.51", {}))
        self.assertEqual(NIGHTLY, source.installed_commit("0.2.51", {"sha": NIGHTLY}))
        self.assertTrue(source.is_current("e7062c7", NIGHTLY))
        self.assertFalse(source.is_current("", NIGHTLY))
        self.assertFalse(source.is_current("ae32250", NIGHTLY))

    def test_marker_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual({}, source.read_source_marker(root))
            source.write_source_marker(root, {"branch": "nightly", "sha": NIGHTLY})
            self.assertEqual({"branch": "nightly", "sha": NIGHTLY}, source.read_source_marker(root))
        self.assertEqual({}, source.read_source_marker(None))


class GitHubSelfUpdateTests(unittest.TestCase):
    def lifecycle(self, version, *, remote, marker=None, npm_latest="", installed=None, written=None, outputs=None):
        return SelfUpdateLifecycle(
            version,
            SelfUpdatePorts(
                running_from_package=lambda: True,
                find_executable=lambda name: "npm" if name == "npm" else None,
                latest_version=lambda _npm, _spec: npm_latest,
                version_newer=lambda latest, current: latest != current,
                package_root=lambda: Path("/pkg"),
                prefix_from_root=lambda _root: Path("/prefix"),
                install_command=lambda npm, spec, prefix: (installed if installed is not None else []).append(spec)
                or [npm, "install", "-g", "--prefix", str(prefix), spec],
                forced_environment=lambda: {},
                restart=lambda npm, **kwargs: None,
                output=lambda message, **_kwargs: (outputs if outputs is not None else []).append(message),
            ),
            github=GitHubSourcePorts(
                remote_head=lambda branch: remote.get(branch, ""),
                read_marker=lambda _root: dict(marker or {}),
                write_marker=lambda root, data: (written if written is not None else []).append((root, data)),
            ),
        )

    def run_update(self, lifecycle, returncode=0):
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "CIEL_RUNTIME_SKIP_SELF_UPDATE",
                "CIEL_RUNTIME_SELF_UPDATE_CHECK",
                "CIEL_RUNTIME_UPDATE_SOURCE",
                "CIEL_RUNTIME_UPDATE_BRANCH",
            }
        }
        with (
            mock.patch.dict("os.environ", environment, clear=True),
            mock.patch("ciel_runtime_support.package_lifecycle.subprocess.run", return_value=mock.Mock(returncode=returncode, stdout="")),
        ):
            return lifecycle.run()

    def test_npm_nightly_install_moves_to_the_github_branch_head(self):
        installed, written, outputs = [], [], []
        lifecycle = self.lifecycle(
            "0.2.51-nightly.20260924-003723.ae32250",
            remote={"nightly": NIGHTLY},
            installed=installed,
            written=written,
            outputs=outputs,
        )
        self.assertTrue(self.run_update(lifecycle))
        self.assertEqual([source.tarball_url(NIGHTLY)], installed)
        self.assertEqual(
            [(Path("/pkg"), {"source": "github", "repository": source.REPOSITORY, "branch": "nightly", "sha": NIGHTLY})],
            written,
        )
        self.assertTrue(any("nightly@e7062c7" in line for line in outputs))

    def test_github_install_at_the_branch_head_is_left_alone(self):
        installed = []
        lifecycle = self.lifecycle(
            "0.2.51", remote={"nightly": NIGHTLY}, marker={"branch": "nightly", "sha": NIGHTLY}, installed=installed
        )
        self.assertFalse(self.run_update(lifecycle))
        self.assertEqual([], installed)

    def test_unreadable_branch_head_falls_back_to_npm(self):
        installed, written = [], []
        lifecycle = self.lifecycle("0.2.51", remote={}, npm_latest="0.2.52", installed=installed, written=written)
        self.assertTrue(self.run_update(lifecycle))
        self.assertEqual(["@oneciel-ai/ciel-runtime@latest"], installed)
        self.assertEqual([], written)

    def test_failed_install_records_nothing(self):
        written = []
        lifecycle = self.lifecycle("0.2.51", remote={"main": MAIN}, written=written)
        self.assertFalse(self.run_update(lifecycle, returncode=1))
        self.assertEqual([], written)

    def test_npm_source_can_be_forced(self):
        installed = []
        lifecycle = self.lifecycle("0.2.51", remote={"main": MAIN}, npm_latest="0.2.52", installed=installed)
        with (
            mock.patch.dict("os.environ", {"CIEL_RUNTIME_UPDATE_SOURCE": "npm"}),
            mock.patch("ciel_runtime_support.package_lifecycle.subprocess.run", return_value=mock.Mock(returncode=0, stdout="")),
        ):
            self.assertTrue(lifecycle.run())
        self.assertEqual(["@oneciel-ai/ciel-runtime@latest"], installed)


if __name__ == "__main__":
    unittest.main()
