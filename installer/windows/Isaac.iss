; Isaac Windows 11 Installer
#define AppName "Isaac"
#define AppVersion "5.4.0"
#define AppPublisher "Isaac"
#define AppExeName "start_isaac_windows.cmd"
[Setup]
AppId={{A6D5A7D7-1B4B-4C77-9D4A-ISAAC540WIN}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Isaac
DefaultGroupName=Isaac
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=IsaacSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
Uninstallable=yes
[Files]
Source: "..\..\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".git\*;.venv\*;dist\*;installer\windows\*.iss"
[Dirs]
Name: "{app}\data"
Name: "{app}\logs"
Name: "{app}\runtime"
Name: "{app}\workspace"
Name: "{app}\traces"
[Icons]
Name: "{group}\Isaac"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\Isaac API"; Filename: "{app}\start_isaac_api_windows.cmd"; WorkingDir: "{app}"
Name: "{group}\Uninstall Isaac"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Isaac"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install_windows11.ps1"" -NoStart"; WorkingDir: "{app}"; StatusMsg: "Isaac wird eingerichtet..."; Flags: waituntilterminated
Filename: "{app}\start_isaac_windows.cmd"; WorkingDir: "{app}"; Description: "Isaac jetzt starten"; Flags: postinstall nowait skipifsilent
[UninstallDelete]
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\workspace"
Type: filesandordirs; Name: "{app}\traces"
