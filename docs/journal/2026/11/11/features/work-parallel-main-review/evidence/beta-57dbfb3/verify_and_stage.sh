#!/usr/bin/env bash
# Verify the published beta for aafbe1c and stage an immutable snapshot.
# Read-only against the registry; writes only under $WORK and the new snapshot dir.
set -euo pipefail
SHA=57dbfb3ac9c8d9f1a42e89a038d27e16f4fab409
REPO=/c/Users/djlov/ciel-runtime/.local/tmp/work-parallel
WORK=/c/Users/djlov/ciel-runtime/.local/tmp/beta-install-aafbe1c
PKG=@one-ciel-ai/ciel-runtime
SNAPSHOT="/c/Users/djlov/.local/share/ciel-runtime-$SHA"
cd "$WORK"
npm view "$PKG" dist-tags --json > dist-tags.json
VERSION=$(node -p "require('./dist-tags.json').beta")
node -e "const t=require('./dist-tags.json');if(t.latest!=='0.2.51'||t.nightly!=='0.2.51-nightly.20261106-020333.c00c8b6'){console.error('latest/nightly moved',t);process.exit(1)}"
case "$VERSION" in *".${SHA:0:7}") ;; *) echo "beta tag $VERSION is not $SHA" >&2; exit 1;; esac
npm view "$PKG@$VERSION" gitHead dist.integrity dist.tarball --json > view.json
node -e "const v=require('./view.json');if(v.gitHead!=='$SHA'){console.error('gitHead',v.gitHead);process.exit(1)}"
rm -rf tgz && mkdir tgz
(cd tgz && npm pack "$PKG@$VERSION" --silent > ../tgz-name.txt)
TGZ="$WORK/tgz/$(cat tgz-name.txt)"
INTEGRITY="sha512-$(openssl dgst -sha512 -binary "$TGZ" | base64 -w0)"
node -e "const v=require('./view.json');if(v['dist.integrity']!=='$INTEGRITY'){console.error('integrity',v['dist.integrity'],'$INTEGRITY');process.exit(1)}"
rm -rf extract archive && mkdir extract archive
tar -xzf "$TGZ" -C extract
# Blob bytes as committed: no worktree EOL conversion (core.autocrlf would emit CRLF for text=auto files).
git -C "$REPO" -c core.autocrlf=false -c core.eol=lf archive "$SHA" | tar -x -C archive
# Every packaged file except the CI-stamped version files must equal the commit byte for byte.
: > source-compare.txt
match=0; differ=0; missing=0
while IFS= read -r rel; do
  if [ ! -f "archive/$rel" ]; then echo "NOT_IN_COMMIT $rel" >> source-compare.txt; missing=$((missing+1)); continue; fi
  if cmp -s "extract/package/$rel" "archive/$rel"; then match=$((match+1)); else echo "DIFF $rel" >> source-compare.txt; differ=$((differ+1)); fi
done < <(cd extract/package && find . -type f | sed 's#^\./##' | sort)
echo "match=$match differ=$differ not_in_commit=$missing" | tee -a source-compare.txt
[ -e "$SNAPSHOT" ] && { echo "snapshot exists: $SNAPSHOT" >&2; exit 1; }
cp -a extract/package "$SNAPSHOT"
(cd extract/package && find . -type f -exec sha256sum {} + | sort -k2) > package.sha256
(cd "$SNAPSHOT" && find . -type f -exec sha256sum {} + | sort -k2) > snapshot.sha256
cmp package.sha256 snapshot.sha256 && echo "snapshot_files=$(wc -l < snapshot.sha256) snapshot_match=true"
printf '{"version":"%s","git_head":"%s","integrity":"%s","tgz":"%s","snapshot":"%s"}\n' "$VERSION" "$SHA" "$INTEGRITY" "$TGZ" "$SNAPSHOT" > staged.json
cat staged.json
