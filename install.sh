#!/usr/bin/env sh
set -eu

PREFIX="${PREFIX:-"$HOME/.local"}"
SHARE_DIR="${CIEL_RUNTIME_HOME:-"$PREFIX/share/ciel-runtime"}"
BIN_DIR="$PREFIX/bin"

mkdir -p "$SHARE_DIR" "$BIN_DIR"

install -m 755 ciel_runtime.py "$SHARE_DIR/ciel_runtime.py"
rm -rf "$SHARE_DIR/ciel_runtime_support"
mkdir -p "$SHARE_DIR/ciel_runtime_support"
cp -R ciel_runtime_support/. "$SHARE_DIR/ciel_runtime_support/"
install -m 755 ciel-runtime-menu.py "$BIN_DIR/ciel-runtime-menu"
install -m 755 ciel-runtime-tool-guard.py "$BIN_DIR/ciel-runtime-tool-guard"
install -m 755 ciel-runtime "$BIN_DIR/ciel-runtime"
install -m 755 ciel-runtimectl "$BIN_DIR/ciel-runtimectl"
install -m 755 ciel-runtime-stop "$BIN_DIR/ciel-runtime-stop"

printf 'Installed Ciel Runtime to %s\n' "$SHARE_DIR"
printf 'Launch with: %s/ciel-runtime\n' "$BIN_DIR"
