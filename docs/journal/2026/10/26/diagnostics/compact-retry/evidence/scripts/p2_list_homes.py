# Read-only: list sandbox homes and top-level config dir names (no recursion).
import os, glob, time
def T(x): return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(x))
for home in sorted(glob.glob(r"C:\ProgramData\aap-win\sandboxes\u*\home\*")):
    names = []
    try: names = os.listdir(home)
    except Exception as e: print("HOME_ERR", home, type(e).__name__); continue
    print("HOME", home, [n for n in names if n.startswith(".")][:30])
    pj = os.path.join(home, ".claude", "projects")
    if os.path.isdir(pj):
        for d in os.listdir(pj):
            print("   PROJ", d, T(os.path.getmtime(os.path.join(pj, d))))
