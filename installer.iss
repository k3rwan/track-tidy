; Inno Setup script for Track Tidy.
; 1. Install Inno Setup (free): https://jrsoftware.org/isdl.php
; 2. Run build_exe.bat first, so dist\Track-Tidy.exe exists.
; 3. Open this file with Inno Setup Compiler and click "Compile".
;    The final installer will be created in the "installer_output" folder.
;
; Bump MyAppVersion below to match track_tidy.py's APP_VERSION on release -
; it's the only place the version needs to change in this file.
#define MyAppVersion "0.9"

[Setup]
; Fixed forever, regardless of AppName/version changes - this is how Setup
; recognizes "this is the same product" to detect an existing installation.
; Never change this once it's shipped.
AppId={{59AAEA02-0524-4647-BCEB-B225BD129AD2}
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
; Safety net for the in-app updater: it closes the app itself before
; launching Setup, but if that's ever skipped/too slow, this lets Setup
; close (and relaunch) it instead of failing to overwrite locked files.
CloseApplications=yes
RestartApplications=yes

[Files]
Source: "dist\Track-Tidy\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion
; mutagen (GPL-2.0-or-later, imported directly into the app) and FFmpeg
; (GPLv3, bundled as a separate executable) both require their license
; text to accompany the distribution - see THIRD-PARTY-NOTICES.md.
Source: "THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE-GPLv2-mutagen.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE-GPLv3-FFmpeg.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Track Tidy"; Filename: "{app}\Track-Tidy.exe"
Name: "{autodesktop}\Track Tidy"; Filename: "{app}\Track-Tidy.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\Track-Tidy.exe"; Description: "Launch Track Tidy now"; Flags: nowait postinstall skipifsilent

[Messages]
WelcomeLabel2=This will install Track Tidy on your computer.

[Code]
// NOTE: intentionally NOT using {#SetupSetting("AppId")} here - that
// preprocessor macro returns the [Setup] section's RAW text, i.e. the
// double-brace escape sequence ('{{GUID}') as literally written, not the
// resolved single-brace GUID - which silently never matches the real
// registry key. A plain Pascal string doesn't need that escaping, so the
// GUID is just hardcoded here (must be kept in sync with AppId above).
const
  AppUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{59AAEA02-0524-4647-BCEB-B225BD129AD2}_is1';

function GetInstalledVersion(): String;
var
  Version: String;
begin
  if not RegQueryStringValue(HKLM64, AppUninstallKey, 'DisplayVersion', Version) then
    if not RegQueryStringValue(HKLM32, AppUninstallKey, 'DisplayVersion', Version) then
      RegQueryStringValue(HKCU, AppUninstallKey, 'DisplayVersion', Version);
  Result := Version;
end;

// Detects a previous installation (via AppId, so it survives AppName/version
// changes) and asks to update instead of silently reinstalling everything
// with no explanation. Answering "No" cancels Setup entirely.
function InitializeSetup(): Boolean;
var
  InstalledVersion: String;
begin
  Result := True;
  InstalledVersion := GetInstalledVersion();
  if InstalledVersion <> '' then
  begin
    if InstalledVersion <> '{#MyAppVersion}' then
      Result := (MsgBox(
        'Track Tidy ' + InstalledVersion + ' is already installed.' + #13#10 +
        'This will update it to version {#MyAppVersion}.' + #13#10#13#10 +
        'Continue?',
        mbConfirmation, MB_YESNO) = IDYES)
    else
      Result := (MsgBox(
        'Track Tidy {#MyAppVersion} is already installed.' + #13#10#13#10 +
        'Reinstall it anyway?',
        mbConfirmation, MB_YESNO) = IDYES);
  end;
end;
