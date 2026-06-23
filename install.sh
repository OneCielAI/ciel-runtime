#!/usr/bin/env sh
set -eu

PREFIX="${PREFIX:-"$HOME/.local"}"
SHARE_DIR="${CLAUDE_ANY_HOME:-"$PREFIX/share/claude-any"}"
BIN_DIR="$PREFIX/bin"

mkdir -p "$SHARE_DIR" "$BIN_DIR"

install -m 755 claude_any.py "$SHARE_DIR/claude_any.py"
rm -rf "$SHARE_DIR/claude_any_support"
mkdir -p "$SHARE_DIR/claude_any_support"
cp -R claude_any_support/. "$SHARE_DIR/claude_any_support/"
install -m 755 claude-any-menu.py "$BIN_DIR/claude-any-menu"
install -m 755 claude-any-tool-guard.py "$BIN_DIR/claude-any-tool-guard"
install -m 755 claude-any "$BIN_DIR/claude-any"
install -m 755 claude-anyctl "$BIN_DIR/claude-anyctl"
install -m 755 claude-any-stop "$BIN_DIR/claude-any-stop"

printf 'Installed Claude Any to %s\n' "$SHARE_DIR"
printf 'Launch with: %s/claude-any\n' "$BIN_DIR"
