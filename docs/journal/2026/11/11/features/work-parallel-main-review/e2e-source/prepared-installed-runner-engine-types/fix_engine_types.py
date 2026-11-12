"""Installed runner a2faa191 -> engine-types classification against the INSTALLED manifest.
A path under ENGINE_TYPES that is absent from the INSTALLED manifest is engine-generated
(Claude Code writes .claude-plugin/types/, its tsconfig.json and .gitignore at plugin load).
A path that exists in INSTALLED and changes stays a product change."""
import hashlib
import sys
from pathlib import Path

p = Path(sys.argv[1])
raw = p.read_bytes()
assert hashlib.sha256(raw).hexdigest().startswith("a2faa191"), "installed runner is not a2faa191"
s = raw.decode("utf-8")


def rep(a, b):
    global s
    assert s.count(a) == 1, (s.count(a), a[:100])
    s = s.replace(a, b)


rep('''def engine_generated(rel: str) -> bool:
    """Claude Code writes these git-ignored plugin types at load (debug: wrote .claude-plugin/types/...)."""
    return rel.startswith(ENGINE_TYPES) and rel not in (ENGINE_TYPES + ".gitignore", ENGINE_TYPES + "tsconfig.json")''',
'''def engine_generated(rel: str, installed: dict | None = None) -> bool:
    """Claude Code writes these git-ignored plugin types at load (debug: wrote .claude-plugin/types/...).

    With the INSTALLED manifest: a path under ENGINE_TYPES (including .gitignore and
    tsconfig.json, which the engine also writes) is engine-generated only when the
    installed package does not contain it; a path that exists in INSTALLED and
    changes is a product change (run-20261112-044325 classified the engine-written
    .gitignore/tsconfig.json as product)."""
    if installed is not None:
        return rel.startswith(ENGINE_TYPES) and rel not in installed
    return rel.startswith(ENGINE_TYPES) and rel not in (ENGINE_TYPES + ".gitignore", ENGINE_TYPES + "tsconfig.json")''')

rep('''    engine_changes = [k for k in changed_snapshot if engine_generated(k)]
    product_changes = [k for k in changed_snapshot if not engine_generated(k)]''',
'''    installed_manifest = json.loads((OUT / "installed-manifest.json").read_text(encoding="utf-8"))
    engine_changes = [k for k in changed_snapshot if engine_generated(k, installed_manifest)]
    product_changes = [k for k in changed_snapshot if not engine_generated(k, installed_manifest)]''')

p.write_bytes(s.encode("utf-8"))
print("written", hashlib.sha256(s.encode("utf-8")).hexdigest())
