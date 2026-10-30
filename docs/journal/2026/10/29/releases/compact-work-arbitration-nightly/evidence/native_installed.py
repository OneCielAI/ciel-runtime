"""Run installed native mod tests with an owned HOME and no provider environment."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
OUT = Path(__file__).resolve().parent
snapshot = Path("C:/Users/djlov/.local/share/ciel-runtime-037c55fc252641c41d8058db66fa055e9f43dfdf")
mod = snapshot / "ciel_runtime_support/claude_mods/ciel-work"
assert (mod / "tests/work.test.ts").is_file()
env = dict(os.environ)
for key in list(env):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        del env[key]
with tempfile.TemporaryDirectory(prefix="ciel-native-installed-") as scratch:
    env.update(HOME=scratch, USERPROFILE=scratch, CLAUDE_CONFIG_DIR=scratch, DISABLE_TELEMETRY="1", DISABLE_AUTOUPDATER="1")
    proc = subprocess.run(["C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe", "plugin", "test", str(mod)], env=env, cwd=scratch, capture_output=True, encoding="utf-8", errors="replace", timeout=300)
(OUT / "native-installed-tests.txt").write_text(proc.stdout + proc.stderr, encoding="utf-8")
(OUT / "native-installed-tests.json").write_text(json.dumps({"exit_code": proc.returncode, "mod": str(mod)}, indent=2), encoding="utf-8")
print(proc.stdout + proc.stderr)
print("NATIVE TEST EXIT", proc.returncode)
raise SystemExit(proc.returncode)
