"""Compare installed/current detectors against an isolated Claude draft (no submit)."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ciel_runtime_support.windows_conpty import WindowsConPtySession


def main():
    path = Path.home() / ".local/share/ciel-runtime/ciel_runtime_support/windows_conpty.py"
    spec = importlib.util.spec_from_file_location("ciel_runtime_support._installed_conpty_probe", path)
    installed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installed)
    session = WindowsConPtySession([shutil.which("claude")], dict(os.environ), log=lambda *_: None, mirror_output=False, forward_stdin=False)
    try:
        time.sleep(8)
        checkpoint = session.prompt_readiness_checkpoint()
        prompt = "한글 입력 검증 메시지입니다. 원문 그대로 표시합니다."
        session.write(b"\x15\x1b[200~" + prompt.encode() + b"\x1b[201~")
        time.sleep(3)
        output, _ = session._output_since(checkpoint)
        result = {
            "draft_only_no_submit": True,
            "output_bytes": len(output),
            "installed_detected": installed.WindowsConPtySession._prompt_rendered_in_output(output, prompt),
            "source_detected": WindowsConPtySession._prompt_rendered_in_output(output, prompt),
            "output": output.decode("utf-8", errors="replace"),
        }
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result["source_detected"] else 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
