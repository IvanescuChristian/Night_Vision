#!/usr/bin/env bash
# Remove the per-user install made by install.sh.
set -euo pipefail

dest="$HOME/.local/opt/NightVision"
link="$HOME/.local/bin/nightvision"
desktop="$HOME/.local/share/applications/nightvision.desktop"

if [ -L "$link" ] && [ "$(readlink "$link")" = "$dest/NightVision" ]; then
    rm -f "$link"
fi
rm -f "$desktop"
rm -rf "$dest"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi
echo "Night Vision removed. Your settings (if any) under ~/.config were left in place."
