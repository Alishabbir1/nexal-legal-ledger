; Inno Setup Script for Nexal Legal
; Build chain: build.ps1 -> PyInstaller -> this script
; Output: installer_output\NexalLegalSetup.exe

#define MyAppName "Nexal Legal"
#define MyAppVersion "1.0"
#define MyAppPublisher "Nexal Solutions"
#define MyAppURL "https://example.com"
#define MyAppExeName "NexalLegal.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\NexalLegal
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=NexalLegalSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checked
Name: "autostart"; Description: "Start automatically when Windows starts"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "dist_build\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
; Ensure writable data directory exists
Name: "{localappdata}\SolicitorLedger"; Permissions: users-full
Name: "{localappdata}\SolicitorLedger\logs"; Permissions: users-full
Name: "{localappdata}\SolicitorLedger\backups"; Permissions: users-full
Name: "{localappdata}\SolicitorLedger\backups\local"; Permissions: users-full
Name: "{userdocs}\NexalLegal\Exports"; Permissions: users-full

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartmenu}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Nexal Legal"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and IsTaskSelected('autostart') then
    RegWriteStringValue(HKEY_CURRENT_USER,
      'Software\Microsoft\Windows\CurrentVersion\Run',
      'Nexal Legal', ExpandConstant('{app}\{#MyAppExeName}'));
end;

[UninstallDelete]
; Clean up logs on uninstall (data preserved by default)
Type: filesandordirs; Name: "{localappdata}\SolicitorLedger\logs"
