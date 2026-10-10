"""Synology QuickConnect backup target against an in-memory DSM (File Station API) and QuickConnect service."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
import zlib
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path
from unittest import mock

from ciel_runtime_support import session_backup_menu, session_backup_synology
from ciel_runtime_support.session_backup_cli import BackupContext, run_backup_command
from ciel_runtime_support.session_backup_service import load_settings, save_settings
from ciel_runtime_support.session_backup_store import ChunkWriter, TargetWriteError, list_snapshots
from ciel_runtime_support.session_backup_synology import DsmClient, SynologyError, SynologyTarget, build_synology_target
from ciel_runtime_support.session_backup_targets import build_target

RELAY = "https://nas1.us9.quickconnect.to"
API_INFO = {name: {"minVersion": 1 if name != "SYNO.FileStation.Upload" else 2, "maxVersion": 7 if name == "SYNO.API.Auth" else 2,
                   "path": "entry.cgi"}
            for name in ("SYNO.API.Auth", "SYNO.FileStation.List", "SYNO.FileStation.Download", "SYNO.FileStation.Upload",
                         "SYNO.FileStation.Delete", "SYNO.FileStation.CreateFolder")}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeNas:
    """QuickConnect lookup, relay and DSM File Station with one shared folder /backup."""

    def __init__(self, password: str = "right") -> None:
        self.password = password
        self.files: dict[str, bytes] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.sessions: set[str] = set()
        self.expire_next = False
        self.relay_cookie = False
        self.full = False

    def __call__(self, request, timeout=None):
        url = request.full_url
        body = request.data or b""
        headers = {key.lower(): value for key, value in request.header_items()}
        if url == session_backup_synology.QUICKCONNECT_GLOBAL_URL:
            command = json.loads(body)[0]["command"]
            assert command == "get_server_info" and json.loads(body)[0]["serverID"] == "nas1"
            return _Response(json.dumps([{"errno": 0, "env": {"control_host": "ctl.example", "relay_region": "us9"},
                                          "server": {"ddns": "nas1.synology.me", "pingpong_path": "/webman/pingpong.cgi?a"},
                                          "service": {"port": 5001}}]).encode())
        if url == "https://ctl.example/Serv.php":
            return _Response(json.dumps([{"errno": 0, "env": {"relay_region": "us9"},
                                          "server": {"pingpong_path": "/webman/pingpong.cgi?a"}}]).encode())
        if url.startswith("https://nas1.synology.me"):
            raise urllib.error.URLError("timed out")
        assert url.startswith(RELAY), url
        assert headers.get("origin") == RELAY, "relay requests carry the relay Origin"
        path, _, query = url[len(RELAY):].partition("?")
        if path == "/webman/pingpong.cgi":
            self.relay_cookie = True
            return _Response(b'{"success": true}')
        assert self.relay_cookie, "pingpong comes first on the relay"
        if path == "/webapi/query.cgi":
            return _Response(json.dumps({"success": True, "data": API_INFO}).encode())
        params = dict(urllib.parse.parse_qsl(query))
        if headers.get("content-type", "").startswith("multipart/form-data"):
            message = BytesParser(policy=HTTP).parsebytes(b"content-type: " + headers["content-type"].encode() + b"\r\n\r\n" + body)
            form, upload = {}, None
            for part in message.iter_parts():
                if part.get_filename():
                    upload = (part.get_filename(), part.get_payload(decode=True))
                else:
                    form[part.get_param("name", header="content-disposition")] = part.get_content().strip()
            params.update(form)
            params["_upload"] = upload
        elif body:
            params.update(dict(urllib.parse.parse_qsl(body.decode())))
        self.calls.append((params.get("api", ""), params.get("method", ""), {k: v for k, v in params.items() if k != "passwd"}))
        return _Response(self.dsm(params))

    def _ok(self, data=None) -> bytes:
        return json.dumps({"success": True, "data": data or {}}).encode()

    def _fail(self, code: int) -> bytes:
        return json.dumps({"success": False, "error": {"code": code}}).encode()

    def dsm(self, params: dict) -> bytes:
        api, method = params.get("api"), params.get("method")
        if api == "SYNO.API.Auth" and method == "login":
            if params.get("account") != "ciel" or params.get("passwd") != self.password:
                return self._fail(400)
            sid = f"sid{len(self.sessions) + 1}"
            self.sessions.add(sid)
            return self._ok({"sid": sid})
        if params.get("_sid") not in self.sessions:
            return self._fail(119)
        if self.expire_next:
            self.expire_next = False
            self.sessions.discard(params["_sid"])
            return self._fail(106)
        if api == "SYNO.API.Auth" and method == "logout":
            self.sessions.discard(params["_sid"])
            return self._ok()
        if api == "SYNO.FileStation.Upload":
            assert params["path"].startswith("/backup/") and params["create_parents"] == "true" and params["overwrite"] == "true"
            if self.full:
                return self._fail(416)
            name, data = params["_upload"]
            self.files[f"{params['path']}/{name}"] = data
            return self._ok()
        if api == "SYNO.FileStation.Download":
            path = json.loads(params["path"])[0]
            if path not in self.files:
                return self._fail(408)
            return self.files[path]
        if api == "SYNO.FileStation.List":
            folder = params["folder_path"].rstrip("/") + "/"
            names = {}
            for path in self.files:
                if path.startswith(folder):
                    head, slash, _ = path[len(folder):].partition("/")
                    names[head] = names.get(head, False) or bool(slash)
            if not names:
                return self._fail(408)
            rows = [{"name": name, "isdir": isdir, "path": folder + name} for name, isdir in sorted(names.items())]
            offset, limit = int(params.get("offset", 0)), int(params.get("limit", 1000))
            return self._ok({"files": rows[offset:offset + limit], "total": len(rows), "offset": offset})
        if api == "SYNO.FileStation.Delete":
            path = json.loads(params["path"])[0]
            if path not in self.files:
                return self._fail(408)
            del self.files[path]
            return self._ok()
        return self._fail(102)


def make_target(nas: FakeNas, password: str = "right") -> SynologyTarget:
    return SynologyTarget("nas", DsmClient("nas1", "ciel", lambda: password, opener=nas), "/backup/ciel")


class ClientTests(unittest.TestCase):
    def test_quickconnect_id_falls_back_to_the_relay_when_ddns_is_unreachable(self):
        nas = FakeNas()
        client = DsmClient("nas1", "ciel", lambda: "right", opener=nas)
        self.assertEqual(RELAY, client.connect())
        self.assertTrue(nas.relay_cookie)

    def test_round_trip_dedupe_list_and_delete(self):
        nas = FakeNas()
        target = make_target(nas)
        self.assertFalse(target.has("blobs/ab/abcd"))
        target.put("blobs/ab/abcd", b"\x78\x9cchunk")
        manifest = json.dumps({"format": 1, "entries": []}).encode()
        target.put("snapshots/ws1/20261010.json", manifest)
        self.assertTrue(target.has("blobs/ab/abcd"))
        self.assertTrue(target.has("snapshots/ws1/20261010.json"))
        self.assertEqual(b"\x78\x9cchunk", target.get("blobs/ab/abcd"))
        self.assertEqual(manifest, target.get("snapshots/ws1/20261010.json"))  # JSON content is not read as an error
        self.assertEqual(["snapshots/ws1/20261010.json"], target.list("snapshots/"))
        target.delete("blobs/ab/abcd")
        target.delete("blobs/ab/abcd")  # already gone: no error
        self.assertFalse(target.has("blobs/ab/abcd"))
        with self.assertRaises(SynologyError) as missing:
            target.get("blobs/zz/none")
        self.assertEqual(408, missing.exception.code)
        target.close()
        self.assertEqual([], sorted(nas.sessions))
        self.assertTrue(all("passwd" not in call[2] for call in nas.calls))

    def test_wrong_password_stops_a_backup(self):
        writer = ChunkWriter([make_target(FakeNas(), password="wrong")])
        with self.assertRaises(TargetWriteError) as stopped:
            writer.write(b"data")
        self.assertIn("incorrect password", str(stopped.exception))

    def test_full_nas_volume_stops_a_backup(self):
        nas = FakeNas()
        nas.full = True
        with self.assertRaises(TargetWriteError) as stopped:
            ChunkWriter([make_target(nas)]).write(b"data")
        self.assertIn("no space left on the NAS volume", str(stopped.exception))

    def test_parallel_chunks_share_one_login(self):
        nas = FakeNas()
        target = make_target(nas)
        writer = ChunkWriter([target], jobs=8)
        digests = [writer.write(bytes([index]) * 70000) for index in range(20)]
        writer.drain()
        writer.close()
        self.assertEqual(1, sum(1 for _api, method, _ in nas.calls if method == "login"))
        self.assertEqual(20, sum(1 for path in nas.files if "/blobs/" in path))
        self.assertEqual(bytes([7]) * 70000, zlib.decompress(target.get(f"blobs/{digests[7][:2]}/{digests[7]}")))

    def test_an_expired_session_logs_in_again_once(self):
        nas = FakeNas()
        target = make_target(nas)
        target.put("blobs/ab/one", b"1")
        nas.expire_next = True
        target.put("blobs/ab/two", b"2")
        self.assertEqual({"/backup/ciel/blobs/ab/one", "/backup/ciel/blobs/ab/two"}, set(nas.files))
        self.assertEqual(2, sum(1 for api, method, _ in nas.calls if method == "login"))


class SpecTests(unittest.TestCase):
    def test_password_must_not_be_written_into_the_settings(self):
        expand = lambda value, field: value  # noqa: E731
        with self.assertRaisesRegex(ValueError, "environment reference"):
            build_synology_target("nas", {"address": "nas1", "account": "ciel", "path": "/backup/ciel", "password": "hunter2"}, expand)
        with self.assertRaisesRegex(ValueError, "password_file"):
            build_synology_target("nas", {"address": "nas1", "account": "ciel", "path": "/backup/ciel"}, expand)
        with self.assertRaisesRegex(ValueError, "shared folder"):
            build_synology_target("nas", {"address": "nas1", "account": "ciel", "path": "backup", "password": "${P}"}, expand)
        target = build_target("nas", {"type": "synology", "address": "nas1", "account": "ciel", "path": "/backup/ciel",
                                      "password": "${NAS_PASSWORD}"}, {"NAS_PASSWORD": "right"})
        self.assertEqual("right", target.client._password())

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
    def test_stored_password_is_encrypted_for_this_user(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nas.dpapi"
            session_backup_synology.store_password(path, "pässword-1")
            self.assertNotIn(b"ssword", path.read_bytes())
            self.assertEqual("pässword-1", session_backup_synology.load_password(path))


@unittest.skipUnless(os.name == "nt", "the menu stores the password with DPAPI")
class MenuAndBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.config, self.cwd, self.home = base / "ciel", base / "work", base / "home"
        for folder in (self.config, self.cwd, self.home):
            folder.mkdir()
        (self.cwd / "notes.md").write_text("plan v1\n", encoding="utf-8")
        self.nas = FakeNas()
        patch = mock.patch.object(session_backup_synology, "_default_opener", return_value=self.nas)
        patch.start()
        self.addCleanup(patch.stop)
        space = mock.patch("ciel_runtime_support.session_backup_cli.shutil.disk_usage", return_value=mock.Mock(free=1024 ** 4))
        space.start()
        self.addCleanup(space.stop)

    def prompt(self, answers: dict[str, str], secrets_seen: list[str]):
        def ask(label, default, secret=False):
            if secret:
                secrets_seen.append(label)
            for start, value in answers.items():
                if label.startswith(start):
                    return value
            return default
        return ask

    def test_add_test_use_change_password_and_remove_from_the_menu(self):
        secrets_seen: list[str] = []
        answers = {"Target type": "synology", "Name": "nas", "Synology QuickConnect ID": "nas1", "DSM account": "ciel",
                   "NAS folder": "/backup/ciel", "DSM password": "right", "Test it now": "yes", "Use it": "yes"}
        lines = session_backup_menu.apply(self.config, self.cwd, "target-add", self.prompt(answers, secrets_seen))
        self.assertIn("Added target nas (synology · ciel@nas1:/backup/ciel · password stored).", lines[0])
        self.assertIn("Test: ", lines[1])
        self.assertNotIn("failed", lines[1])
        self.assertEqual(["DSM password (not shown; stored encrypted for this Windows user) or ${ENV_NAME}"], secrets_seen)
        spec = load_settings(self.config)["targets"]["nas"]
        self.assertNotIn("password", spec)
        stored = Path(spec["password_file"])
        self.assertNotIn(b"right", stored.read_bytes())
        self.assertNotIn("right", (self.config / "session-backup.json").read_text(encoding="utf-8"))
        rows, values = session_backup_menu.panel_rows(self.config, self.cwd)
        self.assertTrue(any("[x] nas  [synology" in row for row in rows))
        self.assertTrue(any("Synology QuickConnect" in row for row in rows))

        self.nas.password = "changed"
        lines = session_backup_menu.apply(self.config, self.cwd, "target:nas", self.prompt({"nas:": "password", "DSM password": "changed"}, []))
        self.assertEqual(["nas: password stored for this Windows user."], lines)
        lines = session_backup_menu.apply(self.config, self.cwd, "target:nas", self.prompt({"nas:": "test"}, []))
        self.assertTrue(lines[0].startswith("nas: ") and "failed" not in lines[0], lines)

        lines = session_backup_menu.apply(self.config, self.cwd, "target:nas", self.prompt({"nas:": "remove", "Remove target": "yes"}, []))
        self.assertEqual(["Removed target nas."], lines)
        self.assertFalse(stored.exists())

    def test_backups_to_the_nas_are_incremental_and_restore(self):
        session_backup_synology.store_password(self.config / "backup-credentials" / "nas.dpapi", "right")
        settings = load_settings(self.config)
        settings["targets"]["nas"] = {"type": "synology", "address": "nas1", "account": "ciel", "path": "/backup/ciel",
                                      "password_file": str(self.config / "backup-credentials" / "nas.dpapi")}
        settings["default_targets"] = ["nas"]
        save_settings(self.config, settings)
        outputs: list[str] = []
        context = BackupContext(self.config, self.home, self.home, {}, {"ciel_runtime": "test"}, cwd=lambda: self.cwd, output=outputs.append)
        self.assertEqual(0, run_backup_command(["create", "--json"], context))
        first = json.loads(outputs[-1])
        self.assertEqual(0, run_backup_command(["create", "--json"], context))
        second = json.loads(outputs[-1])
        self.assertGreater(first["stats"]["uploaded_chunks"], 0)
        self.assertEqual(0, second["stats"]["uploaded_chunks"])  # nothing changed: nothing new is uploaded
        listing = make_target(self.nas)
        self.assertEqual(2, len(list_snapshots(listing)))
        listing.close()
        (self.cwd / "notes.md").write_text("lost\n", encoding="utf-8")
        self.assertEqual(0, run_backup_command(["restore", first["id"], "--target", "nas", "--no-safety", "--json"], context))
        self.assertEqual("plan v1\n", (self.cwd / "notes.md").read_text(encoding="utf-8"))
        self.assertEqual(0, run_backup_command(["verify", second["id"], "--target", "nas"], context))
        self.assertEqual(set(), self.nas.sessions, "every command logs out of the NAS when it ends")


if __name__ == "__main__":
    unittest.main()
