; Inno Setup script for Zephyr Download Manager.
; One-time, on your build PC only: install Inno Setup (free) from https://jrsoftware.org/isdl.php
; Then run build_installer.bat in this folder. It produces Output\Zephyr-Setup.exe.
#define AppName "Zephyr Download Manager"
#define AppVersion "1.0"
#define AppExe "Zephyr.exe"

; ---------------------------------------------------------------------------------------------
; FULLY AUTOMATIC, FORCED EXTENSION INSTALL (do this AFTER you publish the extension to the stores):
; Remove the leading ";" from the line(s) below and paste the extension ID(s) from the store, then
; rebuild. The installer then sets a machine-wide browser policy so Chrome, Edge and Brave install the
; extension silently for EVERY user and EVERY profile on the PC. (Chrome/Edge only allow this for
; store-hosted extensions on a normal PC.) For Edge use the ID from Edge Add-ons (free); if you only set
; the Chrome ID, Edge is attempted with it too, which works on some Windows versions only.
;#define ChromeStoreId "paste-the-32-letter-chrome-web-store-id-here"
;#define EdgeStoreId   "paste-the-32-letter-edge-addons-id-here"
; ---------------------------------------------------------------------------------------------

[Setup]
AppId={{B6E1E9C1-7A2C-4F3B-9B0D-9D4D7E1E6A01}}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\Zephyr
DefaultGroupName={#AppName}
OutputDir=Output
OutputBaseFilename=Zephyr-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupIconFile=icon.ico
WizardImageFile=wizard.bmp
UninstallDisplayIcon={app}\{#AppExe}

[Files]
Source: "..\release\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\browser-extension\*"; DestDir: "{app}\browser-extension"; Flags: ignoreversion recursesubdirs
Source: "install_extension.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "Install-Guide.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\release\runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs skipifsourcedoesntexist

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startupicon"; Description: "Start {#AppName} automatically when Windows starts (minimized to tray)"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "sendtoicon"; Description: "Add {#AppName} to the right-click ""Send to"" menu"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\icon.ico"
Name: "{group}\Installation guide"; Filename: "{app}\Install-Guide.html"; IconFilename: "{app}\icon.ico"
Name: "{group}\Install browser extension"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install_extension.ps1"""; IconFilename: "{app}\icon.ico"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--minimized"; IconFilename: "{app}\icon.ico"; Tasks: startupicon
Name: "{userappdata}\Microsoft\Windows\SendTo\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\icon.ico"; Tasks: sendtoicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent
; Opens in the default browser right after installing: shows how to add the extension by hand if the helper below fails.
Filename: "{app}\Install-Guide.html"; Description: "Open the installation guide (how to add the browser extension)"; \
    Flags: postinstall shellexec runasoriginaluser skipifsilent
#ifndef ChromeStoreId
; No store ID set: offer the guided setup (all installed browsers) on the last page of the installer.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install_extension.ps1"""; \
    Description: "Add the Zephyr extension to my browsers (2 clicks each)"; Flags: postinstall runasoriginaluser skipifsilent
#endif

[Registry]
; Forced, silent install for all users/profiles. Only active when ChromeStoreId (and/or EdgeStoreId) is set above.
; Removed again automatically when Zephyr is uninstalled (the browsers then drop the extension).
#ifdef ChromeStoreId
Root: HKLM; Subkey: "Software\Policies\Google\Chrome\ExtensionInstallForcelist"; ValueType: string; ValueName: "{code:ChromeIdx}"; ValueData: "{#ChromeStoreId};https://clients2.google.com/service/update2/crx"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\Policies\BraveSoftware\Brave\ExtensionInstallForcelist"; ValueType: string; ValueName: "{code:BraveIdx}"; ValueData: "{#ChromeStoreId};https://clients2.google.com/service/update2/crx"; Flags: uninsdeletevalue
#endif
#ifdef EdgeStoreId
Root: HKLM; Subkey: "Software\Policies\Microsoft\Edge\ExtensionInstallForcelist"; ValueType: string; ValueName: "{code:EdgeIdx}"; ValueData: "{#EdgeStoreId};https://edge.microsoft.com/extensionwebstorebase/v1/crx"; Flags: uninsdeletevalue
#else
#ifdef ChromeStoreId
Root: HKLM; Subkey: "Software\Policies\Microsoft\Edge\ExtensionInstallForcelist"; ValueType: string; ValueName: "{code:EdgeIdx}"; ValueData: "{#ChromeStoreId};https://clients2.google.com/service/update2/crx"; Flags: uninsdeletevalue
#endif
#endif

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
{ Browser policy lists use value names 1, 2, 3 ... Reuse our own entry on re-install, otherwise take the
  first free number so we never overwrite another program's entry. }
function ForceIndex(const Key, ExtId: String): String;
var
  Names: TArrayOfString;
  I, N: Integer;
  Data: String;
begin
  if RegGetValueNames(HKLM, Key, Names) then
    for I := 0 to GetArrayLength(Names) - 1 do
      if RegQueryStringValue(HKLM, Key, Names[I], Data) then
        if Pos(Lowercase(ExtId), Lowercase(Data)) = 1 then
        begin
          Result := Names[I];
          Exit;
        end;
  N := 1;
  while RegValueExists(HKLM, Key, IntToStr(N)) do
    N := N + 1;
  Result := IntToStr(N);
end;

#ifdef ChromeStoreId
function ChromeIdx(Param: String): String;
begin
  Result := ForceIndex('Software\Policies\Google\Chrome\ExtensionInstallForcelist', '{#ChromeStoreId}');
end;

function BraveIdx(Param: String): String;
begin
  Result := ForceIndex('Software\Policies\BraveSoftware\Brave\ExtensionInstallForcelist', '{#ChromeStoreId}');
end;
#endif

#ifdef EdgeStoreId
function EdgeIdx(Param: String): String;
begin
  Result := ForceIndex('Software\Policies\Microsoft\Edge\ExtensionInstallForcelist', '{#EdgeStoreId}');
end;
#else
#ifdef ChromeStoreId
function EdgeIdx(Param: String): String;
begin
  Result := ForceIndex('Software\Policies\Microsoft\Edge\ExtensionInstallForcelist', '{#ChromeStoreId}');
end;
#endif
#endif
