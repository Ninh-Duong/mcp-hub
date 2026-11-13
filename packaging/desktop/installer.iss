; Inno Setup 6 script for the desktop status app. Built by scripts/build_desktop.py, which passes
; AppVersion, SourceDir (the PyInstaller output), OutputDir, and IconFile with /D.
;
; Per-user and without admin rights: the app goes to the user's Programs folder, its shortcuts to the
; user's Start Menu and desktop, and its sign-in entry to the user's Run key.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #error SourceDir must point at the PyInstaller output (run scripts/build_desktop.py)
#endif
#ifndef IconFile
  #error IconFile must point at the drawn app icon (run scripts/build_desktop.py)
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

#define AppExe "mcp-hub-status.exe"
#define AppGuid "10E2C381-6C43-42AD-B74C-5A98EC2AA2E8"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
AppId={{{#AppGuid}}
AppName=mcp-hub Status
AppVersion={#AppVersion}
AppPublisher=mcp-hub
DefaultDirName={localappdata}\Programs\mcp-hub Status
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=mcp-hub-status-setup-{#AppVersion}
SetupIconFile={#IconFile}
; GPL-3.0-only: setup shows the license before installing (see THIRD-PARTY-NOTICES.md for the libraries).
LicenseFile=..\..\src\mcp_hub\desktop\LICENSE
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName=mcp-hub Status
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; The running app holds this mutex: setup and uninstall ask to close it first.
AppMutex=Local\mcp-hub-status
CloseApplications=yes
; Windows 10 1809 or later (it ships the Universal C runtime the bundled Python needs), 64-bit only.
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "Open mcp-hub Status when I sign in"; GroupDescription: "Sign-in:"

[InstallDelete]
; An update replaces the whole bundle: files of the previous version (an old mcp_hub-<version>.dist-info
; above all, which made the app read its old version and offer the same update again) must not survive.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; The same AppUserModelID as the running app (win32.APP_USER_MODEL_ID), so the taskbar groups them.
Name: "{autoprograms}\mcp-hub Status"; Filename: "{app}\{#AppExe}"; AppUserModelID: "mcp-hub.status"
Name: "{autodesktop}\mcp-hub Status"; Filename: "{app}\{#AppExe}"; AppUserModelID: "mcp-hub.status"; Tasks: desktopicon

[Registry]
; hubctl app opens the installed exe from here.
Root: HKCU; Subkey: "Software\mcp-hub-status"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
; The app's Settings switch "Open this app at sign-in" changes the same value (desktop/system.py).
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "mcp-hub-status"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,mcp-hub Status}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The app's own remembered look and mini-bar place; nothing of the Hub.
Type: filesandordirs; Name: "{userappdata}\mcp-hub-status"

[Code]
const
  { Where a per-user install of this AppId is registered; it exists only while the app is installed. }
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{#AppGuid}}_is1';
  RequiredFreeMB = 300;

var
  CheckPage: TOutputMsgMemoWizardPage;
  EnoughSpace: Boolean;

{ An older installed version compares below zero: only then does setup offer to update it. }
function CompareInstalled(const Installed: String): Integer;
var
  Old, New: Int64;
begin
  if StrToVersion(Installed, Old) and StrToVersion('{#AppVersion}', New) then
    Result := ComparePackedVersion(Old, New)
  else
    Result := 0;
end;

procedure RunUninstaller();
var
  Uninstaller: String;
  Code: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, UninstallKey, 'UninstallString', Uninstaller) or
     not Exec(RemoveQuotes(Uninstaller), '', '', SW_SHOWNORMAL, ewNoWait, Code) then
    MsgBox('The uninstaller could not start. Remove mcp-hub Status from Settings > Apps instead.', mbError, MB_OK);
end;

{ The same or a newer version is installed: never install again; offer to uninstall it instead. }
procedure ReportInstalled(const Installed: String);
begin
  if WizardSilent then
    Exit;
  if TaskDialogMsgBox('mcp-hub Status ' + Installed + ' is already installed on this PC.',
       'Setup does not install it again. Uninstall it first if you want a fresh install.',
       mbInformation, MB_YESNO, ['Uninstall', 'Close'], 0) = IDYES then
    RunUninstaller();
end;

{ An older version is installed: update it in place (a silent run updates without asking). }
function ConfirmUpdate(const Installed: String): Boolean;
begin
  Result := WizardSilent or (TaskDialogMsgBox(
    'Update mcp-hub Status from ' + Installed + ' to {#AppVersion}?',
    'The app keeps its folder and your appearance settings. Your Hub and its MCPs are not touched.',
    mbConfirmation, MB_YESNO, ['Update', 'Close'], 0) = IDYES);
end;

function InitializeSetup(): Boolean;
var
  Installed: String;
begin
  Result := True;
  if not RegQueryStringValue(HKEY_CURRENT_USER, UninstallKey, 'DisplayVersion', Installed) then
    Exit;
  if CompareInstalled(Installed) < 0 then
    Result := ConfirmUpdate(Installed)
  else begin
    ReportInstalled(Installed);
    Result := False;
  end;
end;

function SpaceLine(): String;
var
  Free, Total: Int64;
begin
  EnoughSpace := GetSpaceOnDisk64(ExpandConstant('{localappdata}'), Free, Total) and
    (Free div (1024 * 1024) >= RequiredFreeMB);
  if EnoughSpace then
    Result := '[OK]  Free disk space: at least ' + IntToStr(RequiredFreeMB) + ' MB'
  else
    Result := '[X]   Free disk space: less than ' + IntToStr(RequiredFreeMB) + ' MB. Free some space, then run setup again.';
end;

{ Only whether the Hub's connection file exists; its content (the token) is never read. }
function HubLine(): String;
begin
  if FileExists(ExpandConstant('{localappdata}\mcp-hub\connection.json')) then
    Result := '[OK]  mcp-hub was found on this PC.'
  else
    Result := '[!]   mcp-hub was not found. The app installs, but shows "Not installed" until you run' + #13#10 +
      '      scripts\hubctl.ps1 install in your mcp-hub folder.';
end;

function SystemReport(): String;
begin
  Result :=
    '[OK]  Windows: ' + GetWindowsVersionString + ', 64-bit' + #13#10 +
    '[OK]  Libraries: Python, Qt, and the Microsoft Visual C++ runtime ship inside the app.' + #13#10 +
    '      Nothing else needs to be installed.' + #13#10 +
    SpaceLine() + #13#10 +
    HubLine();
end;

procedure InitializeWizard();
begin
  CheckPage := CreateOutputMsgMemoPage(wpWelcome, 'System check',
    'Setup checked that this PC can run mcp-hub Status.', 'Results:', SystemReport());
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := (CurPageID <> CheckPage.ID) or EnoughSpace;
  if not Result then
    MsgBox('There is not enough free disk space. Free at least ' + IntToStr(RequiredFreeMB) +
      ' MB, then run setup again.', mbError, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { The sign-in entry may have been added from the app's Settings rather than by setup. }
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, '{#RunKey}', 'mcp-hub-status');
end;
