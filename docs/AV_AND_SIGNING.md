# Antivirus false positives and code signing

## Why false positives happen

The Windows build is made with PyInstaller, which bundles a Python interpreter
and the program into an executable. Many antivirus products score that kind of
bundle with machine-learning or reputation heuristics, and a new, unsigned
file with no download history is often flagged even though it is harmless.

## What we do

- PyInstaller one-dir builds (not a single self-extracting file).
- No UPX or other executable compression.
- Pinned dependency and build tool versions.
- Version metadata and a clear executable name.
- SHA256 checksums published with every release.
- Build-from-source instructions, so you can build it yourself.

## What we never do

No packing, obfuscation, renaming tricks or any technique designed to evade
antivirus or anti-cheat software.

## If Defender flags a file

Verify the checksum, then report the false positive to Microsoft:
https://www.microsoft.com/en-us/wdsi/filesubmission

## Code signing

Windows builds are unsigned, and will stay unsigned for now: no code-signing
certificate is being purchased. What that means for you:

- Windows SmartScreen will warn "Windows protected your PC". Click **More
  info**, then **Run anyway**.
- Microsoft Defender or another antivirus may flag the file by mistake (a
  false positive).
- Verify the SHA256 checksum against the one on the release page before you
  run anything. If it matches, the file is the one the release built.
- To report a false positive, submit the file to Microsoft:
  https://www.microsoft.com/en-us/wdsi/filesubmission

Background: SignPath Foundation's free tier requires an OSI-approved
open-source license. This project uses PolyForm Strict (source-available), so
it does not qualify. A paid certificate also only removes the SmartScreen
warning after it builds reputation.
