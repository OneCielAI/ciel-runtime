"""Stand-in npm for the self-update E2E: reports a newer release, accepts the
install without changing anything, and records every invocation."""
import json, os, sys

args = sys.argv[1:]
with open(os.environ['FAKE_NPM_LOG'], 'a', encoding='utf-8') as log:
    log.write(json.dumps(args) + '\n')
if args[:1] == ['view'] and args[-1:] == ['version']:
    print('9.9.9')
    raise SystemExit(0)
if 'install' in args:
    print('fake npm install: no files changed')
    raise SystemExit(0)
raise SystemExit(1)
