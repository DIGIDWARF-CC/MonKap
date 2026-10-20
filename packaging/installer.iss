; MonKap's installer (Inno Setup 6). Built by packaging/build.py: ISCC /DAppVersion=x.y.z installer.iss
; Per-user: no administrator rights, installs under %LOCALAPPDATA%\Programs\MonKap, removes cleanly.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1D2C7A-5B0E-4C0B-9C61-3A4D7E2F9B15}
AppName=MonKap
AppVersion={#AppVersion}
AppPublisher=DIGIDWARF-CC
DefaultDirName={autopf}\MonKap
DefaultGroupName=MonKap
UninstallDisplayIcon={app}\MonKap.exe
UninstallDisplayName=MonKap
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=MonKap-{#AppVersion}-setup
SetupIconFile=monkap.ico
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
; always ask where to install, even when MonKap is already installed (the default `auto` skips the page then);
; the folder used last time is still the one offered
DisableDirPage=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\MonKap\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\MonKap"; Filename: "{app}\MonKap.exe"
Name: "{autoprograms}\MonKap (in the browser)"; Filename: "{app}\MonKap.exe"; Parameters: "--browser"; Comment: "Use this when the window doesn't start"
Name: "{autodesktop}\MonKap"; Filename: "{app}\MonKap.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MonKap.exe"; Description: "Start MonKap"; Flags: nowait postinstall skipifsilent

[Code]
// MonKap keeps its settings and the list of opened folders in the user_data folder inside its own folder. The
// uninstaller asks before removing them (a silent uninstall leaves them; deleting the folder removes them too), and
// never touches a dataset folder.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    Data := ExpandConstant('{app}\user_data');
    if DirExists(Data) and (not UninstallSilent) then
      if MsgBox('Also remove MonKap''s settings and the list of folders you opened?' + #13#10 + Data + #13#10#13#10 +
                'Your datasets are not touched.', mbConfirmation, MB_YESNO) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
