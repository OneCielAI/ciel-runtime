"""Synology NAS backup target: File Station Web API, reached by QuickConnect ID or address.

Requests follow Synology's File Station API guide (SYNO.API.Info, SYNO.API.Auth
login/logout, SYNO.FileStation.Upload/Download/List/Delete/CreateFolder).
A QuickConnect ID is resolved the way Synology's own clients do it (not an
officially documented API; measured 2026-10-10 against the user's NAS):
``get_server_info`` at global.quickconnect.to names the NAS's DDNS name and a
control host; ``request_tunnel`` there names the relay region, and the NAS
answers at ``https://<id>.<region>.quickconnect.to`` after one call to its
``pingpong_path`` (which sets the relay cookie).  The DDNS address is tried
first, then the relay.  TLS is always verified.

The password is never part of the target settings: either an environment
reference (``password=${SYNOLOGY_PASSWORD}``) or ``password_file``, a file the
menu writes with Windows DPAPI (readable only by the same Windows user).
Every failure is an OSError, so a backup stops with the reason instead of
skipping chunks.
"""

from __future__ import annotations

import ctypes
import http.cookiejar
import json
import os
import secrets
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

QUICKCONNECT_GLOBAL_URL = "https://global.quickconnect.to/Serv.php"
DPAPI_ENTROPY = b"ciel-runtime session backup"
_REQUEST_TIMEOUT = 120.0
_PROBE_TIMEOUT = 8.0
_RELOGIN_CODES = {106, 107, 119}
_COMMON_ERRORS = {
    100: "unknown error", 101: "no parameter of API, method or version", 102: "the API does not exist",
    103: "the method does not exist", 104: "this API version is not supported", 105: "no permission for this session",
    106: "session timeout", 107: "session interrupted by a duplicate login", 119: "SID not found",
}
_AUTH_ERRORS = {
    400: "no such account or incorrect password", 401: "account disabled", 402: "permission denied",
    403: "2-step verification code required (use an account without 2-step verification)",
    404: "failed to authenticate the 2-step verification code", 407: "this IP address is blocked by the NAS",
}
_FILE_ERRORS = {
    400: "invalid parameter of file operation", 401: "unknown error of file operation", 402: "system is too busy",
    403: "invalid user does this file operation", 404: "invalid group does this file operation",
    407: "operation not permitted", 408: "no such file or directory", 409: "non-supported file system",
    414: "file already exists", 415: "disk quota exceeded", 416: "no space left on the NAS volume",
    418: "illegal name or path", 419: "illegal file name", 421: "device or resource busy",
    1800: "upload size mismatch", 1801: "upload timed out", 1802: "no filename in the upload", 1803: "upload cancelled",
    1805: "file exists and overwrite was not given",
}
Opener = Callable[..., Any]


class SynologyError(OSError):
    """A NAS request failed; ``code`` is the DSM error code when there is one."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise SynologyError("a stored NAS password needs Windows (DPAPI); use password=${ENV_NAME} instead")
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    source = ctypes.create_string_buffer(data, len(data))
    entropy_buffer = ctypes.create_string_buffer(DPAPI_ENTROPY, len(DPAPI_ENTROPY))
    blob_in = _Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_char)))
    entropy = _Blob(len(DPAPI_ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN: never show a prompt.
    if not call(ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(blob_out)):
        raise SynologyError(f"DPAPI {'protect' if protect else 'unprotect'} failed (error {ctypes.GetLastError()})")  # type: ignore[attr-defined]
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def store_password(path: Path, password: str) -> None:
    """Write ``password`` for this Windows user only (DPAPI)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_bytes(_dpapi(password.encode("utf-8"), True))
    os.replace(temporary, path)


def load_password(path: Path) -> str:
    try:
        sealed = Path(path).read_bytes()
    except OSError as error:
        raise SynologyError(f"the stored NAS password {path} cannot be read: {error}") from error
    return _dpapi(sealed, False).decode("utf-8")


