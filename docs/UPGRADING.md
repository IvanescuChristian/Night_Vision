# Upgrading Night Vision

Installing a newer version replaces the old one. You do not need to uninstall
first, and no old copy, stale file or second Apps & features entry is left behind.
Your settings are kept.

## Which download do I need?

| You have | Download from the release | What to do |
| --- | --- | --- |
| Windows, installed with the setup program | `NightVision-<version>-windows-x64-setup.exe` | Run it (below) |
| Windows, unzipped portable folder | `NightVision-<version>-windows-x64.zip` | Replace the folder (below) |
| Linux, installed with `install.sh` | `NightVision-<version>-linux-x86_64.tar.gz` | Run its `install.sh` (below) |

Verify any download against `SHA256SUMS.txt` on the release page first.

## Windows installer

1. Run the new `...-setup.exe`. It finds the existing install by its fixed
   application ID, so it upgrades in place: same folder, same Start menu
   shortcut, one entry in Settings, Apps, Installed apps.
2. If Night Vision is running, the installer asks to close it (it closes it for
   you with Windows Restart Manager). It does not start it again by itself;
   tick "Launch Night Vision" on the last page.
3. Before copying, the installer deletes the previous program files (the
   `_internal` folder and the old `.exe` files in the install folder), so
   files that a newer version no longer ships do not stay behind.
4. Choices you made last time (desktop shortcut) are preselected. If you
   untick one, the installer removes that shortcut.
5. Your settings are not in the install folder and are not touched: `%APPDATA%\NightVision\` (profiles, saved gamma backup).

The installer always installs for the current user (no administrator
needed). Installs for all users are only possible from the command line
(`setup.exe /ALLUSERS`). If Night Vision was installed the other way, the new setup
stops with a message instead of creating a second copy; upgrade it the same way
it was installed (or uninstall it first).

Silent upgrade: `...-setup.exe /SILENT` (the app is closed, not relaunched).

Uninstall (Settings, Apps) removes everything the installer put in the install
folder and the shortcuts. Settings are kept so a later reinstall finds them.

## Windows portable zip

The zip is a separate, self-contained copy. It is not the installed program and
does not update it, so do not unzip over an installed copy or over an older
zip folder.

1. Close Night Vision.
2. Delete the old unzipped folder (settings are stored in AppData, not in the folder).
3. Extract the new zip and run `NightVision.exe` from it.

## Linux

```
tar -xzf NightVision-<version>-linux-x86_64.tar.gz
cd NightVision-<version>-linux-x86_64
./install.sh
```

If Night Vision is installed already, `install.sh`:

- builds the new copy next to `~/.local/opt/NightVision`, stops the running
  Night Vision from that folder only (other programs, other copies and a source
  checkout are never touched), then swaps the folders and deletes the old one,
  so no stale file remains;
- keeps your settings in `~/.config/nightvision`;
- prints `Upgraded from <old> to <new>` (installs made by earlier releases
  have no version file and are reported as `unknown`).

It does not restart Night Vision; start it again from the menu or with `nightvision`.

To remove it: `~/.local/opt/NightVision/uninstall.sh`. This removes the app, the
`nightvision` command, the launcher and keeps your settings. Add `--purge` to delete
`~/.config/nightvision` as well.

## Which version do I have?

- Windows installed: Settings, Apps, Installed apps shows the version; or
  right-click `NightVision.exe` in the install folder, Properties, Details.
- Windows zip: right-click `NightVision.exe`, Properties, Details.
- Linux: `cat ~/.local/opt/NightVision/VERSION`.
