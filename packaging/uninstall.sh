#!/usr/bin/env bash
# Remove the per-user install made by install.sh (app dir, command symlink,
# launcher). Settings in ~/.config/nightvision are kept unless --purge is given.
set -euo pipefail

purge=0
for arg in "$@"; do
    case "$arg" in
        --purge) purge=1 ;;
        -h|--help) echo "usage: uninstall.sh [--purge]   (--purge also deletes ~/.config/nightvision)"; exit 0 ;;
        *) echo "error: unknown option $arg" >&2; exit 2 ;;
    esac
done

dest="$HOME/.local/opt/NightVision"
link="$HOME/.local/bin/nightvision"
desktop="$HOME/.local/share/applications/nightvision.desktop"
config="${XDG_CONFIG_HOME:-$HOME/.config}/nightvision"

# This script may live inside $dest and delete it; run everything from a
# function and exit before bash reads past it.
main() {
    # Stop only processes running from this install dir (exact exe path match, never by name).
    local pids=() p exe i alive
    for p in /proc/[0-9]*; do
        exe="$(readlink "$p/exe" 2>/dev/null || true)"
        exe="${exe% (deleted)}"
        case "$exe" in "$dest"/*) pids+=("${p#/proc/}") ;; esac
    done
    if [ "${#pids[@]}" -gt 0 ]; then
        echo "Stopping the running Night Vision (pid ${pids[*]})..."
        kill -TERM "${pids[@]}" 2>/dev/null || true
        for i in $(seq 1 50); do
            alive=0
            for p in "${pids[@]}"; do [ -d "/proc/$p" ] && alive=1; done
            [ "$alive" = 0 ] && break
            sleep 0.2
        done
        kill -KILL "${pids[@]}" 2>/dev/null || true
    fi

    if [ -L "$link" ] && [ "$(readlink "$link")" = "$dest/NightVision" ]; then
        rm -f "$link"
    fi
    rm -f "$desktop"
    rm -rf "$dest" "$dest".new "$dest".new.* "$dest".old.*

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
    fi
    if [ "$purge" = 1 ]; then
        rm -rf "$config"
        echo "Night Vision removed, including your settings ($config)."
    else
        echo "Night Vision removed. Your settings ($config) were kept; use --purge to delete them too."
    fi
}
main
exit 0
