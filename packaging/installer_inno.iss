; Inno Setup 6 Script for DocSearcher on Windows 10 and Windows 11
; Produces: setup_output\DocSearcher_Setup_Win10_Win11.exe
; Lives in packaging/; SourceDir points all relative paths at the project root.

#define MyAppName "DocSearcher"
#define MyAppPublisher "Antigravity"
#define MyAppExeName "DocSearcher.exe"
; Version comes from the built exe's version resource (set from core/version.py by
; packaging/doc_searcher_win.spec), so build DocSearcher.exe before compiling this script.
#define MyAppExePath AddBackslash(SourcePath) + "..\dist\" + MyAppExeName
#if !FileExists(MyAppExePath)
  #error dist\DocSearcher.exe not found - run packaging\build_win.ps1 first
#endif
#define MyAppVersion GetVersionNumbersString(MyAppExePath)

[Setup]
SourceDir=..
AppId={{D0C5EA4C-11E2-4C75-9B21-7A3982467C4A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
OutputDir=setup_output
OutputBaseFilename=DocSearcher_Setup_Win10_Win11
SetupIconFile=assets\app_icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "chinesetraditional"; MessagesFile: "compiler:Languages\ChineseTraditional.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\assets\app_icon.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\assets\app_icon.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
