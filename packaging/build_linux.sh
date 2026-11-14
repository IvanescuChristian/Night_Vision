#!/usr/bin/env bash
# Build the Linux release tarball. Run from anywhere; uses the active Python.
#   pip install -r packaging/requirements-build.txt
#   packaging/build_linux.sh
# Output (default build/ and dist/linux/ in the repo, both git-ignored):
#   dist/linux/NightVision-<version>-linux-x86_64.tar.gz
#   dist/linux/SHA256SUMS-linux-x86_64.txt
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
py="${PYTHON:-python3}"
work="${NV_WORK:-$root/build}"
out="${NV_OUT:-$root/dist/linux}"

version="$("$py" "$root/packaging/make_version_info.py" --print-version)"
name="NightVision-$version-linux-x86_64"

rm -rf "$work/pyi" "$work/stage" "$out"
mkdir -p "$out" "$work/stage/$name"

(cd "$root" && "$py" -m PyInstaller packaging/nightvision.spec --noconfirm --clean \
    --workpath "$work/pyi" --distpath "$work/pyi-dist")

cp -a "$work/pyi-dist/NightVision" "$work/stage/$name/NightVision"
cp "$root/packaging/install.sh" "$root/packaging/uninstall.sh" "$root/LICENSE" "$work/stage/$name/"
chmod 755 "$work/stage/$name/install.sh" "$work/stage/$name/uninstall.sh"

tar --sort=name --owner=0 --group=0 --numeric-owner -C "$work/stage" -czf "$out/$name.tar.gz" "$name"
"$py" "$root/packaging/checksums.py" -o "$out/SHA256SUMS-linux-x86_64.txt" "$out/$name.tar.gz"
echo "Built $out/$name.tar.gz"
