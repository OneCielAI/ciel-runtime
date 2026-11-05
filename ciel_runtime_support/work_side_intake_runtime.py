"""Bind automatic side intake to the verified native session, not a guessed cwd."""
from .work_mode_launch import work_mode_enabled


def runtime_intake(load_config, state_dir, session_id, text, event_key):
    config = load_config()
    if config.get('work_side_intake', {}).get('enabled') is not True:
        return None
    from .side_intake_service import receive
    from .work_plan_tree import WorkPlanCapacityError
    receipt = receive(state_dir, session_id, text, event_key, admit=work_mode_enabled(config))
    if receipt.get('board_pending') and not receipt.get('board_error'):
        # Keep the user's interim ordinary-message fallback while retaining
        # the independent receipt for subsequent ACK/triage implementation.
        raise WorkPlanCapacityError('side inbox received; Work board admission pending')
    return receipt
