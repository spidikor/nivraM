; Build only from a verified Windows bundle. User documents/recovery are not installed files.
#ifndef AppVersion
  #define AppVersion "0.2.0-dev.1"
#endif
#ifndef BundleDir
  #define BundleDir "..\dist\nivraM"
#endif
#ifndef BuildId
  #error Use scripts/build_installer.py to bind the installer to a verified bundle.
#endif
#ifndef MaxBundleRelativePathUnits
  #error Use scripts/build_installer.py to supply the exact bundle path-length limit.
#endif
#define AppSubdir "versions\" + AppVersion + "-" + BuildId

[Setup]
AppId={{4A56E435-742C-4E8C-9CEF-AD78F83B7556}
AppName=nivraM
AppVersion={#AppVersion}
AppPublisher=nivraM contributors
DefaultDirName={localappdata}\Programs\nivraM
DefaultGroupName=nivraM
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
OutputDir=..\dist\installer
OutputBaseFilename=nivraM-{#AppVersion}-Windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
AllowNoIcons=yes
UninstallDisplayIcon={app}\{#AppSubdir}\nivraM.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
VersionInfoVersion=0.2.0.1
VersionInfoProductVersion=0.2.0.1
VersionInfoProductTextVersion={#AppVersion}
LicenseFile=..\LICENSE.txt

[Files]
; Exact-build directories avoid obsolete runtime files contaminating a new build.
; Previous directories are retained for explicit rollback; user data is separate.
Source: "{#BundleDir}\*"; DestDir: "{app}\{#AppSubdir}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\nivraM"; Filename: "{app}\{#AppSubdir}\nivraM.exe"; WorkingDir: "{app}\{#AppSubdir}"; Check: not WizardNoIcons

[Run]
Filename: "{app}\{#AppSubdir}\nivraM.exe"; Description: "Open nivraM"; Flags: nowait postinstall skipifsilent

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var LongestPathUnits: Integer;
begin
  Result := '';
  { Inno Unicode strings count UTF-16 units, matching the build inventory. }
  LongestPathUnits := Length(AddBackslash(ExpandConstant('{app}\{#AppSubdir}'))) + {#MaxBundleRelativePathUnits};
  if LongestPathUnits >= 260 then
  begin
    Log('NIVRAM_PATH_TOO_LONG');
    Result := 'The selected installation destination is too long for this build. Choose a shorter destination folder. No application files have been installed.';
  end;
end;

function HasWebView2: Boolean;
var Version: String;
begin
  Result := (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM32, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function InitializeSetup: Boolean;
begin
  Result := HasWebView2;
  if not Result then
    MsgBox('Microsoft Edge WebView2 Runtime is required. Install the official Microsoft WebView2 Runtime, then run this installer again. No runtime is downloaded automatically.', mbError, MB_OK);
end;
