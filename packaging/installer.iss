; Inno Setup script for Aura IB Bridge. Built by packaging\build.ps1 (passes /DAppVersion=x.y.z).
; Per-user install (no administrator prompt), Start Menu shortcut, optional start-with-Windows,
; launches the tray app at the end so the first desk can pair immediately.
; Replaces the pre-0.3 "IB Copilot Bridge" if present (stops it, runs its uninstaller silently;
; its settings and token are migrated by the app itself on first start).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Aura IB Bridge"
#define AppExe "Aura IB Bridge.exe"
#define AppPublisher "Aura Causal Systems"
#define LegacyExe "IB Copilot Bridge.exe"
#define LegacyUninstaller "{localappdata}\Programs\IB Copilot Bridge\unins000.exe"

[Setup]
AppId={{4E2C8F1A-9B7D-4C3E-A6F5-2D8B1E0C7A93}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/Aura-Causal-Systems/aura-ib-bridge
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Aura-IB-Bridge-Setup-{#AppVersion}
SetupIconFile=bridge.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Start {#AppName} when Windows starts (recommended)"; GroupDescription: "Startup:"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "AuraIBBridge"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: autostart
; Legacy autostart value of the pre-0.3 "IB Copilot Bridge"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "IBCopilotBridge"; Flags: deletevalue

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "--open-desk"; Description: "Start {#AppName} now and open the desk"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/IM ""{#AppExe}"" /F"; Flags: runhidden; RunOnceId: "KillBridge"

[Messages]
WelcomeLabel2=This installs the small helper that lets Aura Causal Systems desks (IB Copilot, Bond Copilot) read your Trader Workstation (TWS) book on this computer.%n%nIt is read-only: it cannot place, change or cancel orders. It listens only on this computer and requires a token that each desk receives automatically.%n%nYou will still need TWS running with its API enabled in Read-Only mode (the desk's Setup page shows the five clicks).

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Legacy: String;
begin
  Result := '';
  { Stop running copies (this app and the legacy one) so files and port 7801 are free. }
  Exec('taskkill', '/IM "{#AppExe}" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill', '/IM "{#LegacyExe}" /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  { Remove the legacy "IB Copilot Bridge" install if present. }
  Legacy := ExpandConstant('{#LegacyUninstaller}');
  if FileExists(Legacy) then
    Exec(Legacy, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
