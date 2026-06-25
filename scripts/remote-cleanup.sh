#!/usr/bin/env bash
set -euo pipefail

echo "=== 1. Find existing ciel-runtime installations ==="

# Binaries
for path in ~/.local/bin /usr/local/bin /usr/bin ~/bin; do
    if [ -d "$path" ]; then
        found=$(find "$path" -maxdepth 1 -name 'ciel-runtime*' -type f 2>/dev/null || true)
        if [ -n "$found" ]; then
            echo "Found in $path:"
            echo "$found" | sed 's/^/  /'
        fi
    fi
done

# Python source copies
for path in ~/.local/share/ciel-runtime /opt/ciel-runtime ~/ciel-runtime ~/ciel-runtime-portable; do
    if [ -d "$path" ]; then
        echo "Found directory: $path"
        ls -la "$path" 2>/dev/null | head -5 | sed 's/^/  /'
    fi
done

# npm global
if command -v npm >/dev/null 2>&1; then
    npm_ls=$(npm ls -g @oneciel-ai/ciel-runtime 2>/dev/null || true)
    if echo "$npm_ls" | grep -q 'ciel-runtime'; then
        echo "Found npm global:"
        echo "$npm_ls" | sed 's/^/  /'
    fi
fi

# pip
if command -v pip >/dev/null 2>&1; then
    pip_show=$(pip show ciel-runtime 2>/dev/null || true)
    if [ -n "$pip_show" ]; then
        echo "Found pip package:"
        echo "$pip_show" | sed 's/^/  /'
    fi
fi

echo ""
echo "=== 2. Remove all existing installations ==="

# Remove npm global
if command -v npm >/dev/null 2>&1; then
    npm uninstall -g @oneciel-ai/ciel-runtime 2>/dev/null || true
fi

# Remove pip package
if command -v pip >/dev/null 2>&1; then
    pip uninstall -y ciel-runtime 2>/dev/null || true
fi

# Remove directories
for path in ~/.local/share/ciel-runtime /opt/ciel-runtime ~/ciel-runtime ~/ciel-runtime-portable; do
    if [ -d "$path" ]; then
        echo "Removing $path ..."
        rm -rf "$path"
    fi
done

# Remove binaries
for bindir in ~/.local/bin /usr/local/bin /usr/bin ~/bin; do
    if [ -d "$bindir" ]; then
        for f in "$bindir"/ciel-runtime "$bindir"/ciel-runtimectl "$bindir"/ciel-runtime-stop "$bindir"/ciel-runtime-menu "$bindir"/ciel-runtime-menu.py "$bindir"/ciel-runtime-tool-guard "$bindir"/ciel-runtime-tool-guard.py; do
            if [ -e "$f" ]; then
                echo "Removing $f"
                rm -f "$f"
            fi
        done
        # Also remove .cmd / .ps1 if present (Windows wrappers on WSL)
        for f in "$bindir"/ciel-runtime.cmd "$bindir"/ciel-runtime.ps1 "$bindir"/ciel-runtimectl.cmd "$bindir"/ciel-runtimectl.ps1 "$bindir"/ciel-runtime-stop.cmd "$bindir"/ciel-runtime-stop.ps1; do
            if [ -e "$f" ]; then
                echo "Removing $f"
                rm -f "$f"
            fi
        done
    fi
done

echo ""
echo "=== 3. Verify nothing remains ==="
found_any=false
for bindir in ~/.local/bin /usr/local/bin /usr/bin ~/bin; do
    if [ -d "$bindir" ]; then
        rem=$(find "$bindir" -maxdepth 1 -name 'ciel-runtime*' -type f 2>/dev/null || true)
        if [ -n "$rem" ]; then
            echo "STILL EXISTS in $bindir:"
            echo "$rem" | sed 's/^/  /'
            found_any=true
        fi
    fi
done

if [ "$found_any" = false ]; then
    echo "All existing ciel-runtime installations removed."
else
    echo "WARNING: some files remain (may need sudo)."
fi