def _post_json(opener: Opener, url: str, payload: Any, timeout: float) -> Any:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"content-type": "application/json"})
    with opener(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def quickconnect_addresses(quickconnect_id: str, opener: Opener, timeout: float = 20.0) -> list[tuple[str, str]]:
    """(origin, pingpong path) to try for a QuickConnect ID: the DDNS name, then the relay."""

    def payload(command: str) -> list[dict[str, Any]]:
        return [{"version": 1, "command": command, "stop_when_error": False, "stop_when_success": command == "request_tunnel",
                 "id": "mainapp_https", "serverID": quickconnect_id, "is_gofile": False, "path": ""}]

    try:
        rows = _post_json(opener, QUICKCONNECT_GLOBAL_URL, payload("get_server_info"), timeout)
    except (OSError, ValueError) as error:
        raise SynologyError(f"QuickConnect lookup for {quickconnect_id!r} failed: {error}") from error
    info = rows[0] if isinstance(rows, list) and rows else {}
    if info.get("errno") != 0:
        raise SynologyError(f"QuickConnect ID {quickconnect_id!r} was not found (errno {info.get('errno')})")
    server = info.get("server") or {}
    service = info.get("service") or {}
    addresses: list[tuple[str, str]] = []
    ddns = str(server.get("ddns") or "")
    port = int(service.get("port") or 5001)
    if ddns and ddns.upper() != "NULL":
        addresses.append((f"https://{ddns}:{port}", ""))
    control_host = str((info.get("env") or {}).get("control_host") or "")
    if control_host:
        try:
            tunnel_rows = _post_json(opener, f"https://{control_host}/Serv.php", payload("request_tunnel"), timeout)
            tunnel = tunnel_rows[0] if isinstance(tunnel_rows, list) and tunnel_rows else {}
            region = str((tunnel.get("env") or {}).get("relay_region") or "")
            pingpong = str((tunnel.get("server") or {}).get("pingpong_path") or server.get("pingpong_path") or "")
            if tunnel.get("errno") == 0 and region:
                addresses.append((f"https://{quickconnect_id}.{region}.quickconnect.to", pingpong))
        except (OSError, ValueError):
            pass
    if not addresses:
        raise SynologyError(f"QuickConnect returned no usable address for {quickconnect_id!r}")
    return addresses


def _default_opener() -> Opener:
    cookies = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(cookies), urllib.request.HTTPSHandler(context=ssl.create_default_context())]
    return urllib.request.build_opener(*handlers).open


def _error(data: Mapping[str, Any], table: Mapping[int, str]) -> SynologyError:
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    code = error.get("code")
    reason = table.get(code) or _COMMON_ERRORS.get(code) or "error"
    details = error.get("errors")
    if isinstance(details, list) and details and isinstance(details[0], dict) and details[0].get("code"):
        inner = details[0]["code"]
        reason += f"; {inner}: {_FILE_ERRORS.get(inner) or 'error'}"
    return SynologyError(f"NAS error {code}: {reason}", code if isinstance(code, int) else None)


