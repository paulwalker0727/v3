; ============================================================================
;  Cimitir Desktop — script Inno Setup
;  Compilează cu:  iscc installer\cimitir_setup.iss
;  (build_exe.bat face asta automat dacă găsește Inno Setup)
;
;  Parametri opționali de compilare:
;    /DMyAppVersion=1.2.3        — versiunea afișată în instalator
;    /DSourceDir=..\dist\Cimitir — folderul produs de PyInstaller (onedir)
;    /DOutputDir=..\dist_installer
;    /DSignTool=signcmd          — semnare digitală (vezi build_exe.bat)
; ============================================================================

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Cimitir"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist_installer"
#endif

#define MyAppName "Cimitir Desktop"
#define MyAppPublisher "Cimitir Desktop"
#define MyAppExeName "Cimitir.exe"

[Setup]
AppId={{8F3C1A7E-4B92-4C2D-9E51-1A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Cimitir
DefaultGroupName=Cimitir
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
OutputDir={#OutputDir}
OutputBaseFilename=CimitirSetup-{#MyAppVersion}
SetupIconFile=..\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=admin
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE.txt
#ifdef SignTool
SignTool={#SignTool}
#endif

[Languages]
Name: "ro"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Creează o scurtătură pe Desktop"; GroupDescription: "Scurtături:"
Name: "quicklaunchicon"; Description: "Creează o scurtătură în bara de activități"; GroupDescription: "Scurtături:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "verify_install.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Cimitir"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Ghid de instalare (PDF)"; Filename: "{app}\docs\Ghid_Instalare_Cimitir.pdf"; Check: FileExists(ExpandConstant('{app}\docs\Ghid_Instalare_Cimitir.pdf'))
Name: "{group}\Dezinstalează Cimitir"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Cimitir"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\Cimitir"; Filename: "{app}\{#MyAppExeName}"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Pornește Cimitir"; Flags: nowait postinstall skipifsilent

[Messages]
ro.WelcomeLabel2=Acest program va instala [name/ver] pe calculatorul tău.%n%nDatele tale (baza de date, documentele scanate, licența și setările AI) sunt păstrate separat, în folderul utilizatorului, și NU se pierd la reinstalare sau update.

[Code]
// La dezinstalare NU ștergem datele utilizatorului (%LOCALAPPDATA%\Cimitir
// și %APPDATA%\CimitirDesktop): baza de date, licența și cheia AI rămân intacte.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    MsgBox('Cimitir a fost dezinstalat.' + #13#10 + #13#10 +
           'Baza de date și documentele au fost PĂSTRATE în:' + #13#10 +
           ExpandConstant('{localappdata}\Cimitir') + #13#10 + #13#10 +
           'Licența și cheia AI au fost păstrate în:' + #13#10 +
           ExpandConstant('{userappdata}\CimitirDesktop') + #13#10 + #13#10 +
           'Șterge manual aceste foldere doar dacă vrei să elimini complet datele.',
           mbInformation, MB_OK);
end;
