"""Fetch exact registry tarball; verify digest; safely extract into fresh directories."""
import base64
import hashlib
import json
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

commit = "19627938ad5b0b9682bd87e1000ff0ddc01784f4"
evidence = Path(__file__).resolve().parent.parent
url = "https://registry.npmjs.org/" + urllib.parse.quote("@one-ciel-ai/ciel-runtime", safe="")
with urllib.request.urlopen(url, timeout=60) as response:
    registry = json.load(response)
versions = [v for v, row in registry["versions"].items() if row.get("gitHead") == commit]
assert len(versions) == 1, versions
version = versions[0]
meta = registry["versions"][version]
assert registry["dist-tags"]["nightly"] == version
root = Path(tempfile.mkdtemp(prefix="ciel-published-1962793-"))
download = root / "download"
unpack = root / "unpack"
download.mkdir()
unpack.mkdir()
with urllib.request.urlopen(meta["dist"]["tarball"], timeout=120) as response:
    data = response.read()
assert hashlib.sha1(data).hexdigest() == meta["dist"]["shasum"]
assert "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode() == meta["dist"]["integrity"]
archive = download / "published.tgz"
archive.write_bytes(data)
with tarfile.open(archive) as tf:
    members = tf.getmembers()
    for member in members:
        target = (unpack / member.name).resolve()
        assert target.is_relative_to(unpack.resolve()), member.name
        assert member.isdir() or member.isfile(), member.name
    tf.extractall(unpack, members=members)
record = dict(version=version, gitHead=commit, tags=registry["dist-tags"], dist=meta["dist"], tarball_sha256=hashlib.sha256(data).hexdigest(), package_dir=str(unpack / "package"))
(evidence / "published.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))
