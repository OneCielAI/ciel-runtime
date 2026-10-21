"""Run the committed retry acceptance harnesses against an installed runtime root.

The committed harnesses resolve the runtime from their own repository
location: ``protocol_acceptance.py`` assigns ``REPO = HERE.parents[7]`` and
imports ``ciel_runtime`` from it before ``routed_native_e2e.py`` imports the
same module.  This adapter does not edit those files.  It reads the protocol
source, substitutes that single assignment with the absolute installed root,
executes it as ``protocol_acceptance`` in ``sys.modules`` *before* any runtime
import, and only then imports the committed native module.  Both harnesses
therefore run against the installed runtime, and the native module's
production ``RouterHandler`` is the installed one.

Stages:

* protocol: 31 owned-loopback cases (Claude Messages opener, native Responses,
  Chat Completions, real gateway overlay/reselection), same code and isolation
  as the committed 31/31 capture, but with source digests read from the
  installed root, pinned byte-for-byte to the release commit's blobs (and
  LF-normalized against this checkout, which may hold CRLF files);
* native: 6 installed-CLI cases (Claude x2, both installed Codex binaries x2)
  through the real ``RouterHandler`` dispatch, 503-then-success and persistent
  503, with native router-path retries zeroed by the harness;
* SSE: the release ``focused.py --runtime <installed> test_runtime_sse_recovery.py``
  run (28 tests) in a subprocess.

Every stage keeps the existing harness isolation: private HOME/config/cwd/TEMP,
all inherited CIEL_RUNTIME*/provider/credential variables removed, owned
ephemeral loopback ports and fixtures, and installed CLIs pointed at an owned
router.  No live runtime, provider, gateway, remote host or existing session is
contacted, restarted or replayed.

Committed harness sources are read-only inputs.  Evidence is written under
``evidence/installed/`` only; the committed ``routed-native-e2e.json`` and
``protocol-acceptance-*.json`` captures are never touched.

usage:
  python -I installed_verify.py --runtime <absolute installed root> --label <label>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[7]
HARNESS = REPO / "docs/journal/2026/10/21/diagnostics/cielairouter-transient-retry/harness"
PROTOCOL_SOURCE = HARNESS / "protocol_acceptance.py"
FOCUSED = HERE / "focused.py"

REPO_ASSIGNMENT = "REPO = HERE.parents[7]"
PROTOCOL_EXPECTED_CASES = 31
NATIVE_EXPECTED_CASES = 6
SSE_TEST_FILE = "test_runtime_sse_recovery.py"
SSE_EXPECTED_TESTS = 28


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_commit(repo: Path, ref: str) -> str:
    """Resolve the release commit whose blobs the published tarball carries."""

    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"cannot resolve release commit {ref!r}: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def git_blob_sha256(repo: Path, commit: str, name: str) -> str | None:
    """SHA-256 of a committed blob, not of a possibly CRLF working-tree file.

    npm publishes from the CI checkout of the release commit, so tarball bytes
    are the committed blob bytes.  A Windows working tree with
    ``core.autocrlf=true`` can differ from those bytes by line endings alone;
    that is not a release difference.
    """

    completed = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{name}"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
    )
    if completed.returncode != 0:
        return None
    return hashlib.sha256(completed.stdout).hexdigest()


def slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip()).strip("-.")
    return cleaned or "installed"


def first_file(*candidates) -> Path | None:
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return path.resolve()
    return None


def pre_scrub_identity(overrides: dict) -> dict:
    """Resolve host executables before the harness replaces HOME.

    ``routed_native_e2e.py`` resolves its own CLI defaults at import time from
    the harness process's HOME; this adapter imports it after the protocol
    module has already created a private HOME.  The binaries are therefore
    resolved here, before that scrub, and passed explicitly into the committed
    ``case(protocol, binary, persistent)`` entrypoint.
    """

    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    claude = first_file(
        overrides.get("claude"),
        Path.home() / ".local" / "bin" / "claude.exe",
        Path.home() / ".local" / "bin" / "claude",
        shutil.which("claude"),
    )
    codex_vendor = first_file(
        overrides.get("codex"),
        Path(appdata) / "npm" / "node_modules" / "@openai" / "codex"
        / "node_modules" / "@openai" / "codex-win32-x64" / "vendor"
        / "x86_64-pc-windows-msvc" / "bin" / "codex.exe",
        shutil.which("codex"),
    )
    codex_second = first_file(
        overrides.get("codex_second"),
        Path(localappdata) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe",
    )
    return {
        "home_before_scrub": str(Path.home()),
        "claude": claude,
        "codex_vendor": codex_vendor,
        "codex_second": codex_second,
        "inherited_ciel_runtime_keys": sorted(
            key for key in os.environ if key.upper().startswith("CIEL_RUNTIME_")
        ),
        "inherited_provider_keys": sorted(
            key for key in os.environ
            if key.upper().startswith(
                ("CLAUDE", "ANTHROPIC_", "OPENAI_", "CODEX")
            )
        ),
    }


def validate_runtime(runtime: Path) -> dict:
    if not runtime.is_absolute():
        raise SystemExit(f"--runtime must be absolute, got {runtime}")
    if not runtime.is_dir():
        raise SystemExit(f"--runtime is not a directory: {runtime}")
    required = ("ciel_runtime.py", "ciel_runtime_support/transient_retry.py")
    missing = [rel for rel in required if not (runtime / rel).is_file()]
    if missing:
        raise SystemExit(f"installed runtime missing {missing}: {runtime}")
    package = {}
    package_path = runtime / "package.json"
    if package_path.is_file():
        try:
            document = json.loads(package_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SystemExit(f"unreadable package.json in {runtime}: {exc}") from exc
        package = {"name": document.get("name"), "version": document.get("version")}
    constants = runtime / "ciel_runtime_support" / "runtime_constants.py"
    stamped = None
    if constants.is_file():
        match = re.search(
            r'^VERSION = "([^"]+)"$',
            constants.read_text(encoding="utf-8", errors="replace"),
            re.M,
        )
        stamped = match.group(1) if match else None
    return {"package": package, "stamped_version": stamped}


def load_protocol_module(runtime: Path, console) -> tuple[types.ModuleType, dict]:
    """Execute the committed protocol harness with its REPO redirected.

    The substitution happens on the source text before ``exec``; the module's
    own ``_isolate_environment()`` therefore runs before any runtime import and
    its ``sys.path`` entry is the installed root.  Nothing else is changed.
    """

    source = PROTOCOL_SOURCE.read_text(encoding="utf-8")
    replacements = source.count(REPO_ASSIGNMENT)
    if replacements != 1:
        raise SystemExit(
            f"expected exactly one {REPO_ASSIGNMENT!r} in {PROTOCOL_SOURCE}, "
            f"found {replacements}"
        )
    patched = source.replace(
        REPO_ASSIGNMENT, f"REPO = Path({json.dumps(str(runtime))})", 1
    )
    module = types.ModuleType("protocol_acceptance")
    module.__file__ = str(PROTOCOL_SOURCE)
    module.__package__ = ""
    sys.modules["protocol_acceptance"] = module
    try:
        exec(compile(patched, str(PROTOCOL_SOURCE), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop("protocol_acceptance", None)
        raise
    # The committed native module lives beside the protocol module and imports
    # its siblings.  ``python -I`` does not put this directory on sys.path, so
    # add it after the installed runtime entry that the protocol module just
    # created.  The runtime keeps import precedence.
    if str(HARNESS) not in sys.path:
        sys.path.insert(1, str(HARNESS))
    console(f"[adapter] protocol source {PROTOCOL_SOURCE}")
    console(f"[adapter] substituted REPO -> {module.REPO}")
    console(f"[adapter] isolation root {module.ISOLATION['root']}")
    return module, {
        "source": str(PROTOCOL_SOURCE),
        "source_sha256": sha256_text(source),
        "executed_source_sha256": sha256_text(patched),
        "repo_assignment_substitutions": replacements,
        "installed_repo": str(module.REPO),
        "harness_sys_path": str(HARNESS),
        "sys_path_head": [str(entry) for entry in sys.path[:3]],
    }


def module_identity(pa, runtime: Path) -> dict:
    imported_main = Path(pa.ciel_runtime.__file__).resolve()
    expected_main = (runtime / "ciel_runtime.py").resolve()
    transient = sys.modules.get("ciel_runtime_support.transient_retry")
    transient_file = (
        str(Path(transient.__file__).resolve()) if transient is not None else None
    )
    support = {}
    for name in sorted(sys.modules):
        if name != "ciel_runtime_support" and not name.startswith(
            "ciel_runtime_support."
        ):
            continue
        module = sys.modules[name]
        path = getattr(module, "__file__", None)
        if not path:
            continue
        resolved = Path(path).resolve()
        support[name] = {
            "file": str(resolved),
            "under_runtime": resolved.is_relative_to(runtime),
        }
    return {
        "ciel_runtime_file": str(imported_main),
        "expected_ciel_runtime_file": str(expected_main),
        "ciel_runtime_from_installed_root": imported_main == expected_main,
        "ciel_runtime_sha256": sha256_file(imported_main),
        "transient_retry_file": transient_file,
        "all_support_modules_under_installed_root": bool(support)
        and all(item["under_runtime"] for item in support.values()),
        "support_modules": support,
    }


def source_facts(runtime: Path, names) -> tuple[dict, dict, dict]:
    runtime_facts, repo_facts, matches = {}, {}, {}
    for name in names:
        runtime_path, repo_path = runtime / name, REPO / name
        runtime_sha = sha256_file(runtime_path) if runtime_path.is_file() else None
        repo_sha = sha256_file(repo_path) if repo_path.is_file() else None
        runtime_facts[name] = {
            "path": str(runtime_path),
            "exists": runtime_sha is not None,
            "sha256": runtime_sha,
        }
        repo_facts[name] = {
            "path": str(repo_path),
            "exists": repo_sha is not None,
            "sha256": repo_sha,
        }
        # Registry builds use git's LF bytes; the Windows checkout may have
        # CRLF. Keep raw identities above and report this normalization rather
        # than misclassifying newline-only differences as a different build.
        runtime_normalized = hashlib.sha256(runtime_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() if runtime_sha else None
        repo_normalized = hashlib.sha256(repo_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() if repo_sha else None
        runtime_facts[name]["lf_sha256"] = runtime_normalized
        repo_facts[name]["lf_sha256"] = repo_normalized
        matches[name] = bool(runtime_sha and runtime_normalized == repo_normalized)
    return runtime_facts, repo_facts, matches


def release_commit_facts(runtime: Path, names, commit: str) -> tuple[dict, dict]:
    """Installed digests vs the exact release commit's committed blobs.

    npm publishes the CI checkout bytes of the commit that produced the
    tarball.  ``source_facts`` above tolerates CRLF-only working-tree
    differences; this exact check is recorded separately and both must pass.
    """

    facts, matches = {}, {}
    for name in names:
        runtime_path = runtime / name
        runtime_sha = sha256_file(runtime_path) if runtime_path.is_file() else None
        commit_sha = git_blob_sha256(REPO, commit, name)
        facts[name] = {
            "path": str(runtime_path),
            "installed_sha256": runtime_sha,
            "commit": commit,
            "commit_sha256": commit_sha,
            "match": bool(runtime_sha and runtime_sha == commit_sha),
        }
        matches[name] = facts[name]["match"]
    return facts, matches


def run_protocol(pa, runtime: Path, out_dir: Path, label: str, console) -> dict:
    console(f"[protocol] {len(pa.CASES)} cases against {runtime}")
    started = time.monotonic()
    results = []
    for name, run in pa.CASES:
        result = pa.case(name, run)
        results.append(result)
        console(
            f"[protocol] {name:<48} "
            f"{'PASS' if result['passed'] else 'FAIL'} {result['elapsed_seconds']:>6.2f}s"
        )
    payload = {
        "label": label,
        "generated_at": iso(time.time()),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "isolation": pa.ISOLATION,
        "runtime": str(runtime),
        "production_wiring": {
            "claude_messages_opener": (
                "ciel_runtime.open_provider_request_with_key_retry via "
                "upstream_retry_context()"
            ),
            "responses": "ciel_runtime.provider_responses_passthrough()",
            "chat": "ciel_runtime.provider_chat_passthrough()",
            "gateway": (
                "CielAiRouterGatewayService + CielAiRouterGatewayOverlay + "
                "provider_network.provider_urlopen + reselect_cielairouter_gateway"
            ),
        },
        "not_covered": [
            "router_http request dispatch wrapping (owned by dispatch-scope)",
            "live runtime, real provider and remote gateway hosts",
        ],
        "cases": results,
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "all_passed": all(item["passed"] for item in results),
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    path = out_dir / f"protocol-acceptance-{label}.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    console(f"[protocol] PASS {payload['passed']}/{len(results)}  FAIL {payload['failed']}")
    return {"payload": payload, "path": path}


def run_native(rne, binaries, pa, out_dir: Path, label: str, console) -> dict:
    identities = []
    for protocol, binary in binaries:
        completed = subprocess.run(
            [str(binary), "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )
        identities.append(
            {
                "protocol": protocol,
                "path": str(binary),
                "version": (completed.stdout or "").strip(),
                "returncode": completed.returncode,
                "sha256": sha256_file(binary),
            }
        )
    console(f"[native] {len(identities)} installed CLI binaries")
    results = []
    for protocol, binary in binaries:
        for persistent in (False, True):
            result = rne.case(protocol, binary, persistent)
            results.append(result)
            statuses = [hit["status"] for hit in result["upstream_hits"]]
            console(
                f"[native] {result['name']:<26} {str(binary):<72} "
                f"{'PASS' if result['passed'] else 'FAIL'} "
                f"cli_requests={len(result['ingress'])} "
                f"upstream_hits={len(result['upstream_hits'])} "
                f"statuses={statuses} "
                f"successes={result['cli']['success_output_count']} "
                f"rc={result['cli']['returncode']}"
            )
    source_paths = [
        "ciel_runtime.py",
        "ciel_runtime_support/router_http.py",
        "ciel_runtime_support/transient_retry.py",
        "ciel_runtime_support/upstream_retry.py",
        "ciel_runtime_support/provider_responses_passthrough.py",
        "ciel_runtime_support/claude_router.py",
    ]
    sources = {
        path: sha256_file(pa.REPO / path)
        for path in source_paths
        if (pa.REPO / path).is_file()
    }
    payload = {
        "label": label,
        "generated_at": iso(time.time()),
        "runtime": str(pa.REPO),
        "cli_identities": identities,
        "production_dispatch": (
            "RouterHandler -> build_router_http_services -> "
            "RouterHttpHandler.do_POST -> _dispatch_with_failover -> route_runtime_post"
        ),
        "lifecycle": (
            "Only ThreadingHTTPServer. No run_cli/serve/scheduler/receiver/backup/"
            "lifetime-watchdog start."
        ),
        "isolation": pa.ISOLATION,
        "sources": sources,
        "cases": results,
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "all_passed": all(item["passed"] for item in results),
        "limitations": [
            "Owned loopback fixtures, not live provider infrastructure.",
            "Codex still advertises request_user_input/apply_patch; fixture emits no "
            "tool call and zero execution items were observed.",
            "No process-wide network capture; provider HTTP sequence is recorded at "
            "owned ingress/upstream sockets.",
            "Upstream statuses observed at fixture send_response; raw request bodies "
            "and native transcripts are not persisted.",
        ],
    }
    path = out_dir / f"routed-native-e2e-{label}.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    console(f"[native] PASS {payload['passed']}/{len(results)}  FAIL {payload['failed']}")
    return {"payload": payload, "path": path}


def run_sse_tests(runtime: Path, pa, out_dir: Path, label: str, console) -> dict:
    command = [
        sys.executable,
        "-I",
        str(FOCUSED),
        "--runtime",
        str(runtime),
        SSE_TEST_FILE,
    ]
    console(f"[sse] {' '.join(command)}")
    completed = subprocess.run(
        command,
        cwd=str(pa.ISOLATION["cwd"]),
        env=dict(os.environ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    output = completed.stdout or ""
    ran = re.search(r"^Ran (\d+) tests? in ", output, re.M)
    ok = re.search(r"^OK(?: \(.*\))?$", output, re.M)
    tests_run = int(ran.group(1)) if ran else None
    text = (
        f"command: {' '.join(command)}\n"
        f"runtime: {runtime}\n"
        f"exit_code: {completed.returncode}\n"
        f"--- output ---\n{output}"
    )
    path = out_dir / f"focused-sse-{label}.txt"
    path.write_text(text, encoding="utf-8")
    console(
        f"[sse] exit={completed.returncode} ran={tests_run} "
        f"ok={bool(ok)} evidence={path}"
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "tests_run": tests_run,
        "ok": bool(ok) and completed.returncode == 0,
        "expected_tests": SSE_EXPECTED_TESTS,
        "passed": bool(ok)
        and completed.returncode == 0
        and tests_run == SSE_EXPECTED_TESTS,
        "path": path,
        "output_tail": output.strip().splitlines()[-12:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True,
                        help="absolute installed runtime root")
    parser.add_argument("--label", default="",
                        help="evidence label; defaults to installed-<version>")
    parser.add_argument("--out-dir", default=str(HERE / "installed"),
                        help="evidence output directory (default: evidence/installed)")
    parser.add_argument("--expect-version", default="",
                        help="assert the installed stamped/package version matches")
    parser.add_argument("--expect-commit", default="HEAD",
                        help="release commit whose blobs the installed sources must "
                             "match byte-for-byte (default: HEAD)")
    parser.add_argument("--claude", default="", help="Claude CLI override")
    parser.add_argument("--codex", default="", help="primary Codex CLI override")
    parser.add_argument("--codex-second", default="", help="second Codex CLI override")
    parser.add_argument("--skip-protocol", action="store_true")
    parser.add_argument("--skip-native", action="store_true")
    parser.add_argument("--skip-sse-tests", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    runtime = Path(args.runtime).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    runtime_facts = validate_runtime(runtime)
    version = (
        runtime_facts["stamped_version"]
        or runtime_facts["package"].get("version")
        or "unknown"
    )
    label = slug(args.label or f"installed-{version}")
    release_commit = resolve_commit(REPO, args.expect_commit)

    binaries = []
    if not args.skip_native:
        identity = pre_scrub_identity(
            {
                "claude": args.claude,
                "codex": args.codex,
                "codex_second": args.codex_second,
            }
        )
        missing = [
            key for key in ("claude", "codex_vendor", "codex_second")
            if identity.get(key) is None
        ]
        if missing:
            raise SystemExit(
                f"required installed CLI binaries not found: {missing}; "
                f"resolved={identity}"
            )
        binaries = [
            ("claude", identity["claude"]),
            ("codex", identity["codex_vendor"]),
            ("codex", identity["codex_second"]),
        ]
    else:
        identity = pre_scrub_identity({})

    if args.expect_version:
        installed_versions = {
            runtime_facts["stamped_version"],
            runtime_facts["package"].get("version"),
        }
        if args.expect_version not in installed_versions:
            raise SystemExit(
                f"installed version {args.expect_version!r} not found; "
                f"runtime has {sorted(v for v in installed_versions if v)}"
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    planned = [
        out_dir / f"installed-verify-{label}.json",
        out_dir / f"protocol-acceptance-{label}.json",
        out_dir / f"routed-native-e2e-{label}.json",
        out_dir / f"focused-sse-{label}.txt",
    ]
    if not args.overwrite:
        existing = [str(path) for path in planned if path.exists()]
        if existing:
            raise SystemExit(
                f"refusing to overwrite existing evidence: {existing}; "
                "pass --overwrite or a new --label"
            )

    console_lines: list[str] = []

    def console(line: str) -> None:
        print(line, flush=True)
        console_lines.append(line)

    console(f"[adapter] runtime {runtime}")
    console(f"[adapter] package {runtime_facts['package']} stamped={runtime_facts['stamped_version']}")
    console(f"[adapter] output {out_dir} label={label}")
    console(f"[adapter] expected release commit {release_commit}")

    stage_errors: list[str] = []
    protocol_result = native_result = sse_result = None

    started = time.monotonic()
    pa, loader_facts = load_protocol_module(runtime, console)
    runtime_digest_before, repo_digests, source_matches = source_facts(runtime, pa.SOURCES)
    release_facts, release_matches = release_commit_facts(runtime, pa.SOURCES, release_commit)
    identity_facts = module_identity(pa, runtime)
    console(
        f"[adapter] ciel_runtime {identity_facts['ciel_runtime_file']} "
        f"from_installed={identity_facts['ciel_runtime_from_installed_root']}"
    )

    checks = {
        "runtime_layout_present": True,
        "protocol_repo_assignment_substituted_once": (
            loader_facts["repo_assignment_substitutions"] == 1
            and Path(pa.REPO).resolve() == runtime
        ),
        "imported_ciel_runtime_from_installed_root": (
            identity_facts["ciel_runtime_from_installed_root"]
        ),
        "imported_ciel_runtime_digest_matches_installed_source": (
            identity_facts["ciel_runtime_sha256"]
            == runtime_digest_before.get("ciel_runtime.py", {}).get("sha256")
        ),
        "all_support_modules_under_installed_root": (
            identity_facts["all_support_modules_under_installed_root"]
        ),
        "all_sources_present_in_installed_runtime": all(
            item["exists"] for item in runtime_digest_before.values()
        ),
        "source_digests_match_repo_checkout": all(source_matches.values()),
        "source_digests_match_release_commit_exactly": all(release_matches.values()),
        "expected_version_matches": (
            not args.expect_version
            or args.expect_version in {
                runtime_facts["stamped_version"],
                runtime_facts["package"].get("version"),
            }
        ),
    }

    try:
        if not args.skip_protocol:
            protocol_result = run_protocol(pa, runtime, out_dir, label, console)
            checks["protocol_31_of_31"] = (
                protocol_result["payload"]["passed"] == PROTOCOL_EXPECTED_CASES
                and protocol_result["payload"]["failed"] == 0
            )
    except BaseException as exc:  # noqa: BLE001 - keep gathering other stages
        stage_errors.append(f"protocol: {type(exc).__name__}: {exc}")
        console(f"[protocol] ERROR {type(exc).__name__}: {exc}")
        checks["protocol_31_of_31"] = False

    checks["native_module_uses_injected_protocol_module"] = False
    try:
        if not args.skip_native:
            import routed_native_e2e as rne  # noqa: PLC0415 - after injection
            console(
                f"[adapter] routed_native_e2e import from {Path(rne.__file__).resolve()}"
            )
            same_module = rne.ciel_runtime is pa.ciel_runtime
            checks["native_module_uses_injected_protocol_module"] = (
                Path(rne.pa.__file__).resolve() == PROTOCOL_SOURCE.resolve()
                and same_module
            )
            identity_facts["routed_native_ciel_runtime_file"] = str(
                Path(rne.ciel_runtime.__file__).resolve()
            )
            identity_facts["routed_native_shares_ciel_runtime_module"] = same_module
            native_result = run_native(rne, binaries, pa, out_dir, label, console)
            checks["native_6_of_6"] = (
                native_result["payload"]["passed"] == NATIVE_EXPECTED_CASES
                and native_result["payload"]["failed"] == 0
            )
    except BaseException as exc:  # noqa: BLE001
        stage_errors.append(f"native: {type(exc).__name__}: {exc}")
        console(f"[native] ERROR {type(exc).__name__}: {exc}")
        checks["native_6_of_6"] = False

    try:
        if not args.skip_sse_tests:
            sse_result = run_sse_tests(runtime, pa, out_dir, label, console)
            checks["sse_28_tests_ok"] = sse_result["passed"]
    except BaseException as exc:  # noqa: BLE001
        stage_errors.append(f"sse: {type(exc).__name__}: {exc}")
        console(f"[sse] ERROR {type(exc).__name__}: {exc}")
        checks["sse_28_tests_ok"] = False

    runtime_digest_after, _, _ = source_facts(runtime, pa.SOURCES)
    checks["sources_stable_during_run"] = runtime_digest_before == runtime_digest_after
    checks["all_stages_run"] = not (
        args.skip_protocol or args.skip_native or args.skip_sse_tests
    )

    all_passed = all(value for value in checks.values()) and not stage_errors
    elapsed = round(time.monotonic() - started, 2)
    payload = {
        "label": label,
        "generated_at": iso(time.time()),
        "elapsed_seconds": elapsed,
        "runtime": {
            "root": str(runtime),
            "package": runtime_facts["package"],
            "stamped_version": runtime_facts["stamped_version"],
            "expected_version": args.expect_version or None,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "protocol_module": loader_facts,
        "module_identity": identity_facts,
        "sources": {
            name: {
                "runtime": runtime_digest_before[name],
                "repo": repo_digests[name],
                "match": source_matches[name],
                "release_commit": release_facts[name],
                "release_match": release_matches[name],
            }
            for name in runtime_digest_before
        },
        "release_commit": release_commit,
        "source_digests_stable_during_run": runtime_digest_before == runtime_digest_after,
        "environment_isolation": {
            "harness_isolation": pa.ISOLATION,
            "inherited_ciel_runtime_keys_removed": identity[
                "inherited_ciel_runtime_keys"
            ],
            "inherited_provider_keys_removed": identity["inherited_provider_keys"],
            "control": (
                "protocol_acceptance._isolate_environment before runtime import; "
                "native CLIs get private HOME/CODEX_HOME/cwd and only loopback URLs"
            ),
        },
        "stages": {
            "protocol": (
                {
                    "evidence": str(protocol_result["path"]),
                    "cases": len(protocol_result["payload"]["cases"]),
                    "passed": protocol_result["payload"]["passed"],
                    "failed": protocol_result["payload"]["failed"],
                    "all_passed": protocol_result["payload"]["all_passed"],
                }
                if protocol_result else {"skipped": True}
            ),
            "native": (
                {
                    "evidence": str(native_result["path"]),
                    "cases": len(native_result["payload"]["cases"]),
                    "passed": native_result["payload"]["passed"],
                    "failed": native_result["payload"]["failed"],
                    "all_passed": native_result["payload"]["all_passed"],
                    "counts": [
                        {
                            "name": item["name"],
                            "binary": item["binary"],
                            "cli_requests": len(item["ingress"]),
                            "upstream_hits": len(item["upstream_hits"]),
                            "upstream_statuses": [
                                hit["status"] for hit in item["upstream_hits"]
                            ],
                            "successes": item["cli"]["success_output_count"],
                            "returncode": item["cli"]["returncode"],
                        }
                        for item in native_result["payload"]["cases"]
                    ],
                }
                if native_result else {"skipped": True}
            ),
            "sse_tests": (
                {
                    "evidence": str(sse_result["path"]),
                    "command": sse_result["command"],
                    "exit_code": sse_result["exit_code"],
                    "tests_run": sse_result["tests_run"],
                    "expected_tests": sse_result["expected_tests"],
                    "ok": sse_result["ok"],
                    "output_tail": sse_result["output_tail"],
                }
                if sse_result else {"skipped": True}
            ),
        },
        "checks": checks,
        "stage_errors": stage_errors,
        "all_passed": all_passed,
    }
    aggregate = out_dir / f"installed-verify-{label}.json"
    aggregate.write_text(json.dumps(payload, indent=2, default=str) + "\n",
                         encoding="utf-8")
    console(f"[adapter] checks {json.dumps(checks)}")
    console(f"[adapter] all_passed={all_passed} evidence={aggregate}")
    transcript = out_dir / f"installed-verify-{label}.txt"
    transcript.write_text("\n".join(console_lines) + "\n", encoding="utf-8")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
