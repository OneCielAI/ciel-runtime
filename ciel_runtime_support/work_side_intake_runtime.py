"""Stage automatic side mail for the exact native session; the mod consumes it."""


def runtime_intake(load_config, state_dir, session_id, text, event_key, route=None):
    config = load_config()
    if config.get('work_side_intake', {}).get('enabled') is not True:
        return None
    from .side_intake_service import receive
    # Socket admission acknowledges durable receipt only. It must not mutate
    # the Work board behind the mod or depend on Work execution being enabled.
    return receive(state_dir, session_id, text, event_key, admit=False, route=route)
