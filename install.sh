#!/usr/bin/env sh
set -eu

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX="${PREFIX:-"$HOME/.local"}"
DEFAULT_SHARE_DIR="$PREFIX/share/ciel-runtime"
RUNTIME_HOME="${CIEL_RUNTIME_HOME:-}"
# A Windows path has no "/" for ${var##*/} to split on, so strip the last
# backslash as well: Git Bash otherwise keeps the whole path and the snapshot
# guard below never fires, making an install overwrite the pinned snapshot
# directory that a running session was launched from (observed 2026-09-18).
RUNTIME_HOME_BASE="${RUNTIME_HOME##*/}"
RUNTIME_HOME_BASE="${RUNTIME_HOME_BASE##*\\}"
case "$RUNTIME_HOME_BASE" in
  ciel-runtime-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*)
    printf '%s\n' "Warning: ignoring snapshot CIEL_RUNTIME_HOME during install: $RUNTIME_HOME" >&2
    RUNTIME_HOME=""
    ;;
esac
SHARE_DIR="${CIEL_RUNTIME_INSTALL_HOME:-${RUNTIME_HOME:-$DEFAULT_SHARE_DIR}}"
BIN_DIR="$PREFIX/bin"

mkdir -p "$SHARE_DIR" "$BIN_DIR"

install -m 755 "$SOURCE_DIR/ciel_runtime.py" "$SHARE_DIR/ciel_runtime.py"
rm -rf "$SHARE_DIR/ciel_runtime_support"
mkdir -p "$SHARE_DIR/ciel_runtime_support"
cp -R "$SOURCE_DIR/ciel_runtime_support/." "$SHARE_DIR/ciel_runtime_support/"
rm -rf "$SHARE_DIR/scripts"
mkdir -p "$SHARE_DIR/scripts"
cp -R "$SOURCE_DIR/scripts/." "$SHARE_DIR/scripts/"
install -m 755 "$SOURCE_DIR/ciel-runtime-menu.py" "$BIN_DIR/ciel-runtime-menu"
install -m 755 "$SOURCE_DIR/ciel-runtime-tool-guard.py" "$BIN_DIR/ciel-runtime-tool-guard"
install -m 755 "$SOURCE_DIR/ciel-runtime" "$BIN_DIR/ciel-runtime"
install -m 755 "$SOURCE_DIR/ciel-runtimectl" "$BIN_DIR/ciel-runtimectl"
install -m 755 "$SOURCE_DIR/ciel-runtime-stop" "$BIN_DIR/ciel-runtime-stop"

printf 'Installed Ciel Runtime to %s\n' "$SHARE_DIR"
printf 'Launch with: %s/ciel-runtime\n' "$BIN_DIR"
