; UEFN Ducky Windows install *engine* (Inno Setup 6). The published Setup.exe is
; the Ducky-themed host (release/installer/host) which embeds this file as
; Setup-engine.exe and runs it silently. Build via make_release_installer.ps1,
; which passes /DMyAppVersion=<x.y.z> (from frontend/__init__.py) and /DMyAppExe.

#ifndef MyAppVersion
  #error Pass /DMyAppVersion=x.y.z (use release/installer/make_release_installer.ps1)
#endif
; One-dir build: MyAppDir is the folder PyInstaller produced (UEFN-Ducky.exe,
; UEFN-Ducky-Bridge.exe, and the payload they share). The whole folder installs.
#ifndef MyAppDir
  #error Pass /DMyAppDir=<absolute path to dist\UEFN-Ducky-x.y.z>
#endif
; Installed copies (1.2.342 and older) carry an uninstall log written by Inno
; Setup 6.7; an older compiler would add a second uninstaller (unins001) beside it.
#if Ver < EncodeVer(6, 7, 0)
  #error Build with Inno Setup 6.7 or newer (winget install -e --id JRSoftware.InnoSetup)
#endif

#define MyAppName "UEFN Ducky"
#define MyAppPublisher "UEFN Ducky"
#define MyAppURL "https://uefnducky.org"
#define MyAppExeName "UEFN-Ducky.exe"
; NEVER change this GUID — it must stay identical across every release AND match
; INNO_APP_ID in frontend/install_info.py. Same AppId is what makes a newer
; Setup run as an in-place upgrade (same folder, same uninstall entry) instead of
; a second install.
#define MyAppId "EAD694ED-E221-40B0-909B-AFFD7F683C9E"

