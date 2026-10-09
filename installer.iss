; Build with Inno Setup 6.7+ / 7 from the source directory:
;   ISCC.exe /DAppBuild=dist\CCRaw installer.iss
; AppVersion must equal ccraw/__init__.py __version__ (checked by tests/test_packaging.py).
#ifndef AppBuild
  #define AppBuild "dist\CCRaw"
#endif
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define PackageDir "v" + StringChange(AppVersion, ".", "")

[Setup]
AppId={{BD85AE22-87A4-4B74-9C52-B975FEE1052C}
AppName=CCRaw
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
AppPublisher=CCRaw
DefaultDirName={localappdata}\Programs\CCRaw
DefaultGroupName=CCRaw
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
OutputDir=.publish\{#PackageDir}\packages
OutputBaseFilename=CCRaw-{#AppVersion}-Setup
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\ccraw.ico
UninstallDisplayIcon={app}\CCRaw.exe
LicenseFile=LICENSE
CloseApplications=yes
RestartApplications=no
Uninstallable=not PortableMode
CreateUninstallRegKey=not PortableMode
UsePreviousAppDir=not PortableMode

[Languages]
Name: "zhcn"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式"; Flags: unchecked; Check: not PortableMode

[Files]
Source: "{#AppBuild}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Upgrades install over the previous version (same AppId); drop its runtime first so
; no stale DLL or model from an older build can shadow the new one.
Type: filesandordirs; Name: "{app}\_internal"; Check: not PortableMode

[Icons]
Name: "{group}\CCRaw"; Filename: "{app}\CCRaw.exe"; Check: not PortableMode
Name: "{userdesktop}\CCRaw"; Filename: "{app}\CCRaw.exe"; Tasks: desktopicon; Check: not PortableMode

[Run]
Filename: "{app}\CCRaw.exe"; Description: "启动 CCRaw"; Flags: nowait postinstall skipifsilent

[Code]
function PortableMode: Boolean;
begin
  Result := ExpandConstant('{param:PORTABLE|0}') = '1';
end;
