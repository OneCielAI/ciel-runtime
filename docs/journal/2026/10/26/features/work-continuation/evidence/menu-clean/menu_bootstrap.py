"""Run only the production prelaunch menu with caller-isolated paths."""
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import ciel_runtime

Path(sys.argv[2], "menu.pid").write_text(str(os.getpid()), encoding="utf-8")
result = ciel_runtime.portable_prelaunch_menu([])
Path(sys.argv[2], "menu-exit.txt").write_text(str(result), encoding="utf-8")
