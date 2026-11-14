#!/usr/bin/env bash
# Per-user install of Night Vision. No root needed.
#   app:      ~/.local/opt/NightVision
#   command:  ~/.local/bin/nightvision  (symlink)
#   launcher: ~/.local/share/applications/nightvision.desktop
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$here/NightVision"
dest="$HOME/.local/opt/NightVision"
bindir="$HOME/.local/bin"
appdir="$HOME/.local/share/applications"

if [ ! -x "$src/NightVision" ]; then
    echo "error: $src/NightVision not found; run this script from the extracted archive" >&2
    exit 1
fi

mkdir -p "$HOME/.local/opt" "$bindir" "$appdir"
rm -rf "$dest.new"
cp -a "$src" "$dest.new"
rm -rf "$dest"
mv "$dest.new" "$dest"

ln -sfn "$dest/NightVision" "$bindir/nightvision"

cat > "$appdir/nightvision.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Night Vision
Comment=Display-side image enhancement for dark scenes
Exec="$dest/NightVision"
Icon=video-display
Terminal=false
Categories=Utility;
DESKTOP
chmod 644 "$appdir/nightvision.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$appdir" >/dev/null 2>&1 || true
fi

echo "Installed to $dest"
echo "Run: nightvision   (make sure $bindir is on your PATH)"
echo "Remove with: $here/uninstall.sh"