class DsmClient:
    """One File Station session on a NAS; connects and logs in on first use."""

    def __init__(self, address: str, account: str, password: Callable[[], str], *, opener: Opener | None = None) -> None:
        self.address = address.strip()
        self.account = account
        self._password = password
        self._opener = opener or _default_opener()
        self.origin = ""
        self._headers: dict[str, str] = {}
        self._apis: dict[str, dict[str, Any]] = {}
        self._sid = ""

    # connection -------------------------------------------------------------------------------------------------
    def _candidates(self) -> list[tuple[str, str]]:
        if self.address.startswith(("https://", "http://")):
            return [(self.address.rstrip("/"), "")]
        return quickconnect_addresses(self.address, self._opener)

    def connect(self) -> str:
        if self.origin:
            return self.origin
        failures = []
        for origin, pingpong in self._candidates():
            headers = {"Origin": origin, "Referer": origin + "/"} if ".quickconnect.to" in origin else {}
            try:
                if pingpong:
                    self._raw("GET", origin + pingpong, headers=headers, timeout=_PROBE_TIMEOUT)
                query = "api=SYNO.API.Info&version=1&method=query&query=SYNO.API.Auth,SYNO.FileStation."
                data = json.loads(self._raw("GET", f"{origin}/webapi/query.cgi?{query}", headers=headers, timeout=_PROBE_TIMEOUT))
            except (OSError, ValueError) as error:
                failures.append(f"{origin}: {type(error).__name__}: {str(error)[:120]}")
                continue
            if data.get("success"):
                self.origin, self._headers, self._apis = origin, headers, data.get("data") or {}
                return origin
            failures.append(f"{origin}: API info refused")
        raise SynologyError("cannot reach the NAS: " + "; ".join(failures))

    def _raw(self, method: str, url: str, *, body: bytes | None = None, headers: Mapping[str, str] | None = None,
             timeout: float = _REQUEST_TIMEOUT) -> bytes:
        request = urllib.request.Request(url, data=body, method=method, headers=dict(headers or {}))
        try:
            with self._opener(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            raise SynologyError(f"HTTP {error.code} from the NAS for {url.split('?')[0]}") from error

    def _api(self, name: str, wanted: int) -> tuple[str, int]:
        info = self._apis.get(name)
        if not info:
            raise SynologyError(f"the NAS does not offer {name}")
        version = max(int(info.get("minVersion") or 1), min(wanted, int(info.get("maxVersion") or wanted)))
        return f"/webapi/{info.get('path') or 'entry.cgi'}", version

    # session ----------------------------------------------------------------------------------------------------
    def login(self) -> None:
        self.connect()
        path, version = self._api("SYNO.API.Auth", 6)
        form = {"api": "SYNO.API.Auth", "version": str(version), "method": "login", "account": self.account,
                "passwd": self._password(), "session": "FileStation", "format": "sid"}
        data = self._decode(self._raw("POST", self.origin + path, body=urllib.parse.urlencode(form).encode(),
                                      headers={**self._headers, "content-type": "application/x-www-form-urlencoded"}))
        if not data.get("success"):
            raise _error(data, _AUTH_ERRORS)
        self._sid = str((data.get("data") or {}).get("sid") or "")
        if not self._sid:
            raise SynologyError("the NAS accepted the login but returned no session id")

    def logout(self) -> None:
        if not self._sid:
            return
        try:
            self.call("SYNO.API.Auth", 6, "logout", {"session": "FileStation"}, relogin=False)
        except OSError:
            pass
        self._sid = ""

    @staticmethod
    def _decode(raw: bytes) -> dict[str, Any]:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise SynologyError(f"the NAS answered with something that is not JSON: {raw[:80]!r}") from error
        if not isinstance(data, dict):
            raise SynologyError("the NAS answered with unexpected JSON")
        return data

    def call(self, api: str, wanted: int, method: str, params: Mapping[str, Any], *, relogin: bool = True) -> dict[str, Any]:
        if not self._sid:
            self.login()
        path, version = self._api(api, wanted)
        form = {"api": api, "version": str(version), "method": method, **{key: str(value) for key, value in params.items()}}
        url = f"{self.origin}{path}?_sid={urllib.parse.quote(self._sid)}"
        data = self._decode(self._raw("POST", url, body=urllib.parse.urlencode(form).encode(),
                                      headers={**self._headers, "content-type": "application/x-www-form-urlencoded"}))
        if data.get("success"):
            return data.get("data") or {}
        failure = _error(data, _FILE_ERRORS)
        if relogin and failure.code in _RELOGIN_CODES:
            self._sid = ""
            return self.call(api, wanted, method, params, relogin=False)
        raise failure

    # files ------------------------------------------------------------------------------------------------------
    def upload(self, folder: str, filename: str, data: bytes) -> None:
        if not self._sid:
            self.login()
        path, version = self._api("SYNO.FileStation.Upload", 2)
        boundary = "ciel" + secrets.token_hex(12)
        fields = {"api": "SYNO.FileStation.Upload", "version": "2" if version < 3 else "3", "method": "upload",
                  "path": folder, "create_parents": "true", "overwrite": "true" if version < 3 else "overwrite"}
        parts = [f'--{boundary}\r\ncontent-disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode() for key, value in fields.items()]
        # The file must be the last part (File Station API guide, Upload).
        parts.append(f'--{boundary}\r\ncontent-disposition: form-data; name="file"; filename="{filename}"\r\n'
                     "content-type: application/octet-stream\r\n\r\n".encode() + data + f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(parts)
        url = f"{self.origin}{path}?_sid={urllib.parse.quote(self._sid)}"
        result = self._decode(self._raw("POST", url, body=body, headers={
            **self._headers, "content-type": f"multipart/form-data; boundary={boundary}", "content-length": str(len(body))}))
        if not result.get("success"):
            failure = _error(result, _FILE_ERRORS)
            if failure.code in _RELOGIN_CODES:
                self._sid = ""
                return self.upload(folder, filename, data)
            raise failure

    def download(self, path: str) -> bytes:
        if not self._sid:
            self.login()
        api_path, version = self._api("SYNO.FileStation.Download", 2)
        query = urllib.parse.urlencode({"api": "SYNO.FileStation.Download", "version": str(version), "method": "download",
                                        "path": json.dumps([path]), "mode": "download", "_sid": self._sid})
        raw = self._raw("GET", f"{self.origin}{api_path}?{query}", headers=self._headers)
        # A failed download is a small JSON error; stored chunks are zlib data and manifests carry no "success".
        if raw[:1] == b"{" and len(raw) < 4096:
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                data = None
            if isinstance(data, dict) and data.get("success") is False:
                raise _error(data, _FILE_ERRORS)
        return raw

    def list_folder(self, folder: str) -> list[tuple[bool, str]]:
        """(is folder, name) for every entry; a missing folder is empty."""

        entries: list[tuple[bool, str]] = []
        offset = 0
        while True:
            try:
                data = self.call("SYNO.FileStation.List", 2, "list", {"folder_path": folder, "offset": offset, "limit": 1000})
            except SynologyError as error:
                if error.code == 408:
                    return []
                raise
            files = data.get("files") or []
            entries += [(bool(item.get("isdir")), str(item.get("name"))) for item in files if isinstance(item, dict)]
            offset += len(files)
            if not files or offset >= int(data.get("total") or 0):
                return entries

    def delete(self, path: str) -> None:
        self.call("SYNO.FileStation.Delete", 2, "delete", {"path": json.dumps([path]), "recursive": "false"})


class SynologyTarget:
    """A folder on a Synology NAS (``/shared-folder/sub/folder``)."""

    def __init__(self, name: str, client: DsmClient, folder: str) -> None:
        self.name = name
        self.client = client
        self.folder = "/" + folder.strip().strip("/")
        self._index: set[str] | None = None

    def _path(self, key: str) -> str:
        if ".." in key.split("/"):
            raise ValueError(f"unsafe key {key!r}")
        return f"{self.folder}/{key}"

    def _walk(self, prefix: str) -> list[str]:
        # Keys are at most two folders deep (blobs/<aa>/<sha>, snapshots/<ws>/<id>.json).
        found: list[str] = []
        level = [prefix.strip("/")]
        for _ in range(3):
            next_level = []
            for item in level:
                for is_dir, name in self.client.list_folder(self._path(item) if item else self.folder):
                    key = f"{item}/{name}" if item else name
                    (next_level if is_dir else found).append(key)
            level = next_level
            if not level:
                break
        return sorted(found)

    def _blob_index(self) -> set[str]:
        if self._index is None:
            self._index = set(self._walk("blobs"))
        return self._index

    def has(self, key: str) -> bool:
        if key.startswith("blobs/"):
            return key in self._blob_index()
        return key in set(self._walk(key.rsplit("/", 1)[0]))

    def put(self, key: str, data: bytes) -> None:
        folder, _, filename = self._path(key).rpartition("/")
        self.client.upload(folder, filename, data)
        if self._index is not None and key.startswith("blobs/"):
            self._index.add(key)

    def get(self, key: str) -> bytes:
        return self.client.download(self._path(key))

    def list(self, prefix: str) -> list[str]:
        return self._walk(prefix)

    def delete(self, key: str) -> None:
        try:
            self.client.delete(self._path(key))
        except SynologyError as error:
            if error.code != 408:
                raise
        if self._index is not None:
            self._index.discard(key)

    def close(self) -> None:
        self.client.logout()


def build_synology_target(name: str, spec: Mapping[str, Any], expand: Callable[[Any, str], str],
                          *, opener: Opener | None = None) -> SynologyTarget:
    for required in ("address", "account", "path"):
        if not spec.get(required):
            raise ValueError(f"synology target needs {required}=... (address = QuickConnect ID or https://host:port)")
    if not str(spec["path"]).startswith("/"):
        raise ValueError("synology path starts with a shared folder, e.g. /backup/ciel")
    if spec.get("password_file"):
        password_file = Path(str(spec["password_file"]))

        def password() -> str:
            return load_password(password_file)
    elif spec.get("password"):
        if "${" not in str(spec["password"]) and not str(spec["password"]).startswith("$"):
            raise ValueError("synology password must be an environment reference like ${SYNOLOGY_PASSWORD}, not the value")
        reference = spec["password"]

        def password() -> str:
            return expand(reference, "password")
    else:
        raise ValueError("synology target needs password_file=... (set in the menu) or password=${ENV_NAME}")
    client = DsmClient(str(spec["address"]), str(spec["account"]), password, opener=opener)
    return SynologyTarget(name, client, str(spec["path"]))


__all__ = [
    "DsmClient",
    "SynologyError",
    "SynologyTarget",
    "build_synology_target",
    "load_password",
    "quickconnect_addresses",
    "store_password",
]
