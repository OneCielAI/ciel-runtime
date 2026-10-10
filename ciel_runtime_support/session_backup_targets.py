"""Backup targets beyond a local folder: SSH (sftp), S3-compatible storage, rclone.

Each target stores named byte blobs.  Remote credentials are never written into
Ciel's settings: SSH uses the user's key/agent, rclone its own config, and S3
keys are ``${ENV_NAME}`` references expanded at use time.

SSH and rclone stage new blobs in a temporary folder and send them in one
batch on ``flush()``; the snapshot code flushes before it writes the manifest,
so a manifest never names chunks a target does not have.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any, Callable, Mapping

from ciel_runtime_support.remote_instructions import expand_environment_references
from ciel_runtime_support.session_backup_store import LocalTarget

TARGET_TYPES = ("local", "ssh", "s3", "rclone")
Runner = Callable[..., subprocess.CompletedProcess]


def _run(command: list[str], *, input_text: str | None = None, timeout: float = 600, runner: Runner = subprocess.run) -> subprocess.CompletedProcess:
    return runner(command, input=input_text, capture_output=True, text=True, timeout=timeout)


class _StagedTarget:
    """Shared staging for targets that send many files best in one command."""

    name = ""

    def __init__(self) -> None:
        self._staging = Path(tempfile.mkdtemp(prefix="ciel-backup-stage-"))
        self._staged: list[str] = []
        self._index: set[str] | None = None

    def _stage(self, key: str, data: bytes) -> None:
        path = self._staging.joinpath(*key.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._staged.append(key)
        if self._index is not None:
            self._index.add(key)

    def _blob_index(self) -> set[str]:
        if self._index is None:
            self._index = set(self._list_remote("blobs/"))
        return self._index

    def has(self, key: str) -> bool:
        if key.startswith("blobs/"):
            return key in self._blob_index()
        return key in set(self._list_remote(key.rsplit("/", 1)[0] + "/"))

    def put(self, key: str, data: bytes) -> None:
        self._stage(key, data)
        if not key.startswith("blobs/"):
            self.flush()

    def flush(self) -> None:
        if self._staged:
            self._send_staged(list(self._staged))
            self._staged.clear()
            shutil.rmtree(self._staging, ignore_errors=True)
            self._staging.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        shutil.rmtree(self._staging, ignore_errors=True)

    def _list_remote(self, prefix: str) -> list[str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def _send_staged(self, keys: list[str]) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class SshTarget(_StagedTarget):
    """A folder on an SSH host, through ``sftp -b`` (key or agent auth only)."""

    def __init__(self, name: str, host: str, path: str, *, port: str = "", identity: str = "", known_hosts: str = "",
                 runner: Runner = subprocess.run) -> None:
        super().__init__()
        self.name = name
        self.host = host
        self.path = path.rstrip("/") or "."
        self.port = port
        self.identity = identity
        self.known_hosts = known_hosts
        self.runner = runner

    def _sftp(self, lines: list[str], *, check: bool = True) -> str:
        command = ["sftp", "-q", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-b", "-"]
        if self.port:
            command += ["-P", str(self.port)]
        if self.identity:
            command += ["-i", os.path.expanduser(self.identity)]
        if self.known_hosts:
            command += ["-o", f"UserKnownHostsFile={os.path.expanduser(self.known_hosts)}"]
        command.append(self.host)
        result = _run(command, input_text="\n".join(lines) + "\n", runner=self.runner)
        if check and result.returncode != 0:
            raise OSError(f"sftp to {self.host} failed: {(result.stderr or result.stdout).strip()[:300]}")
        return result.stdout

    def _remote(self, key: str) -> str:
        return f"{self.path}/{key}" if key else self.path

    def _list_remote(self, prefix: str) -> list[str]:
        # Keys are at most two folders deep (blobs/<aa>/<sha>, snapshots/<ws>/<id>.json);
        # ``ls -l`` marks folders with a leading "d".
        prefix = prefix.rstrip("/")
        found: list[str] = []
        level = [prefix]
        for _ in range(3):
            if not level:
                break
            output = self._sftp([f"-ls -l {self._remote(item)}" for item in level], check=False)
            entries: dict[str, list[tuple[bool, str]]] = {}
            current = None
            for raw in output.splitlines():
                line = raw.strip()
                if line.startswith("sftp>"):
                    current = line.split(" ")[-1]
                    continue
                if current is not None and line and line[0] in "d-l":
                    name = line.split()[-1].rsplit("/", 1)[-1]
                    if name not in (".", ".."):
                        entries.setdefault(current, []).append((line[0] == "d", name))
            next_level = []
            for item in level:
                for is_dir, name in entries.get(self._remote(item), []):
                    key = f"{item}/{name}" if item else name
                    (next_level if is_dir else found).append(key)
            level = next_level
        return sorted(found)

    def _send_staged(self, keys: list[str]) -> None:
        folders = sorted({key.rsplit("/", 1)[0] for key in keys})
        lines = []
        for folder in folders:
            parts = folder.split("/")
            for depth in range(1, len(parts) + 1):
                lines.append(f"-mkdir {self._remote('/'.join(parts[:depth]))}")
        lines.insert(0, f"-mkdir {self.path}")
        for key in keys:
            local = self._staging.joinpath(*key.split("/")).as_posix()
            lines.append(f'put "{local}" {self._remote(key)}')
        self._sftp(lines)

    def get(self, key: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="ciel-backup-get-") as folder:
            local = Path(folder) / "blob"
            self._sftp([f'get {self._remote(key)} "{local.as_posix()}"'])
            return local.read_bytes()

    def list(self, prefix: str) -> list[str]:
        return self._list_remote(prefix)

    def delete(self, key: str) -> None:
        self._sftp([f"-rm {self._remote(key)}"], check=False)
        if self._index is not None:
            self._index.discard(key)


class RcloneTarget(_StagedTarget):
    """Any rclone remote (Google Drive, OneDrive, Dropbox, S3, ...), e.g. ``gdrive:ciel-backups``."""

    def __init__(self, name: str, remote: str, *, binary: str = "rclone", runner: Runner = subprocess.run) -> None:
        super().__init__()
        self.name = name
        self.remote = remote.rstrip("/")
        self.binary = binary
        self.runner = runner

    def _rclone(self, *args: str, check: bool = True) -> str:
        result = _run([self.binary, *args], runner=self.runner)
        if check and result.returncode != 0:
            raise OSError(f"rclone {args[0]} failed: {(result.stderr or result.stdout).strip()[:300]}")
        return result.stdout

    def _list_remote(self, prefix: str) -> list[str]:
        prefix = prefix.rstrip("/")
        output = self._rclone("lsf", "-R", "--files-only", f"{self.remote}/{prefix}", check=False)
        return sorted(f"{prefix}/{line.strip()}" for line in output.splitlines() if line.strip())

    def _send_staged(self, keys: list[str]) -> None:
        self._rclone("copy", str(self._staging), self.remote, "--no-traverse")

    def get(self, key: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="ciel-backup-get-") as folder:
            self._rclone("copyto", f"{self.remote}/{key}", str(Path(folder) / "blob"))
            return (Path(folder) / "blob").read_bytes()

    def list(self, prefix: str) -> list[str]:
        return self._list_remote(prefix)

    def delete(self, key: str) -> None:
        self._rclone("deletefile", f"{self.remote}/{key}", check=False)
        if self._index is not None:
            self._index.discard(key)


class S3Target:
    """S3-compatible object storage (AWS, MinIO, R2, ...) with SigV4, path-style URLs."""

    def __init__(self, name: str, endpoint: str, bucket: str, *, prefix: str = "", region: str = "us-east-1",
                 access_key: str, secret_key: str, opener: Callable[..., Any] = urllib.request.urlopen,
                 clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc)) -> None:
        self.name = name
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.region = region or "us-east-1"
        self.access_key = access_key
        self.secret_key = secret_key
        self.opener = opener
        self.clock = clock

    def _object(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def _request(self, method: str, key: str = "", *, query: Mapping[str, str] | None = None, body: bytes = b"") -> tuple[int, bytes]:
        parsed = urllib.parse.urlsplit(self.endpoint)
        path = "/" + self.bucket + ("/" + urllib.parse.quote(self._object(key), safe="/~") if key else "")
        canonical_query = "&".join(
            f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}" for k, v in sorted((query or {}).items())
        )
        now = self.clock()
        amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()
        headers = {"host": parsed.netloc, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
        signed = ";".join(sorted(headers))
        canonical = "\n".join([
            method, path, canonical_query,
            "".join(f"{name}:{headers[name]}\n" for name in sorted(headers)), signed, payload_hash,
        ])
        scope = f"{day}/{self.region}/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        key_bytes = ("AWS4" + self.secret_key).encode()
        for part in (day, self.region, "s3", "aws4_request"):
            key_bytes = hmac.new(key_bytes, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(key_bytes, to_sign.encode(), hashlib.sha256).hexdigest()
        request_headers = {
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, SignedHeaders={signed}, Signature={signature}",
        }
        url = f"{parsed.scheme}://{parsed.netloc}{path}" + (f"?{canonical_query}" if canonical_query else "")
        request = urllib.request.Request(url, data=body if method in ("PUT", "POST") else None, method=method, headers=request_headers)
        try:
            with self.opener(request, timeout=120) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as error:
            return int(error.code), error.read()

    def has(self, key: str) -> bool:
        status, _ = self._request("HEAD", key)
        return status == 200

    def put(self, key: str, data: bytes) -> None:
        status, body = self._request("PUT", key, body=data)
        if status not in (200, 201):
            raise OSError(f"S3 PUT {key} -> {status}: {body[:200]!r}")

    def get(self, key: str) -> bytes:
        status, body = self._request("GET", key)
        if status != 200:
            raise OSError(f"S3 GET {key} -> {status}")
        return body

    def list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token = ""
        strip = len(self.prefix) + 1 if self.prefix else 0
        while True:
            query = {"list-type": "2", "prefix": self._object(prefix)}
            if token:
                query["continuation-token"] = token
            status, body = self._request("GET", query=query)
            if status != 200:
                raise OSError(f"S3 LIST {prefix} -> {status}: {body[:200]!r}")
            root = ElementTree.fromstring(body)
            namespace = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
            keys += [node.text[strip:] for node in root.iter(f"{namespace}Key") if node.text]
            truncated = (root.findtext(f"{namespace}IsTruncated") or "").lower() == "true"
            token = root.findtext(f"{namespace}NextContinuationToken") or ""
            if not truncated or not token:
                return sorted(keys)

    def delete(self, key: str) -> None:
        self._request("DELETE", key)


def _expand(value: Any, environ: Mapping[str, str], field: str) -> str:
    text, missing = expand_environment_references(str(value or ""), environ)
    if missing:
        raise ValueError(f"{field} refers to unset environment variable(s): {', '.join(missing)}")
    return text


def build_target(name: str, spec: Mapping[str, Any], environ: Mapping[str, str]) -> Any:
    kind = str(spec.get("type") or "")
    if kind == "local":
        if not spec.get("path"):
            raise ValueError("local target needs path=DIR")
        return LocalTarget(Path(_expand(spec["path"], environ, "path")).expanduser(), name)
    if kind == "ssh":
        if not spec.get("host") or not spec.get("path"):
            raise ValueError("ssh target needs host=USER@HOST and path=REMOTE_DIR")
        return SshTarget(name, str(spec["host"]), str(spec["path"]), port=str(spec.get("port") or ""),
                         identity=str(spec.get("identity") or ""), known_hosts=str(spec.get("known_hosts") or ""))
    if kind == "rclone":
        if not spec.get("remote"):
            raise ValueError("rclone target needs remote=NAME:PATH")
        return RcloneTarget(name, str(spec["remote"]), binary=str(spec.get("binary") or "rclone"))
    if kind == "s3":
        for required in ("endpoint", "bucket", "access_key", "secret_key"):
            if not spec.get(required):
                raise ValueError(f"s3 target needs {required}=... (keys as ${{ENV_NAME}} references)")
        for secret in ("access_key", "secret_key"):
            if "${" not in str(spec[secret]) and not str(spec[secret]).startswith("$"):
                raise ValueError(f"s3 {secret} must be an environment reference like ${{AWS_SECRET_ACCESS_KEY}}, not the value")
        return S3Target(
            name, _expand(spec["endpoint"], environ, "endpoint"), str(spec["bucket"]),
            prefix=str(spec.get("prefix") or ""), region=str(spec.get("region") or "us-east-1"),
            access_key=_expand(spec["access_key"], environ, "access_key"),
            secret_key=_expand(spec["secret_key"], environ, "secret_key"),
        )
    raise ValueError(f"unknown target type {kind!r}; expected one of {', '.join(TARGET_TYPES)}")


__all__ = ["RcloneTarget", "S3Target", "SshTarget", "TARGET_TYPES", "build_target"]
