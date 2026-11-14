import ast, sys
from pathlib import Path
support = Path(sys.argv[1]) / "ciel_runtime_support"
mods = {}
for p in support.rglob("*.py"):
    rel = p.relative_to(support.parent).with_suffix("")
    parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
    mods[p] = ".".join(parts)
names = set(mods.values()); g = {m: set() for m in names}; where = {}
for p, m in mods.items():
    pkg = m if p.name == "__init__.py" else m.rsplit(".", 1)[0]
    for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name in names: g[m].add(a.name); where.setdefault((m, a.name), n.lineno)
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                pp = pkg.split("."); par = ".".join(pp[:len(pp)-n.level+1]); t = ".".join(x for x in (par, n.module or "") if x)
            else: t = n.module or ""
            if t in names: g[m].add(t); where.setdefault((m, t), n.lineno)
            for a in n.names:
                c = f"{t}.{a.name}" if t else a.name
                if c in names: g[m].add(c); where.setdefault((m, c), n.lineno)
idx = {}; low = {}; st = []; on = set(); out = []; i = [0]
sys.setrecursionlimit(10000)
def sc(v):
    idx[v] = low[v] = i[0]; i[0] += 1; st.append(v); on.add(v)
    for w in g[v]:
        if w not in idx: sc(w); low[v] = min(low[v], low[w])
        elif w in on: low[v] = min(low[v], idx[w])
    if low[v] == idx[v]:
        comp = []
        while True:
            w = st.pop(); on.discard(w); comp.append(w)
            if w == v: break
        if len(comp) > 1: out.append(comp)
for v in g:
    if v not in idx: sc(v)
for comp in out:
    s = set(comp); print("SCC:", sorted(x.split(".")[-1] for x in comp))
    for a in sorted(s):
        for b in sorted(g[a] & s): print(f"   {a.split('.')[-1]} -> {b.split('.')[-1]} (line {where[(a,b)]})")
print("sccs", len(out))
