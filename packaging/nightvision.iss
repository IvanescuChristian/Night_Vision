; Inno Setup 6 script for the Night Vision installer.
; Build the one-dir app first (dist\NightVision\), then:
;   ISCC.exe /DAppVersion=<version> packaging\nightvision.iss
; The version comes from nightvision/__init__.py (see packaging\make_version_info.py --print-version).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Night Vision"
#define AppExe "NightVision.exe"
#define AppPublisher "IvanescuChristian"

[Setup]
AppId={{741C764E-533D-410D-ABF2-D7209F2B82A9}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} setup
DefaultDirName={autopf}\NightVision
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install (no administrator needed), always. The all-users switch is command-line only
; (/ALLUSERS): a dialog choice would let a later run pick the other scope and leave two installs
; registered under the same AppId (two Apps & features entries, two copies on disk).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
; AppId above must never change: it is how a new installer finds and upgrades the old one.
; An upgrade reuses the previous folder, shortcuts group and task choices (these are the
; defaults, kept explicit so nobody turns them off by accident).
UsePreviousAppDir=yes
UsePreviousGroup=yes
UsePreviousTasks=yes
; The app has no named mutex, so Windows Restart Manager (CloseApplications) closes the running
; app (and its console twin) before files are replaced. RestartApplications=no: PyInstaller
; executables do not register for a restart, and relaunching silently is not wanted; the final
; page offers "Launch" instead.
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
OutputDir=..\dist\installer
OutputBaseFilename=NightVision-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Runs before the new files are copied. Remove the whole previous PyInstaller payload so files
; the new build no longer ships (old DLLs, Python modules) cannot linger and waste space.
; Settings are not here: they live in AppData, never in {app}.
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\NightVision.exe"
Type: files; Name: "{app}\NightVisionCLI.exe"
; An unticked "desktop icon" on upgrade removes the shortcut made by an earlier run.
Type: files; Name: "{autodesktop}\{#AppName}.lnk"; Tasks: not desktopicon

[Files]
Source: "..\dist\NightVision\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Runtime control-channel token written by the app outside {app}; settings (profiles.json) stay.
Type: files; Name: "{localappdata}\NightVision\control.json"
Type: dirifempty; Name: "{localappdata}\NightVision"

[Code]
// A per-user (HKCU) and an all-users (HKLM) install of this AppId would both appear in Apps &
// features and both stay on disk. If the other scope already has it, stop and say how to proceed.
// Keep the GUID below equal to AppId.
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{741C764E-533D-410D-ABF2-D7209F2B82A9}_is1';

function InitializeSetup(): Boolean;
begin
  Result := True;
  if IsAdminInstallMode then
  begin
    if RegKeyExists(HKCU, UninstallKey) then
    begin
      SuppressibleMsgBox('{#AppName} is already installed for the current user only. Run this setup without /ALLUSERS to upgrade it, or uninstall it first.', mbError, MB_OK, IDOK);
      Result := False;
    end;
  end
  else if RegKeyExists(HKLM64, UninstallKey) then
  begin
    SuppressibleMsgBox('{#AppName} is already installed for all users. Run this setup with /ALLUSERS to upgrade that copy, or uninstall it first (Settings, Apps).', mbError, MB_OK, IDOK);
    Result := False;
  end;
end;