[Setup]
AppId={{{#MyAppId}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppCopyright=(c) UEFN Ducky. All rights reserved.
; Full VERSIONINFO on Setup.exe itself — metadata-less installers trip
; Defender ML heuristics (Wacatac.B!ml false positives).
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
VersionInfoCopyright=(c) UEFN Ducky. All rights reserved.
DefaultDirName={autopf}\UEFN Ducky
DisableProgramGroupPage=yes
; Per-user by default. The Ducky Setup host (and in-app updater) pass
; /CURRENTUSER or /ALLUSERS; `commandline` is required for those switches.
; `dialog` remains so a direct run of Setup-engine.exe can still pick scope.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
; Upgrades reuse the folder chosen at first install.
UsePreviousAppDir=yes
; Close a running panel/bridge before replacing files. RestartApplications stays off:
; relaunch is handled explicitly in [Run] below, so the app never starts twice.
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
; Inner engine only — the published artifact is the Ducky Setup host wrapping
; this file. Must NOT start with UEFN-Ducky: the panel's process sweep kills
; every UEFN-Ducky* image and would reap the engine mid-update.
OutputBaseFilename=Setup-engine
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Show the project's Ducky Source-Available License on the install wizard's
; license page — single source of truth, no second copy to drift.
LicenseFile=..\..\LICENSE
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
#if FileExists("..\..\build\app_icon.ico")
SetupIconFile=..\..\build\app_icon.ico
#endif
; Signed releases: make_release_installer.ps1 defines DuckySign and the "ducky"
; sign tool (sign_windows.py) only when a certificate is configured, so the
; uninstaller installed beside the app is signed too. Unsigned builds skip this.
#ifdef DuckySign
SignTool=ducky
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Whole one-dir payload. recursesubdirs+createallsubdirs keeps _internal/ intact —
; the EXEs cannot start without the files beside them.
Source: "{#MyAppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\portable\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "POST_INSTALL.txt"; DestDir: "{app}"; Flags: ignoreversion
; CLI shim. ducky.cmd is a few lines that run ducky.ps1; it does not copy the app.
Source: "ducky.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "ducky.ps1"; DestDir: "{app}"; Flags: ignoreversion
; Uninstall removes {app}; user data in %LOCALAPPDATA%\UEFN-Ducky is only deleted
; if the user answers Yes to the prompt in [Code] below.

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

; Appear in Explorer "Open with" for ANY file type (same pattern as VS Code).
; HKA = HKCU (per-user) or HKLM (per-machine) depending on install scope.
; The running app also re-writes the HKCU keys on launch so portable builds work.
[Registry]
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; ValueType: string; ValueName: ".*"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\*\OpenWithList\{#MyAppExeName}"; ValueType: string; ValueName: ""; ValueData: ""; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\*\shell\UEFNDucky"; ValueType: string; ValueName: ""; ValueData: "Open with UEFN Ducky"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\*\shell\UEFNDucky"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"
Root: HKA; Subkey: "Software\Classes\*\shell\UEFNDucky\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

; No [Run] section: the app is launched from [Code] at ssDone instead.
;
; Why not a `postinstall` [Run] entry? When Setup starts non-elevated
; (PrivilegesRequired=lowest) and then elevates itself for a per-machine install
; or reinstall, Inno runs postinstall entries as the original (non-elevated) user
; through its "spawn server" helper -- even when the Filename is explorer.exe,
; because Inno de-elevates the whole command, not just the target. If that
; spawn-server handshake fails on the machine, Setup shows
; "Internal error: CallSpawnServer: Unexpected response: $0" on auto-launch,
; while a later manual shortcut launch (no spawn server) works fine.
;
; Launching explorer.exe ourselves via ShellExec from [Code] never touches the
; spawn server, yet explorer.exe still hands the app off to the logged-in user's
; shell so the panel starts NON-ELEVATED. Non-elevation is required: the panel's
; native Explorer->Content drag-and-drop is blocked by Windows UIPI when the app
; runs at a higher integrity level.

[Code]
const
  // Same _is1 key frontend/install_info.py reads.
  UninstallRegKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{#MyAppId}}_is1';
  WM_SETTINGCHANGE = $001A;
  SMTO_ABORTIFHUNG = $0002;

function SendMessageTimeout(hWnd: Integer; Msg: Integer; wParam: Integer; lParam: String; fuFlags: Integer; uTimeout: Integer; var dwResult: Integer): Integer;
  external 'SendMessageTimeoutW@user32.dll stdcall';

function PathHasDir(const Path, Dir: string): Boolean;
begin
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Path) + ';') > 0;
end;

function RemovePathDir(const Path, Dir: string): string;
var
  Rest, Item, UpperDir: string;
  P: Integer;
begin
  Rest := Path;
  Result := '';
  UpperDir := Uppercase(Dir);
  while Rest <> '' do
  begin
    P := Pos(';', Rest);
    if P = 0 then
    begin
      Item := Rest;
      Rest := '';
    end
    else
    begin
      Item := Copy(Rest, 1, P - 1);
      Rest := Copy(Rest, P + 1, MaxInt);
    end;
    if (Item <> '') and (Uppercase(Item) <> UpperDir) then
    begin
      if Result <> '' then
        Result := Result + ';';
      Result := Result + Item;
    end;
  end;
end;

procedure NotifyEnvironment();
var
  Res: Integer;
begin
  SendMessageTimeout(HWND_BROADCAST, WM_SETTINGCHANGE, 0, 'Environment', SMTO_ABORTIFHUNG, 5000, Res);
end;

procedure AddDuckyToUserPath();
var
  Path, App: string;
begin
  App := ExpandConstant('{app}');
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', Path) then
    Path := '';
  if PathHasDir(Path, App) then
    Exit;
  if Path = '' then
    Path := App
  else if Path[Length(Path)] = ';' then
    Path := Path + App
  else
    Path := Path + ';' + App;
  RegWriteStringValue(HKCU, 'Environment', 'Path', Path);
  NotifyEnvironment();
end;

procedure RemoveDuckyFromUserPath();
var
  Path, App, Next: string;
begin
  App := ExpandConstant('{app}');
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', Path) then
    Exit;
  if not PathHasDir(Path, App) then
    Exit;
  Next := RemovePathDir(Path, App);
  RegWriteStringValue(HKCU, 'Environment', 'Path', Next);
  NotifyEnvironment();
end;

var
  // Custom "launch on finish" checkbox (replaces the built-in postinstall one so
  // the launch can go through ShellExec instead of Inno's spawn server).
  LaunchCheckBox: TNewCheckBox;

function CmdLineSwitch(const Name: string): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

procedure WriteProgress(Percent: Integer; const Status: string);
var
  Dir, Path: string;
begin
  Dir := ExpandConstant('{localappdata}\UEFN-Ducky');
  Path := Dir + '\setup-progress.txt';
  ForceDirectories(Dir);
  SaveStringToFile(Path, IntToStr(Percent) + #10 + Status, False);
end;

// Launch the panel via Explorer so it runs at the logged-in user's integrity
// level, bypassing Inno's postinstall spawn server. Quotes guard the space in
// DefaultDirName ("...\UEFN Ducky\...").
procedure LaunchApp();
var
  ErrorCode: Integer;
begin
  ShellExec('',
    ExpandConstant('{win}\explorer.exe'),
    '"' + ExpandConstant('{app}\{#MyAppExeName}') + '"',
    ExpandConstant('{app}'),
    SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

function IsUpgrade(): Boolean;
begin
  Result := RegKeyExists(HKLM64, UninstallRegKey)
    or RegKeyExists(HKLM32, UninstallRegKey)
    or RegKeyExists(HKCU, UninstallRegKey);
end;

procedure InitializeWizard();
begin
  if IsUpgrade() then
    WizardForm.Caption := 'Updating {#MyAppName} to v{#MyAppVersion}';

  // Own the finish-page launch checkbox so the launch runs via ShellExec (see
  // LaunchApp) rather than a postinstall [Run] entry / spawn server.
  LaunchCheckBox := TNewCheckBox.Create(WizardForm);
  LaunchCheckBox.Parent := WizardForm.FinishedPage;
  LaunchCheckBox.Left := WizardForm.FinishedLabel.Left;
  LaunchCheckBox.Top :=
    WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(16);
  LaunchCheckBox.Width := WizardForm.FinishedLabel.Width;
  LaunchCheckBox.Height := ScaleY(17);
  LaunchCheckBox.Caption := ExpandConstant('{cm:LaunchProgram,{#MyAppName}}');
  LaunchCheckBox.Checked := True;
end;

// No self-delete of {srcexe} here: a hidden cmd.exe running a delayed
// "del" on the running installer is classic dropper behavior and triggered
// Defender ML (Trojan:Win32/Wacatac.B!ml) on releases. The app prunes its
// own %TEMP%\UEFN-Ducky\Setup-*.exe cache on startup (updater.sweep_installer_cache).

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
var
  Pct: Integer;
begin
  if MaxProgress <= 0 then
    Exit;
  Pct := (CurProgress * 100) div MaxProgress;
  WriteProgress(Pct, 'Installing...');
end;

// ssDone is SUCCESS ONLY (never after UAC No / abort). Launch when the
// checkbox is ticked, or always for silent in-app updates — that is the only
// relaunch path (frontend/updater.py must not start the panel on failure).
// /NOLAUNCH is the custom host's Finish checkbox (silent updates omit it).
procedure CurStepChanged(CurStep: TSetupStep);
var
  PanelCheckCode: Integer;
begin
  if CurStep = ssInstall then
    WriteProgress(5, 'Copying files...');
  if CurStep = ssPostInstall then
  begin
    WriteProgress(95, 'Verifying installed panel...');
    if not Exec(ExpandConstant('{app}\{#MyAppExeName}'),
      '--verify-panel "' + ExpandConstant('{tmp}\ducky-panel-check.json') + '"',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, PanelCheckCode) then
      RaiseException('Could not verify the installed panel. Please run Setup again.');
    if PanelCheckCode <> 0 then
      RaiseException('The installed panel is incomplete. Please run Setup again.');
    WriteProgress(98, 'Creating shortcuts...');
    AddDuckyToUserPath();
  end;
  if CurStep = ssDone then
  begin
    WriteProgress(100, 'Done');
    if CmdLineSwitch('/NOLAUNCH') then
      Exit;
    if WizardSilent or ((LaunchCheckBox <> nil) and LaunchCheckBox.Checked) then
      LaunchApp();
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // Re-running Setup over an existing install is always an in-place update:
  // the license, folder, and shortcut choices were made at first install, so
  // jump straight to installing.
  Result := IsUpgrade()
    and ((PageID = wpLicense) or (PageID = wpSelectDir)
      or (PageID = wpSelectTasks) or (PageID = wpReady));
end;

// On uninstall, offer to remove per-user data (chats, settings, project config).
// Keeping it is the default: /SUPPRESSMSGBOXES or pressing Enter answers No, and a
// later reinstall picks the data straight back up. Only the uninstalling Windows
// user's AppData is offered — other accounts keep theirs.
procedure DeleteUserData();
var
  Dirs: TArrayOfString;
  I: Integer;
begin
  SetArrayLength(Dirs, 2);
  Dirs[0] := ExpandConstant('{localappdata}\UEFN-Ducky');
  // Legacy brand folder from old UEFN-Agent builds.
  Dirs[1] := ExpandConstant('{localappdata}\UEFN-Agent');
  for I := 0 to GetArrayLength(Dirs) - 1 do
    if DirExists(Dirs[I]) then
      DelTree(Dirs[I], True, True, True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RemoveDuckyFromUserPath();
    DataDir := ExpandConstant('{localappdata}\UEFN-Ducky');
    if DirExists(DataDir) then
    begin
      if MsgBox('Also remove your UEFN Ducky data?' #13#10#13#10
                + DataDir + #13#10#13#10
                + 'Yes — delete chats, settings, and cached project data for this Windows user.' #13#10
                + 'No — keep everything; a future install will pick it up again.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DeleteUserData();
    end;
  end;
end;
