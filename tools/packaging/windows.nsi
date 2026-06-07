; NSIS installer script for Teacher's Teammate
; Build from repo root:  makensis tools/packaging/windows.nsi
;
; Prerequisites:
;   * Binaries must already be in dist\ (run tools/build/build_standalone.py first)
;   * NSIS >= 3.0  (https://nsis.sourceforge.io/)
;   * NSIS plugins (all bundled with NSIS): LogicLib, nsDialogs, System, UserInfo

!define APP_NAME      "Teacher's Teammate"
; APP_VERSION and APP_VERSION_WIN can be overridden via /D on the command line.
; APP_VERSION_WIN must be X.Y.Z.W (Windows resource format); pre-release suffixes stripped.
!ifndef APP_VERSION
  !define APP_VERSION "0.1.0"
!endif
!ifndef APP_VERSION_WIN
  !define APP_VERSION_WIN "0.1.0.0"
!endif
!define APP_PUBLISHER "Teacher's Teammate Contributors"
!define APP_URL       "https://github.com/JoHoenk/teachers-teammate"
!define REG_KEY       "Software\TeachersTeammate"
!define UNINST_KEY    "Software\Microsoft\Windows\CurrentVersion\Uninstall\TeachersTeammate"

Name    "${APP_NAME} ${APP_VERSION}"
OutFile "..\..\dist\teachers-teammate-${APP_VERSION}-w64-setup.exe"

; Default install directory — overridden in .onInit based on scope.
InstallDir "$PROGRAMFILES64\TeachersTeammate"

; Start without elevation. UAC is only triggered when the user explicitly
; chooses "Install for all users" on the scope page (see ScopePageLeave).
RequestExecutionLevel user

; ── Modern UI ───────────────────────────────────────────────────────────────

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"

!define MUI_ABORTWARNING
!define MUI_ICON   "..\..\teachers_teammate\assets\teachers_teammate.ico"
!define MUI_UNICON "..\..\teachers_teammate\assets\teachers_teammate.ico"
; Side-panel image shown on the Welcome and Finish pages (164×314 24-bit BMP).
; Generate with:  python tools/packaging/make_ico.py
!define MUI_WELCOMEFINISHPAGE_BITMAP "..\..\teachers_teammate\assets\teachers_teammate_welcome.bmp"

; ── Variables ────────────────────────────────────────────────────────────────

Var IsAdmin          ; "1" if the process has administrator rights, "0" otherwise
Var InstallScope     ; "all" = system-wide (HKLM), "user" = current user (HKCU)
Var UninstScope      ; same two values, read back during uninstall
Var ScopeDialog
Var ScopeAllUsers
Var ScopeCurrentUser

; Upgrade detection
Var PreviousVersion  ; DisplayVersion of an already-installed copy, "" if none

; Uninstaller cleanup page variables
Var UnDelPackages    ; checkbox control handle for packages directory
Var UnDelCache       ; checkbox control handle for cache/settings directory
Var DoDelPackages    ; BST_CHECKED / BST_UNCHECKED — user's choice
Var DoDelCache       ; BST_CHECKED / BST_UNCHECKED — user's choice

; ── Installer pages ──────────────────────────────────────────────────────────

!insertmacro MUI_PAGE_WELCOME
Page custom ScopePageCreate ScopePageLeave
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

; ── Uninstaller pages ────────────────────────────────────────────────────────

!insertmacro MUI_UNPAGE_CONFIRM
UninstPage custom un.UnDataPageCreate un.UnDataPageLeave
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

; ── Version info embedded in the exe ────────────────────────────────────────

VIProductVersion "${APP_VERSION_WIN}"
VIAddVersionKey "ProductName"     "${APP_NAME}"
VIAddVersionKey "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey "CompanyName"     "${APP_PUBLISHER}"
VIAddVersionKey "FileVersion"     "${APP_VERSION}"
VIAddVersionKey "FileDescription" "Installer for ${APP_NAME}"
VIAddVersionKey "LegalCopyright"  "Apache-2.0 Licence"

; ── Initialisation ───────────────────────────────────────────────────────────

Function .onInit
  ; Detect whether we are running with administrator rights.
  UserInfo::GetAccountType
  Pop $0
  ${If} $0 == "Admin"
    StrCpy $IsAdmin "1"
  ${Else}
    StrCpy $IsAdmin "0"
  ${EndIf}

  ; Set default scope and install directory based on privilege level,
  ; then override with any previous install location stored in the registry.
  ${If} $IsAdmin == "1"
    StrCpy $InstallScope "all"
    SetShellVarContext all
    StrCpy $INSTDIR "$PROGRAMFILES64\TeachersTeammate"
    ReadRegStr $0 HKLM "${REG_KEY}" "InstallDir"
    ${If} $0 != ""
      StrCpy $INSTDIR $0
    ${EndIf}
  ${Else}
    StrCpy $InstallScope "user"
    SetShellVarContext current
    StrCpy $INSTDIR "$LOCALAPPDATA\Programs\TeachersTeammate"
    ReadRegStr $0 HKCU "${REG_KEY}" "InstallDir"
    ${If} $0 != ""
      StrCpy $INSTDIR $0
    ${EndIf}
  ${EndIf}

  ; ── Detect a previous installation and offer an in-place upgrade ──────────
  ; $INSTDIR now points at any previous install location read above.
  ${If} $InstallScope == "all"
    ReadRegStr $PreviousVersion HKLM "${UNINST_KEY}" "DisplayVersion"
  ${Else}
    ReadRegStr $PreviousVersion HKCU "${UNINST_KEY}" "DisplayVersion"
  ${EndIf}

  ${If} ${FileExists} "$INSTDIR\Uninstall.exe"
    MessageBox MB_OKCANCEL|MB_ICONQUESTION \
      "Teacher's Teammate $PreviousVersion is already installed.$\r$\n$\r$\nClick OK to upgrade to ${APP_VERSION}. Your settings and downloaded models will be kept.$\r$\nClick Cancel to abort the installation." \
      /SD IDOK IDOK upgrade_proceed
    Quit
  upgrade_proceed:
    ; Run the existing uninstaller silently and synchronously, leaving it in
    ; place (_?=) so the old files are removed before the new bundle is written.
    ; The silent uninstaller skips the data-cleanup page (see un.onInit), so
    ; user settings and downloaded packages in LocalAppData are preserved.
    ExecWait '"$INSTDIR\Uninstall.exe" /S _?=$INSTDIR'
  ${EndIf}
FunctionEnd

; ── Install scope page ───────────────────────────────────────────────────────

Function ScopePageCreate
  nsDialogs::Create 1018
  Pop $ScopeDialog
  ${If} $ScopeDialog == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 280u 16u \
    "Choose how ${APP_NAME} should be installed:"

  ${NSD_CreateRadioButton} 10u 22u 270u 14u \
    "Install for all users  (Program Files - requires administrator rights)"
  Pop $ScopeAllUsers

  ${NSD_CreateRadioButton} 10u 40u 270u 14u \
    "Install only for me  (AppData - no administrator rights required)"
  Pop $ScopeCurrentUser

  ; Pre-select based on current scope (set in .onInit).
  ${If} $InstallScope == "all"
    ${NSD_Check} $ScopeAllUsers
  ${Else}
    ${NSD_Check} $ScopeCurrentUser
  ${EndIf}

  nsDialogs::Show
FunctionEnd

Function ScopePageLeave
  ${NSD_GetState} $ScopeAllUsers $0
  ${If} $0 == ${BST_CHECKED}
    ; User wants a system-wide install.
    ${If} $IsAdmin == "0"
      ; We are not elevated — re-launch with UAC elevation.
      ; ShellExecuteW returns > 32 on success (process handle), <= 32 on failure.
      System::Call 'shell32::ShellExecuteW(i $HWNDPARENT, w "runas", w "$EXEPATH", w "", w "", i 1) i.r0'
      ${If} $0 > 32
        ; Elevated instance launched successfully — quit the non-elevated one.
        Quit
      ${Else}
        ; UAC was cancelled or denied — stay on the scope page.
        Abort
      ${EndIf}
    ${EndIf}
    StrCpy $InstallScope "all"
    SetShellVarContext all
    StrCpy $INSTDIR "$PROGRAMFILES64\TeachersTeammate"
  ${Else}
    StrCpy $InstallScope "user"
    SetShellVarContext current
    StrCpy $INSTDIR "$LOCALAPPDATA\Programs\TeachersTeammate"
  ${EndIf}
FunctionEnd

; ── Install sections ─────────────────────────────────────────────────────────

Section "Core (required)" SecCore
  SectionIn RO
  SetOutPath "$INSTDIR"

  ; Install the full onedir bundle produced by PyInstaller
  File /r "..\..\dist\teachers-teammate\*"

  ; Persist the install location and scope in the appropriate registry hive.
  ${If} $InstallScope == "all"
    WriteRegStr HKLM "${REG_KEY}" "InstallDir" "$INSTDIR"
    WriteRegStr HKLM "${REG_KEY}" "Version"    "${APP_VERSION}"
    WriteRegStr HKLM "${REG_KEY}" "Scope"      "all"
  ${Else}
    WriteRegStr HKCU "${REG_KEY}" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "${REG_KEY}" "Version"    "${APP_VERSION}"
    WriteRegStr HKCU "${REG_KEY}" "Scope"      "user"
  ${EndIf}

  ; Create uninstaller
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; Add/Remove Programs entry — scope-aware registry hive.
  ${If} $InstallScope == "all"
    WriteRegStr   HKLM "${UNINST_KEY}" "DisplayName"     "${APP_NAME}"
    WriteRegStr   HKLM "${UNINST_KEY}" "DisplayVersion"  "${APP_VERSION}"
    WriteRegStr   HKLM "${UNINST_KEY}" "Publisher"       "${APP_PUBLISHER}"
    WriteRegStr   HKLM "${UNINST_KEY}" "URLInfoAbout"    "${APP_URL}"
    WriteRegStr   HKLM "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr   HKLM "${UNINST_KEY}" "DisplayIcon"     "$INSTDIR\teachers-teammate.exe"
    WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify"        1
    WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair"        1
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKLM "${UNINST_KEY}" "EstimatedSize"   "$0"
  ${Else}
    WriteRegStr   HKCU "${UNINST_KEY}" "DisplayName"     "${APP_NAME}"
    WriteRegStr   HKCU "${UNINST_KEY}" "DisplayVersion"  "${APP_VERSION}"
    WriteRegStr   HKCU "${UNINST_KEY}" "Publisher"       "${APP_PUBLISHER}"
    WriteRegStr   HKCU "${UNINST_KEY}" "URLInfoAbout"    "${APP_URL}"
    WriteRegStr   HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr   HKCU "${UNINST_KEY}" "DisplayIcon"     "$INSTDIR\teachers-teammate.exe"
    WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify"        1
    WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair"        1
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize"   "$0"
  ${EndIf}

SectionEnd

Section "Start Menu shortcuts" SecStartMenu
  ; SetShellVarContext was already applied in ScopePageLeave / .onInit,
  ; so $SMPROGRAMS resolves to the correct per-user or all-users folder.
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut  "$SMPROGRAMS\${APP_NAME}\Teacher's Teammate.lnk" \
                  "$INSTDIR\teachers-teammate.exe"
  CreateShortcut  "$SMPROGRAMS\${APP_NAME}\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Desktop shortcut" SecDesktop
  CreateShortcut "$DESKTOP\Teacher's Teammate.lnk" "$INSTDIR\teachers-teammate.exe"
SectionEnd

; ── Uninstaller data-cleanup page ────────────────────────────────────────────

Function un.UnDataPageCreate
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 280u 20u \
    "Choose which user data to remove:"

  ${NSD_CreateCheckBox} 10u 28u 270u 14u \
    "Delete downloaded packages (spaCy, PaddleOCR, language models)"
  Pop $UnDelPackages
  ${NSD_Check} $UnDelPackages

  ${NSD_CreateCheckBox} 10u 48u 270u 14u \
    "Delete application cache and settings"
  Pop $UnDelCache
  ; unchecked by default — user may want to keep settings if reinstalling

  nsDialogs::Show
FunctionEnd

Function un.UnDataPageLeave
  ${NSD_GetState} $UnDelPackages $DoDelPackages
  ${NSD_GetState} $UnDelCache    $DoDelCache
FunctionEnd

; ── Uninstaller ──────────────────────────────────────────────────────────────

Function un.onInit
  ; When the installer runs us silently as part of an in-place upgrade, the
  ; data-cleanup page never appears — so explicitly preserve all user data
  ; (downloaded packages and settings/cache) instead of inheriting page defaults.
  IfSilent 0 +3
    StrCpy $DoDelPackages ${BST_UNCHECKED}
    StrCpy $DoDelCache    ${BST_UNCHECKED}
FunctionEnd

Section "Uninstall"
  ; Determine the install scope from the registry to use the right hive
  ; for shortcuts and uninstall-entry removal.
  ReadRegStr $UninstScope HKLM "${REG_KEY}" "Scope"
  ${If} $UninstScope == "all"
    SetShellVarContext all
  ${Else}
    ReadRegStr $UninstScope HKCU "${REG_KEY}" "Scope"
    ${If} $UninstScope != "user"
      StrCpy $UninstScope "user"   ; fallback if registry is missing
    ${EndIf}
    SetShellVarContext current
  ${EndIf}

  ; Remove program files.
  RMDir /r "$INSTDIR"

  ; Remove shortcuts (SetShellVarContext above routes $SMPROGRAMS correctly).
  Delete "$SMPROGRAMS\${APP_NAME}\Teacher's Teammate.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Uninstall.lnk"
  RMDir  "$SMPROGRAMS\${APP_NAME}"
  Delete "$DESKTOP\Teacher's Teammate.lnk"

  ; Packages and cache are always written to the current user's LocalAppData,
  ; even on system-wide installs — so switch context back before deleting them.
  SetShellVarContext current

  ; Delete addon packages if the user selected the checkbox.
  ${If} $DoDelPackages == ${BST_CHECKED}
    RMDir /r "$LOCALAPPDATA\teachers_teammate\packages"
  ${EndIf}

  ; Delete remaining application cache and settings if the user selected the checkbox.
  ${If} $DoDelCache == ${BST_CHECKED}
    RMDir /r "$LOCALAPPDATA\teachers_teammate"
  ${EndIf}

  ; Remove registry entries from the correct hive.
  ${If} $UninstScope == "all"
    DeleteRegKey HKLM "${UNINST_KEY}"
    DeleteRegKey HKLM "${REG_KEY}"
  ${Else}
    DeleteRegKey HKCU "${UNINST_KEY}"
    DeleteRegKey HKCU "${REG_KEY}"
  ${EndIf}
SectionEnd
