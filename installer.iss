; Inno Setup script for Track Tidy.
; 1. Install Inno Setup (free): https://jrsoftware.org/isdl.php
; 2. Run build_exe.bat first, so dist\Track-Tidy.exe exists.
; 3. Open this file with Inno Setup Compiler and click "Compile".
;    The final installer will be created in the "installer_output" folder.
;
; Bump MyAppVersion below to match track_tidy.py's APP_VERSION on release -
; it's the only place the version needs to change in this file.
#define MyAppVersion "0.3"

[Setup]
AppName=Track Tidy
AppVersion={#MyAppVersion}
AppPublisher=KEVZ
DefaultDirName={autopf}\Track Tidy
DefaultGroupName=Track Tidy
UninstallDisplayIcon={app}\Track-Tidy.exe
OutputDir=installer_output
OutputBaseFilename=Track-Tidy-Setup-v{#MyAppVersion}
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "dist\Track-Tidy\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Track Tidy"; Filename: "{app}\Track-Tidy.exe"
Name: "{autodesktop}\Track Tidy"; Filename: "{app}\Track-Tidy.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\Track-Tidy.exe"; Description: "Launch Track Tidy now"; Flags: nowait postinstall skipifsilent

[Messages]
WelcomeLabel2=This will install Track Tidy on your computer.
