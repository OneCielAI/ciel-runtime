"""Bind automatic side intake to the verified native session, not a guessed cwd."""
from .work_plan_store import session_work_plan_store
from .work_side_intake import enqueue
from .work_mode_launch import work_mode_enabled


def runtime_intake(load_config, state_dir, session_id, text, event_key):
    config = load_config()
    if not work_mode_enabled(config) or config.get('work_side_intake', {}).get('enabled') is not True:
        return None
    return enqueue(session_work_plan_store(state_dir, session_id), text, event_key)
