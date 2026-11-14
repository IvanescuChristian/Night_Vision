# Windows setup (no programming needed)

IMPORTANT: the Windows build is built and smoke-tested by CI; not yet run on a
real Windows PC. Treat the first run as a test. Steps below describe what is
expected, not something already confirmed.

## Before you start: games with anti-cheat

No screen-filter tool can be guaranteed safe with Easy Anti-Cheat (Rust) or
BattlEye (Escape from Tarkov). The game's rules decide. Check the game's rules
or support page first, and use this at your own risk. The README section
"Anti-cheat and safety" explains the details.

## 1. Download

1. Open the project's page on GitHub and click **Releases**.
2. Download the installer (`.exe`) and the checksum file (for example
   `SHA256SUMS.txt`) from the same release.

## 2. Check the download (recommended)

1. Open PowerShell in the folder with the download.
2. Run: `Get-FileHash .\NAME-OF-INSTALLER.exe -Algorithm SHA256`
3. Compare the hash shown with the line for that file in the checksum file. They
   must match exactly. If they differ, do not run it.

## 3. Run the installer

The installer is not code-signed, and will stay unsigned for now (see
[AV_AND_SIGNING.md](AV_AND_SIGNING.md)). Windows SmartScreen will show
"Windows protected your PC".

1. Click **More info**.
2. Click **Run anyway**.

Only do this for a file whose checksum matched the one on the release page.

## 4. If Windows Defender flags it

Programs built with PyInstaller are sometimes flagged by mistake. If Defender
quarantines the file:

1. Confirm the checksum matched.
2. Open Windows Security, Protection history, and restore the item only if you
   are comfortable doing so. Do not turn off antivirus protection.
3. You can report the false positive to Microsoft:
   https://www.microsoft.com/en-us/wdsi/filesubmission
4. If you prefer, build from source (see the README).

## 5. First run

Start Night Vision from the Start menu. The control window opens. Nothing
changes on screen until you choose a mode and a preset.

## 6. Choosing a mode for anti-cheat games

Use **display** mode. It only changes the display's gamma, with no screen
capture and no overlay window, and is the lowest-risk mode in the app. Even
lower risk is using your monitor's own colour/gamma settings or your GPU
driver's colour settings. **overlay** mode captures the screen and draws its
own window, which is more than display mode does.

If the screen looks wrong after a crash, start Night Vision again; it restores
the saved gamma on start. Restarting the PC also resets it.

## 7. Upgrade and uninstall

To upgrade, just run the newer installer: it closes the app, replaces the old
files in place and keeps your settings. See [UPGRADING.md](UPGRADING.md).

To uninstall:

Open Windows Settings, Apps, Installed apps, find Night Vision and choose
Uninstall. Your profiles are in `%APPDATA%\NightVision\` and can be deleted by
hand if you want them gone.
