import sys, os, json
print("FAKE_CLAUDE_ARGV=" + json.dumps(sys.argv[1:]), flush=True)
