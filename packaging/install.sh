#!/usr/bin/env bash
# Per-user install (and in-place upgrade) of Night Vision. No root needed.
#   app:      ~/.local/opt/NightVision
#   command:  ~/.local/bin/nightvision  (symlink)
#   launcher: ~/.local/share/applications/nightvision.desktop
# Running it again from a newer release upgrades the existing install: the old
# copy is replaced as a whole (no stale files survive) and settings in
# ~/.config/nightvision are never touched.
set -euo pipefail

for arg in "$@"; do
    case "$arg" in
        -h|--help) echo "usage: install.sh"; exit 0 ;;
        *) echo "error: unknown option $arg" >&2; exit 2 ;;
    esac
done

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$here/NightVision"
dest="$HOME/.local/opt/NightVision"
bindir="$HOME/.local/bin"
appdir="$HOME/.local/share/applications"
exe_name="NightVision"

if [ ! -x "$src/$exe_name" ]; then
    echo "error: $src/$exe_name not found; run this script from the extracted archive" >&2
    exit 1
fi

# Stop only processes whose executable lives inside THIS install dir. Matching
# the resolved /proc/<pid>/exe path (never the process name) means a source
# checkout, another user's copy or an unrelated "NightVision" program is left alone.
stop_running() {
    local pids=() p exe
    for p in /proc/[0-9]*; do
        exe="$(readlink "$p/exe" 2>/dev/null || true)"
        exe="${exe% (deleted)}"   # the file may already be replaced by an earlier upgrade
        case "$exe" in "$dest"/*) pids+=("${p#/proc/}") ;; esac
    done
    [ "${#pids[@]}" -gt 0 ] || return 0
    echo "Stopping the running Night Vision (pid ${pids[*]})..."
    kill -TERM "${pids[@]}" 2>/dev/null || true
    local i
    for i in $(seq 1 50); do   # up to 10 s for a clean exit (it restores the display gamma on exit)
        local alive=0
        for p in "${pids[@]}"; do [ -d "/proc/$p" ] && alive=1; done
        [ "$alive" = 0 ] && return 0
        sleep 0.2
    done
    echo "It did not exit in time; forcing it to stop (gamma is restored the next time it starts)."
    kill -KILL "${pids[@]}" 2>/dev/null || true
    sleep 0.5
}

read_version() { [ -f "$1/VERSION" ] && head -n1 "$1/VERSION" | tr -d '[:space:]' || true; }

new_version="$(read_version "$src")"
if [ -z "$new_version" ]; then
    # Fall back to the archive folder name (NightVision-<version>-linux-x86_64).
    new_version="$(basename "$here" | sed -n 's/^NightVision-\(.*\)-linux-[^-]*$/\1/p')"
fi
new_version="${new_version:-unknown}"
had_install=0; old_version=""
if [ -e "$dest" ]; then
    had_install=1
    old_version="$(read_version "$dest")"
    old_version="${old_version:-unknown}"   # installs made before VERSION files existed
fi

mkdir -p "$HOME/.local/opt" "$bindir" "$appdir"

# Leftovers of an interrupted earlier run would be wasted space.
rm -rf "$dest".new "$dest".new.* "$dest".old.*

# Build the new tree next to the target (same filesystem) so the swap is two
# renames, then drop the old tree. Nothing of the old version can remain.
tmp="$dest.new.$$"
trap 'rm -rf "$tmp"' EXIT
cp -a "$src" "$tmp"
echo "$new_version" > "$tmp/VERSION"
cp "$here/uninstall.sh" "$tmp/uninstall.sh" 2>/dev/null && chmod 755 "$tmp/uninstall.sh" || true

stop_running
if [ -e "$dest" ]; then
    old="$dest.old.$$"
    mv "$dest" "$old"
    mv "$tmp" "$dest" || { mv "$old" "$dest"; exit 1; }
    rm -rf "$old"
else
    mv "$tmp" "$dest"
fi
trap - EXIT

# Own symlink only: never clobber a regular file the user put there.
link="$bindir/nightvision"
if [ -e "$link" ] && [ ! -L "$link" ]; then
    echo "warning: $link exists and is not a symlink; leaving it alone" >&2
else
    ln -sfn "$dest/$exe_name" "$link"
fi

cat > "$appdir/nightvision.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Night Vision
Comment=Display-side image enhancement for dark scenes
Exec="$dest/$exe_name"
Icon=video-display
Terminal=false
Categories=Utility;
DESKTOP
chmod 644 "$appdir/nightvision.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$appdir" >/dev/null 2>&1 || true
fi

if [ "$had_install" = 0 ]; then
    echo "Installed Night Vision $new_version to $dest"
elif [ "$old_version" = "$new_version" ]; then
    echo "Reinstalled Night Vision $new_version in $dest"
else
    echo "Upgraded from $old_version to $new_version"
fi
echo "Run: nightvision   (make sure $bindir is on your PATH)"
echo "Remove with: $dest/uninstall.sh   (add --purge to also delete your settings)"
