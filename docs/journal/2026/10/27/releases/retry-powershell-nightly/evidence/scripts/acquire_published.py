"""Fetch the exact registry tarball for one commit; verify digests; extract safely."""

import base64
import hashlib
import json
import sys
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path

commit = sys.argv[1]
out_dir = Path(sys.argv[2])

version = sys.argv[3]  # exact published version for this commit
url = "https://registry.npmjs.org/" + urllib.parse.quote("@one-ciel-ai/ciel-runtime", safe="") + "/" + version
with urllib.request.urlopen(url, timeout=60) as response:
    meta = json.load(response)
assert meta["gitHead"] == commit
with urllib.request.urlopen("https://registry.npmjs.org/-/package/@one-ciel-ai%2Fciel-runtime/dist-tags", timeout=60) as response:
    tags = json.load(response)
assert tags["nightly"] == version
registry = {"dist-tags": tags}
root = out_dir / "payload"
download = root / "download"
unpack = root / "unpack"
download.mkdir(parents=True, exist_ok=True)
unpack.mkdir(parents=True, exist_ok=True)
with urllib.request.urlopen(meta["dist"]["tarball"], timeout=120) as response:
    data = response.read()
assert hashlib.sha1(data).hexdigest() == meta["dist"]["shasum"]
assert (
    "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
    == meta["dist"]["integrity"]
)
archive = download / "published.tgz"
archive.write_bytes(data)
with tarfile.open(archive) as tf:
    members = tf.getmembers()
    for member in members:
        target = (unpack / member.name).resolve()
        assert target.is_relative_to(unpack.resolve()), member.name
        assert member.isdir() or member.isfile(), member.name
    tf.extractall(unpack, members=members)
record = dict(
    version=version,
    gitHead=commit,
    tags=registry["dist-tags"],
    dist=meta["dist"],
    tarball_sha256=hashlib.sha256(data).hexdigest(),
    tarball_bytes=len(data),
    files=len(members),
    package_dir=str(unpack / "package"),
)
(out_dir / "published.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))
