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
; Per-user install by default (no administrator needed); the user may choose all-users in the dialog.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
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

[Files]
Source: "..\dist\NightVision\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
