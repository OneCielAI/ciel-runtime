"""Deploy the exact registry release without deleting the live snapshot."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[8]
EVIDENCE = Path(__file__).resolve().parent
COMMIT = sys.argv[1]
CHANGED = ['ciel_runtime_support/runtime_compaction_binding.py']
SNAPSHOT = Path.home() / ".local/share/ciel-runtime"

def run(args, **kwargs):
    print("RUN", args, flush=True)
    result = subprocess.run(args, check=True, **kwargs)
    return result

def npm(*args, **kwargs):
    return subprocess.check_output(["cmd.exe", "/c", "npm", *args], text=True, **kwargs)

def health():
    result = {}
    for port in (9385, 9464, 9465, 9489, 9505, 9524):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                data = json.load(response)
            result[str(port)] = {key: data.get(key) for key in ("ok", "pid", "version")}
        except Exception as error:
            result[str(port)] = {"error": type(error).__name__}
    return result

target = sys.argv[2] if len(sys.argv) > 2 else "nightly"
metadata = json.loads(npm("view", "@one-ciel-ai/ciel-runtime@" + target, "version", "gitHead", "--json"))
assert metadata["gitHead"] == COMMIT, metadata
version = metadata["version"]
before = health()
print("REGISTRY", metadata, "BEFORE", before, flush=True)
with tempfile.TemporaryDirectory(prefix="ciel-release-") as temp:
    directory = Path(temp)
    packed = json.loads(npm("pack", "@one-ciel-ai/ciel-runtime@" + version, "--json", cwd=directory))[0]
    archive = directory / packed["filename"]
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        assert all(not m.issym() and not m.islnk() and not Path(m.name).is_absolute()
                   and ".." not in Path(m.name).parts for m in members)
        tar.extractall(directory, members=members)
    package = directory / "package"
    assert json.loads((package / "package.json").read_text())["version"] == version
    committed = {name: subprocess.check_output(["git", "show", COMMIT + ":" + name], cwd=ROOT)
                 for name in CHANGED}
    assert all((package / name).read_bytes() == data for name, data in committed.items())
    print(npm("install", "-g", "@one-ciel-ai/ciel-runtime@" + version), flush=True)
    targets = [(package / "ciel_runtime.py", SNAPSHOT / "ciel_runtime.py")]
    for name in ("ciel_runtime_support", "scripts"):
        targets.extend((p, SNAPSHOT / p.relative_to(package)) for p in (package / name).rglob("*") if p.is_file())
    bin_dir = Path.home() / ".local/bin"
    for name in ("ciel-runtime-menu.py", "ciel-runtime-tool-guard.py", "ciel-runtime", "ciel-runtime.cmd",
                 "ciel-runtime.ps1", "ciel-runtimectl", "ciel-runtimectl.cmd", "ciel-runtimectl.ps1",
                 "ciel-runtime-stop", "ciel-runtime-stop.cmd", "ciel-runtime-stop.ps1"):
        targets.append((package / name, bin_dir / name))
    for source, destination in targets:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".deploy-" + COMMIT[:7] + ".tmp")
        temporary.write_bytes(source.read_bytes())
        os.replace(temporary, destination)
    for source, destination in targets:
        assert source.read_bytes() == destination.read_bytes(), destination
    global_dir = Path(npm("root", "-g").strip()) / "@one-ciel-ai/ciel-runtime"
    assert all((global_dir / name).read_bytes() == data for name, data in committed.items())
    hashes = {name: {str(base): hashlib.sha256((base / name).read_bytes()).hexdigest()
                    for base in (package, SNAPSHOT, global_dir)} for name in CHANGED}
    source = (ROOT / "docs/journal/2026/10/24/fixes/launch-identity-readiness/evidence/run_focused.py").read_text()
    source = source.replace("root = Path(__file__).resolve().parents[8]", "root = Path(" + repr(str(SNAPSHOT)) + ")")
    source = source.replace('str(root / "tests")', repr(str(ROOT / "tests")))
    source = source.replace('suite = unittest.defaultTestLoader',
        'import ciel_runtime_support.runtime_compaction_binding as loaded; print("installed_module", loaded.__file__, flush=True)'
        + chr(10) + '    suite = unittest.defaultTestLoader')
    runner = directory / "installed_runner.py"
    runner.write_text(source, encoding="utf-8")
    names = ["test_runtime_compaction_readiness", "test_runtime_compaction_identity", "test_runtime_compaction_binding",
             "test_runtime_compaction_contention", "test_channel_pending_poll", "test_channel_pending_injection",
             "test_runtime_compaction_control", "test_runtime_compaction_checkpoint_evidence"]
    with (EVIDENCE / "installed-tests.txt").open("w", encoding="utf-8") as output:
        run([sys.executable, "-X", "utf8", "-I", str(runner), *names], cwd=directory,
            stdout=output, stderr=subprocess.STDOUT)
    after = health()
    for port, data in before.items():
        if data.get("ok"):
            assert after[port].get("ok") and after[port].get("pid") == data["pid"], (port, data, after[port])
    report = {"commit": COMMIT, "version": version, "registry": metadata,
              "copied_files": len(targets), "hashes": hashes, "health_before": before, "health_after": after,
              "installed_tests": "passed (see installed-tests.txt)", "restarted_sessions": False}
    (EVIDENCE / "deployment.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
