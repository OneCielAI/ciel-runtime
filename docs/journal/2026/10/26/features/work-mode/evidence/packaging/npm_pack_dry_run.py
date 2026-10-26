"""npm pack --dry-run in a scrubbed environment; records the claude_mods entries."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

here = Path(__file__).resolve().parent
root = here.parents[8]
assert (root / "package.json").is_file(), root
env = {k: v for k, v in os.environ.items()
       if not k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI", "NPM_CONFIG_"))}
with tempfile.TemporaryDirectory(prefix="ciel-work-pack-") as temp:
    home = Path(temp)
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        env[name] = str(home)
    (home / "user.npmrc").write_text("", encoding="utf-8")
    (home / "global.npmrc").write_text("", encoding="utf-8")
    env.update(npm_config_cache=str(home / "cache"), npm_config_userconfig=str(home / "user.npmrc"),
               npm_config_globalconfig=str(home / "global.npmrc"), npm_config_update_notifier="false",
               npm_config_fund="false", npm_config_audit="false")
    npm = shutil.which("npm", path=env.get("PATH"))
    cmd = [npm, "pack", "--dry-run", "--json", "--ignore-scripts"]
    proc = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, encoding="utf-8")
(here / "npm-pack-dry-run.json").write_text(proc.stdout, encoding="utf-8")
(here / "npm-pack-dry-run.stderr.txt").write_text(proc.stderr, encoding="utf-8")
lines = [f"command: npm pack --dry-run --json --ignore-scripts (cwd={root.name}, scrubbed env, temp HOME/cache/user+global npmrc)",
         f"exit: {proc.returncode}"]
if proc.returncode == 0:
    pkg = json.loads(proc.stdout)[0]
    paths = sorted(f["path"] for f in pkg["files"])
    mods = [p for p in paths if p.startswith("ciel_runtime_support/claude_mods/")]
    lines += [f"package: {pkg['name']}@{pkg['version']} entryCount={pkg['entryCount']}",
              f"claude_mods entries ({len(mods)}):", *[f"  {p}" for p in mods]]
    for need in (".claude-plugin/plugin.json", "hooks/hooks.json", "hooks/register.js"):
        p = "ciel_runtime_support/claude_mods/ciel-work/" + need
        lines.append(f"required {p}: {'present' if p in paths else 'MISSING'}")
(here / "npm-pack-dry-run-summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
sys.exit(proc.returncode)
