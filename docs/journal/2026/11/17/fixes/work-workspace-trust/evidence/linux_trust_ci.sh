#!/bin/bash
# Reproduce the GitHub runner's system gitconfig (safe.directory=*) and run the trust tests.
apt-get update -qq >/dev/null && apt-get install -y -qq git >/dev/null 2>&1
printf '[safe]\n\tdirectory = *\n' >> /etc/gitconfig
mkdir /work && cd /src && tar --exclude=./.git --exclude=./.local --exclude=./node_modules -cf - . | (cd /work && tar xf -)
cd /work && git --version && git config --system --get safe.directory
export CIEL_RUNTIME_TEST_ISOLATED=1 CIEL_RUNTIME_CONFIG_DIR=$(mktemp -d)
if [ -n "$1" ]; then cp "/src/$1" tests/test_work_parallel_workspace_trust.py; fi
python -m unittest tests.test_work_parallel_workspace_trust 2>&1 | grep -E "^(FAIL|ERROR):|^Ran|^OK|^FAILED"
