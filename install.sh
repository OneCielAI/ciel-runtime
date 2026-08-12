#!/usr/bin/env sh
set -eu

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX="${PREFIX:-"$HOME/.local"}"
SHARE_DIR="${CIEL_RUNTIME_HOME:-"$PREFIX/share/ciel-runtime"}"
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
