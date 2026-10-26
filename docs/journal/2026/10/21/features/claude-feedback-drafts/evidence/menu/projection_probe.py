"""Apply the production Claude projection to a real owned child process."""
import json
import os
import subprocess
import sys

sys.path.insert(0, sys.argv[1])
import ciel_runtime
from ciel_runtime_support.claude_environment import CLAUDE_PROJECTED_ENV_KEYS

config = ciel_runtime.load_config()
projected = ciel_runtime.env_vars(config)
env = os.environ.copy()
for key in CLAUDE_PROJECTED_ENV_KEYS:
    env.pop(key, None)
env.update(projected)
child = subprocess.run(
    [sys.executable, "-I", "-c", "import json,os;print(json.dumps({'CLAUDE_CODE_SEND_FEEDBACK':os.environ.get('CLAUDE_CODE_SEND_FEEDBACK')}))"],
    env=env, capture_output=True, text=True, check=True,
)
print(json.dumps({"stored_setting": config.get("claude_feedback_drafts"),
                  "setting_present": "claude_feedback_drafts" in config,
                  "inherited": os.environ.get("CLAUDE_CODE_SEND_FEEDBACK"),
                  "projected": projected.get("CLAUDE_CODE_SEND_FEEDBACK"),
                  "actual_child": json.loads(child.stdout)}))
