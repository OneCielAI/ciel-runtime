"""``ciel-runtimectl work``: settings through the menu's projection, plus the
Work/SSE read views and operator actions of the web console (work_ctl).

Errors never surface as tracebacks: one line (or one JSON object with
``--json``) and a non-zero exit. Exit 3 means a confirmation is required and
nothing was changed.
"""
import json
import sys

from .work_settings_menu import apply, apply_feedback, description, shown_value
from .work_live_settings import known_keys, settings

SETTINGS_ACTIONS = ('status', 'list', 'get', 'set')


def _settings_result(config, action, key, value, save_config, *, load_config=None, expected=None):
    feedback = None
    if action in ('get', 'set') and not key:
        raise ValueError('Work setting key required')
    if key and key not in known_keys():
        # Validate before anything is written (a discovery key once saved and
        # then reported "unknown" while the ledger was unreadable).
        raise ValueError('unknown Work setting: ' + key)
    if action == 'set':
        if value is None:
            raise ValueError('use work set KEY VALUE')
        # Compare-and-set against what this command read (or the fingerprint a
        # script read earlier with `work status --json`): a change made
        # elsewhere in between is refused, never overwritten.
        from .work_live_settings import settings_fingerprint
        pin = expected if expected is not None else settings_fingerprint(config)
        apply(config, key, value, save_config, expected_fingerprint=pin, load=load_config or (lambda: config))
        feedback = apply_feedback(config, key, value)
    state = settings(config, {}, None)
    rows = [row for row in state['settings'] if not key or row['key'] == key]
    result = {'settings': [{**row, 'description': description(config, row['key'], row['value'])} for row in rows],
              'settings_fingerprint': state['settings_fingerprint'], 'applies': state['applies']}
    if feedback is not None:
        result['feedback'] = feedback
    return result


def command(load_config, save_config, output=print, error_output=None):
    def run(args):
        try:
            return _run(args)
        except Exception as error:  # noqa: BLE001 - every failure becomes one line
            from .work_ctl import CtlError
            from .work_live_settings import SettingsConflict
            code = error.code if isinstance(error, CtlError) else 'settings_conflict' if isinstance(
                error, SettingsConflict) else ('invalid_request' if isinstance(error, ValueError)
                                               else type(error).__name__)
            exit_code = error.exit_code if isinstance(error, CtlError) else 1
            message = str(error) or type(error).__name__
            if getattr(args, 'json', False):
                body = {'ok': False, 'error': code, 'message': message}
                if isinstance(error, CtlError) and error.payload:
                    body.update({k: v for k, v in error.payload.items() if k not in ('ok', 'error', 'message')})
                output(json.dumps(body, ensure_ascii=False))
            else:
                (error_output or (lambda text: print(text, file=sys.stderr)))('work: ' + message.splitlines()[0])
                if isinstance(error, CtlError) and error.payload.get('would'):
                    output('would send: ' + json.dumps(error.payload['would'], ensure_ascii=False, sort_keys=True))
            raise SystemExit(exit_code) from None

    def _run(args):
        action = getattr(args, 'action', None) or 'status'
        if action in SETTINGS_ACTIONS:
            return _settings(args, action)
        from .work_ctl import READS, WorkCtl, state_dir, text_lines
        ctl = WorkCtl(load_config, save_config, state_dir())
        if action == 'sessions':
            result = {'ok': True, 'sessions': ctl.sessions()}
        else:
            session = ctl.require_session(getattr(args, 'session', None))
            result = (ctl.view(action, session, args.key) if action in READS
                      else ctl.act(action, session, args))
            result = {'ok': True, **result} if 'ok' not in result else result
        if args.json:
            output(json.dumps(result, ensure_ascii=False))
        else:
            for line in text_lines(action, result):
                output(line)
        return result

    def _settings(args, action):
        config = load_config()
        result = _settings_result(config, action, args.key, args.value, save_config, load_config=load_config,
                                  expected=getattr(args, 'expected_settings_fingerprint', None))
        if action == 'status':
            from .work_ctl import WorkCtl, state_dir
            ctl = WorkCtl(load_config, save_config, state_dir())
            result['operational'] = ctl.operational(getattr(args, 'session', None))
        if args.json:
            output(json.dumps(result, ensure_ascii=False))
            return result
        for row in result['settings']:
            output(row['key'] + ' = ' + shown_value(config, row) + (' [' + row['status'] + ']' if row.get('status') else ''))
            output('  ' + row['description'])
        if result.get('feedback') is not None:
            output(result['feedback'])
        output(result['applies'])
        if 'operational' in result:
            # G5: the server's fork_gate message, verbatim, once, before the block.
            for line in result['operational'].get('warnings') or []:
                output('warning: ' + line)
            output('operational: ' + json.dumps({key: value for key, value in result['operational'].items()
                                                 if key != 'warnings'}, ensure_ascii=False, sort_keys=True))
        return result
    return run
