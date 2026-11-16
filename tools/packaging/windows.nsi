; NSIS installer script for Teacher's Teammate
; Build from repo root, in two passes (the second one needs the first one's output):
;   makensis /DBUILD_UNINSTALLER tools/packaging/windows.nsi   ; writes Uninstall.exe
;   Start-Process pyinstaller-work\uninstaller-helper.exe -Wait ; ... by running the helper
;   makensis /DAPP_VERSION="1.2.3" /DAPP_VERSION_WIN="1.2.3.0" tools/packaging/windows.nsi
;
; Prerequisites:
;   * Binaries must already be in dist\ (run tools/build/build_standalone.py first)
;   * NSIS >= 3.0  (https://nsis.sourceforge.io/)
;   * NSIS plugins (all bundled with NSIS): LogicLib, nsDialogs, System, FileFunc, WinVer, WordFunc
;
; Two scopes, one script. A per-user install lives in %LOCALAPPDATA%\Programs and needs no
; rights; an all-users install lives in Program Files and needs administrator rights. The
; installer starts unelevated and asks for elevation only when the chosen scope needs it, so
; a teacher without an administrator account can still install.
;
; An update never runs the old uninstaller: it removes the old program files itself, inside
; the install section, and leaves every registry value and every piece of user data alone.
; docs/deployment.md lists the command line and the exit codes.

Unicode true
SetCompressor /SOLID lzma

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

; What the Welcome page says about the one thing the app sends on its own: the update check.
; The signing terms want the disclosure shown during installation, with the way to turn it off.
!define PRIVACY_NOTE "$\r$\n$\r$\nThe app asks GitHub once at every start whether a newer version exists; you can switch that off under Settings > General. What it sends, and when:$\r$\n${APP_URL}/blob/main/docs/code_signing_policy.md"

Name    "${APP_NAME} ${APP_VERSION}"

; ── Two passes: the uninstaller is a file of its own ─────────────────────────
; A remote signer cannot sign inside makensis (!uninstfinalize needs a local signing tool), so
; Uninstall.exe has to exist before the setup is compiled and be packed with File instead of
; being written by WriteUninstaller.  Pass 1 (/DBUILD_UNINSTALLER) compiles a helper that only
; writes the uninstaller and quits; pass 2 compiles the setup.  Nothing is signed yet, so the
; setup packs the unsigned helper output: the flow is the same with and without signing.
; https://nsis.sourceforge.io/Signing_an_Uninstaller
; Where the helper writes itself, where it puts the uninstaller, and where the setup packs
; that file from.  The first two are runtime paths ($EXEDIR is the helper's own folder, so a
; hand-run does not depend on the folder it is started from); the third is a compile-time
; path, relative to this script, and both passes need it: pass 1 for the helper, pass 2 for
; File.  The defaults name the same file; the workflow passes one absolute path for pass 1.
!ifndef UNINSTALLER_OUT
  !define UNINSTALLER_OUT "..\..\pyinstaller-work\Uninstall.exe"
!endif
!ifdef BUILD_UNINSTALLER
  !ifndef UNINSTALLER_HELPER
    !define UNINSTALLER_HELPER "..\..\pyinstaller-work\uninstaller-helper.exe"
  !endif
  !ifndef UNINSTALLER_TARGET
    !define UNINSTALLER_TARGET "$EXEDIR\Uninstall.exe"
  !endif
  OutFile "${UNINSTALLER_HELPER}"
!else
  OutFile "..\..\dist\teachers-teammate-${APP_VERSION}-w64-setup.exe"
!endif

; Default install directory: a sentinel. NSIS puts /D=<folder> into $INSTDIR before .onInit and
; removes it from the command line, so the only way to know whether the caller gave a folder
; is that $INSTDIR is no longer the sentinel. .onInit sets the real default for the scope.
; It ends in \TeachersTeammate on purpose: Browse on the folder page appends the last part of
; this compile-time path to the folder the teacher picks.
!define DEFAULT_DIR_SENTINEL "C:\__tt_default_folder__\TeachersTeammate"
InstallDir "${DEFAULT_DIR_SENTINEL}"

; Start without elevation. UAC is only triggered when the user explicitly
; chooses "Install for all users" on the scope page (see ScopePageLeave).
RequestExecutionLevel user

; ── Modern UI ───────────────────────────────────────────────────────────────

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"
!include "WinVer.nsh"
!include "WordFunc.nsh"

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
Var Mode             ; "fresh" or "update"

; What is already on this computer (empty when nothing is installed)
Var FoundAll         ; install folder of an all-users copy
Var FoundUser        ; install folder of a per-user copy
Var VerAll           ; its DisplayVersion
Var VerUser

; Upgrade detection
Var PreviousVersion     ; DisplayVersion of the copy being updated, for the text
Var PreviousVersionNum  ; the same, numbers only, for the comparison
Var PrevLabel           ; "Teacher's Teammate 1.2.3", or "An earlier version of Teacher's Teammate"

; Command line
Var CmdScope            ; "/SCOPE=" value, "" when not given
Var CmdNoDesktop        ; "1" when /NODESKTOP was given
Var CmdAllowDowngrade   ; "1" when /ALLOWDOWNGRADE was given
Var DirGiven            ; "1" when /D= was given, so $INSTDIR is not ours to set

; Pages
Var WelcomeText
Var SkipWelcome         ; "1" in the elevated copy: no Welcome, no Scope page
Var DesktopShortcut     ; "1" when a desktop shortcut is wanted
Var ScopeDialog
Var ScopeAllUsers
Var ScopeCurrentUser
Var ScopeDesktop

; The app-still-open check
Var CheckDir
Var CheckHandle

; Uninstaller cleanup page variables
Var UnDelPackages    ; checkbox control handle for packages directory
Var UnDelCache       ; checkbox control handle for cache/settings directory
Var DoDelPackages    ; BST_CHECKED / BST_UNCHECKED — user's choice
Var DoDelCache       ; BST_CHECKED / BST_UNCHECKED — user's choice

; ── Macros ───────────────────────────────────────────────────────────────────

; shell32::IsUserAnAdmin is true only for an elevated token, which is exactly the question
; both the installer and the uninstaller ask. NSIS needs a separate un. function.
!macro DefineIsElevated FUNC
Function ${FUNC}
  System::Call 'shell32::IsUserAnAdmin() i.r0'
  ${If} $0 <> 0
    StrCpy $IsAdmin "1"
  ${Else}
    StrCpy $IsAdmin "0"
  ${EndIf}
FunctionEnd
!macroend
; One variant per pass: the setup asks the question in .onInit, the uninstaller in un.onInit.
!ifdef BUILD_UNINSTALLER
  !insertmacro DefineIsElevated "un.IsElevated"
!else
  !insertmacro DefineIsElevated "IsElevated"
!endif

; A running exe cannot be opened for writing on Windows, and that holds for every version,
; including the shipped ones, which have no instance lock. $CheckDir is the folder to test.
!macro DefineCheckAppClosed FUNC ID
Function ${FUNC}
  ${IfNot} ${FileExists} "$CheckDir\teachers-teammate.exe"
    Return
  ${EndIf}
  retry${ID}:
  ClearErrors
  FileOpen $CheckHandle "$CheckDir\teachers-teammate.exe" a
  ${IfNot} ${Errors}
    FileClose $CheckHandle
    Return
  ${EndIf}
  ${If} ${Silent}
    SetErrorLevel 10
    Quit
  ${EndIf}
  MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION \
    "Teacher's Teammate is still open. Close it, also its System Monitor and Benchmark windows, then click Retry." \
    IDRETRY retry${ID}
  Quit
FunctionEnd
!macroend
!ifdef BUILD_UNINSTALLER
  !insertmacro DefineCheckAppClosed "un.CheckAppClosed" "U"
!else
  !insertmacro DefineCheckAppClosed "CheckAppClosed" "I"
!endif

; Remove the files this installer put there, and the folder only when nothing else is in it.
; A teacher's own file in the install folder survives an update and an uninstall.
!macro RemoveProgramFiles DIR
  RMDir /r "${DIR}\_internal"
  Delete "${DIR}\teachers-teammate.exe"
  Delete "${DIR}\NOTICE.txt"
  Delete "${DIR}\third_party_license.txt"
  Delete "${DIR}\Uninstall.exe"
  RMDir "${DIR}"
!macroend

; The three values the installer owns. Qt's own subkey keeps the parent key alive.
!macro RemoveInstallValues HIVE
  DeleteRegKey   ${HIVE} "${UNINST_KEY}"
  DeleteRegValue ${HIVE} "${REG_KEY}" "InstallDir"
  DeleteRegValue ${HIVE} "${REG_KEY}" "Version"
  DeleteRegValue ${HIVE} "${REG_KEY}" "Scope"
  DeleteRegKey /ifempty ${HIVE} "${REG_KEY}"
!macroend

; Start this installer again with administrator rights. The result lands in $0: > 32 means the
; elevated copy started, anything else means UAC was refused or cancelled.
!macro RelaunchElevated ARGS
  System::Call 'shell32::ShellExecuteW(i $HWNDPARENT, w "runas", w "$EXEPATH", w "${ARGS}", w "$EXEDIR", i 1) i.r0'
!macroend

; ── Installer pages ──────────────────────────────────────────────────────────

!ifndef BUILD_UNINSTALLER
!define MUI_PAGE_CUSTOMFUNCTION_PRE WelcomePagePre
; .onInit works out the text: fresh install, update, reinstall or downgrade.
!define MUI_WELCOMEPAGE_TEXT "$WelcomeText"
!insertmacro MUI_PAGE_WELCOME

Page custom ScopePageCreate ScopePageLeave

!define MUI_PAGE_CUSTOMFUNCTION_PRE DirectoryPagePre
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE DirectoryPageLeave
!insertmacro MUI_PAGE_DIRECTORY

!insertmacro MUI_PAGE_INSTFILES

!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Start Teacher's Teammate"
!define MUI_FINISHPAGE_RUN_FUNCTION StartAppUnelevated
!insertmacro MUI_PAGE_FINISH
!endif

; ── Uninstaller pages ────────────────────────────────────────────────────────

!ifdef BUILD_UNINSTALLER
!insertmacro MUI_UNPAGE_CONFIRM
UninstPage custom un.UnDataPageCreate un.UnDataPageLeave
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH
!endif

!insertmacro MUI_LANGUAGE "English"

; ── Version info embedded in the exe ────────────────────────────────────────
; Both passes carry this block: NSIS copies the installer it runs into the uninstaller and
; replaces only the icon and the installation data, so the uninstaller gets these keys from
; the helper that wrote it.  ProductName and ProductVersion are the ones the app exe's version
; resource carries (tools/build/make_version_resource.py), which a code-signing service checks.

VIProductVersion "${APP_VERSION_WIN}"
VIAddVersionKey "ProductName"     "${APP_NAME}"
VIAddVersionKey "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey "CompanyName"     "${APP_PUBLISHER}"
VIAddVersionKey "FileVersion"     "${APP_VERSION}"
!ifdef BUILD_UNINSTALLER
  VIAddVersionKey "FileDescription" "Uninstaller for ${APP_NAME}"
!else
  VIAddVersionKey "FileDescription" "Installer for ${APP_NAME}"
!endif
VIAddVersionKey "LegalCopyright"  "${U+00A9} 2026 Teacher's Teammate contributors ${U+00B7} Apache-2.0"

; ── Initialisation ───────────────────────────────────────────────────────────

Function .onInit
!ifdef BUILD_UNINSTALLER
  ; Pass 1: write the uninstaller and quit.  Nothing else of this script is compiled into the
  ; helper, so it stays small and cannot touch the build machine.
  SetSilent silent
  WriteUninstaller "${UNINSTALLER_TARGET}"
  SetErrorLevel 0
  Quit
!else

  ; Windows 10 (1809) is the floor: Qt 6.12 and Python 3.14 need it. ${AtLeastWin10} would let
  ; 1507-1803 through, so the build number is checked. A silent run exits 15.
  ${IfNot} ${AtLeastBuild} 17763
    ${If} ${Silent}
      SetErrorLevel 15
      Quit
    ${EndIf}
    MessageBox MB_OK|MB_ICONSTOP "Teacher's Teammate needs Windows 10 (version 1809 or newer) or Windows 11."
    SetErrorLevel 15
    Quit
  ${EndIf}

  Call IsElevated

  ${If} $INSTDIR == "${DEFAULT_DIR_SENTINEL}"
    StrCpy $DirGiven "0"
  ${Else}
    StrCpy $DirGiven "1"
  ${EndIf}

  StrCpy $Mode "fresh"
  StrCpy $CmdScope ""
  StrCpy $CmdNoDesktop "0"
  StrCpy $CmdAllowDowngrade "0"
  StrCpy $SkipWelcome "0"
  StrCpy $DesktopShortcut "1"
  StrCpy $WelcomeText ""
  StrCpy $PreviousVersion ""
  StrCpy $PreviousVersionNum "0.0.0"

  ; ── the command line ──────────────────────────────────────────────────────
  ${GetParameters} $R0
  ${GetOptions} $R0 "/SCOPE=" $R1
  ${IfNot} ${Errors}
    ${If} $R1 == "user"
      StrCpy $CmdScope "user"
    ${ElseIf} $R1 == "all"
      StrCpy $CmdScope "all"
    ${EndIf}
  ${EndIf}
  ${GetOptions} $R0 "/NODESKTOP" $R1
  ${IfNot} ${Errors}
    StrCpy $CmdNoDesktop "1"
    StrCpy $DesktopShortcut "0"
  ${EndIf}
  ${GetOptions} $R0 "/ALLOWDOWNGRADE" $R1
  ${IfNot} ${Errors}
    StrCpy $CmdAllowDowngrade "1"
  ${EndIf}
  ${GetOptions} $R0 "/ELEVATED" $R1
  ${IfNot} ${Errors}
    StrCpy $SkipWelcome "1"
  ${EndIf}

  ; ── what is already installed ─────────────────────────────────────────────
  ; A copy counts only when its exe is still there: a leftover registry value from a
  ; half-finished uninstall must not turn a fresh install into an update.
  StrCpy $FoundAll ""
  StrCpy $FoundUser ""
  ReadRegStr $0 HKLM "${REG_KEY}" "InstallDir"
  ${If} $0 != ""
  ${AndIf} ${FileExists} "$0\teachers-teammate.exe"
    StrCpy $FoundAll $0
    ReadRegStr $VerAll HKLM "${UNINST_KEY}" "DisplayVersion"
  ${EndIf}
  ReadRegStr $0 HKCU "${REG_KEY}" "InstallDir"
  ${If} $0 != ""
  ${AndIf} ${FileExists} "$0\teachers-teammate.exe"
    StrCpy $FoundUser $0
    ReadRegStr $VerUser HKCU "${UNINST_KEY}" "DisplayVersion"
  ${EndIf}

  ; ── decide the mode ───────────────────────────────────────────────────────
  ${If} $FoundAll == ""
    ${If} $FoundUser == ""
      ; Nothing installed: a fresh install in the scope the command line or the rights give.
      ${If} $CmdScope == "all"
        StrCpy $InstallScope "all"
      ${ElseIf} $CmdScope == "user"
        StrCpy $InstallScope "user"
      ${ElseIf} $IsAdmin == "1"
        StrCpy $InstallScope "all"
      ${Else}
        StrCpy $InstallScope "user"
      ${EndIf}
      ${If} $InstallScope == "all"
      ${AndIf} $IsAdmin == "0"
        ${If} ${Silent}
          SetErrorLevel 14
          Quit
        ${EndIf}
        ; Interactive: the scope page offers the relaunch (see ScopePageLeave).
      ${EndIf}
      ${If} $InstallScope == "all"
        SetShellVarContext all
        ${If} $DirGiven == "0"
          StrCpy $INSTDIR "$PROGRAMFILES64\TeachersTeammate"
        ${EndIf}
      ${Else}
        SetShellVarContext current
        ${If} $DirGiven == "0"
          StrCpy $INSTDIR "$LOCALAPPDATA\Programs\TeachersTeammate"
        ${EndIf}
      ${EndIf}
    ${Else}
      ; Only a personal copy: update it in place.
      ${If} $CmdScope == "all"
        ${IfNot} ${Silent}
          MessageBox MB_OK|MB_ICONSTOP \
            "Teacher's Teammate is installed only for you. Installing it for all users as well would create a second copy. Remove it first, or start this installer without /SCOPE=all."
        ${EndIf}
        SetErrorLevel 13
        Quit
      ${EndIf}
      StrCpy $Mode "update"
      StrCpy $InstallScope "user"
      SetShellVarContext current
      StrCpy $INSTDIR $FoundUser
      StrCpy $PreviousVersion $VerUser
    ${EndIf}
  ${Else}
    ${If} $FoundUser == ""
      ; Only an all-users copy.
      ${If} $CmdScope == "user"
        ${IfNot} ${Silent}
          MessageBox MB_OK|MB_ICONSTOP \
            "Teacher's Teammate is installed for all users. Installing it only for you as well would create a second copy. Remove it first, or start this installer without /SCOPE=user."
        ${EndIf}
        SetErrorLevel 13
        Quit
      ${EndIf}
      ${If} $IsAdmin == "0"
        ${If} ${Silent}
          SetErrorLevel 14
          Quit
        ${EndIf}
        MessageBox MB_OKCANCEL|MB_ICONINFORMATION \
          "Teacher's Teammate is installed for all users. Updating it needs administrator rights.$\r$\n$\r$\nClick OK to continue with administrator rights, or Cancel to quit." \
          /SD IDOK IDOK elevate_update
        Quit
        elevate_update:
          StrCpy $R1 "/SCOPE=all /ELEVATED"
          !insertmacro RelaunchElevated "$R1"
          ${If} $0 <= 32
            MessageBox MB_OK|MB_ICONSTOP "Teacher's Teammate needs administrator rights to update the all-users copy. Nothing was changed."
          ${EndIf}
          Quit
      ${EndIf}
      StrCpy $Mode "update"
      StrCpy $InstallScope "all"
      SetShellVarContext all
      StrCpy $INSTDIR $FoundAll
      StrCpy $PreviousVersion $VerAll
    ${Else}
      ; Both scopes. A silent run names the copy with /SCOPE=; an interactive one stops.
      ${If} ${Silent}
        ${If} $CmdScope == "all"
          StrCpy $Mode "update"
          StrCpy $InstallScope "all"
          SetShellVarContext all
          StrCpy $INSTDIR $FoundAll
          StrCpy $PreviousVersion $VerAll
        ${ElseIf} $CmdScope == "user"
          StrCpy $Mode "update"
          StrCpy $InstallScope "user"
          SetShellVarContext current
          StrCpy $INSTDIR $FoundUser
          StrCpy $PreviousVersion $VerUser
        ${Else}
          SetErrorLevel 13
          Quit
        ${EndIf}
      ${Else}
        ; Two copies only exist from before this installer looked in both places. Do not
        ; guess which one to keep: name both and let the teacher remove one.
        MessageBox MB_OK|MB_ICONEXCLAMATION \
          "Teacher's Teammate is installed twice on this computer: for all users in$\r$\n$FoundAll$\r$\nand for you in$\r$\n$FoundUser.$\r$\n$\r$\nRemove one of them in Windows Settings > Apps, then run this setup again."
        Quit
      ${EndIf}
    ${EndIf}
  ${EndIf}

  ; ── a folder that holds somebody else's files (a silent run has no folder page) ──
  ${If} ${Silent}
  ${AndIf} $Mode == "fresh"
    Call FolderIsOurs
    ${If} $0 == "0"
      SetErrorLevel 12
      Quit
    ${EndIf}
  ${EndIf}

  ; ── the version, and the text the Welcome page shows ──────────────────────
  ${If} $Mode == "fresh"
    StrCpy $WelcomeText "This installs ${APP_NAME} ${APP_VERSION} on this computer.$\r$\n$\r$\nIn the next step you choose whether it is installed for all users or only for you."
  ${Else}
    ${If} $PreviousVersion == ""
      StrCpy $PrevLabel "An earlier version of ${APP_NAME}"
    ${Else}
      StrCpy $PrevLabel "${APP_NAME} $PreviousVersion"
      StrCpy $R0 $PreviousVersion
      Call StripPrerelease
      StrCpy $PreviousVersionNum $R0
    ${EndIf}
    ; Compare numbers only: 0.6.0-beta.1 counts as 0.6.0, on both sides.
    StrCpy $R0 "${APP_VERSION}"
    Call StripPrerelease
    ${VersionCompare} "$PreviousVersionNum" "$R0" $R3
    ${If} $R3 == "0"
      ${If} $PreviousVersion == "${APP_VERSION}"
        StrCpy $WelcomeText "${APP_NAME} ${APP_VERSION} is already installed. This reinstalls it. Your settings, add-ons and downloaded models stay."
      ${Else}
        StrCpy $WelcomeText "$PrevLabel is installed. This replaces it with ${APP_VERSION}. Your settings, add-ons and downloaded models stay."
      ${EndIf}
    ${ElseIf} $R3 == "1"
      ${If} ${Silent}
        ${If} $CmdAllowDowngrade != "1"
          SetErrorLevel 11
          Quit
        ${EndIf}
      ${Else}
        MessageBox MB_YESNO|MB_ICONEXCLAMATION \
          "Version $PreviousVersion is installed, which is newer than ${APP_VERSION}. Install the older version?" \
          /SD IDNO IDYES downgrade_ok
        Quit
        downgrade_ok:
      ${EndIf}
      StrCpy $WelcomeText "Version $PreviousVersion is installed, which is newer than ${APP_VERSION}. This replaces it with the older version. Your settings, add-ons and downloaded models stay."
    ${Else}
      StrCpy $WelcomeText "$PrevLabel is installed. This updates it to ${APP_VERSION}. Your settings, add-ons and downloaded models stay."
    ${EndIf}
  ${EndIf}

  ; The disclosure the signing terms ask for: shown during installation, with the switch that
  ; turns the check off.  One sentence for every case, so an update says it too.
  StrCpy $WelcomeText "$WelcomeText${PRIVACY_NOTE}"
!endif
FunctionEnd

; Cut a version string at its first "-": 0.6.0-beta.1 → 0.6.0. In: $R0. Out: $R0.
!ifndef BUILD_UNINSTALLER
Function StripPrerelease
  StrCpy $R7 0
  ${Do}
    StrCpy $R8 $R0 1 $R7
    ${If} $R8 == ""
      ${ExitDo}
    ${EndIf}
    ${If} $R8 == "-"
      StrCpy $R0 $R0 $R7
      ${ExitDo}
    ${EndIf}
    IntOp $R7 $R7 + 1
  ${Loop}
FunctionEnd

; ── Pages ────────────────────────────────────────────────────────────────────

Function WelcomePagePre
  ${If} $SkipWelcome == "1"
    Abort
  ${EndIf}
FunctionEnd

Function DirectoryPagePre
  ${If} $Mode == "update"
    Abort
  ${EndIf}
FunctionEnd

; Is $INSTDIR ours to install into? $0 = "1" when it is missing, empty, or already holds
; teachers-teammate.exe; "0" when it holds somebody else's files.
Function FolderIsOurs
  StrCpy $0 "1"
  ${IfNot} ${FileExists} "$INSTDIR\teachers-teammate.exe"
    ; ${DirState}: 0 = empty, 1 = not empty, -1 = does not exist
    ${DirState} "$INSTDIR" $1
    ${If} $1 == 1
      StrCpy $0 "0"
    ${EndIf}
  ${EndIf}
FunctionEnd

; The folder page. A silent run skips page callbacks, so .onInit makes the same check.
Function DirectoryPageLeave
  Call FolderIsOurs
  ${If} $0 == "0"
    MessageBox MB_OK|MB_ICONEXCLAMATION \
      "This folder already contains other files. Choose an empty folder or a new one."
    Abort
  ${EndIf}
FunctionEnd

Function ScopePageCreate
  ; The elevated copy was started with the scope already chosen.
  ${If} $Mode == "update"
  ${OrIf} $SkipWelcome == "1"
    Abort
  ${EndIf}

  nsDialogs::Create 1018
  Pop $ScopeDialog
  ${If} $ScopeDialog == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 280u 16u \
    "Choose how ${APP_NAME} should be installed:"
  Pop $0

  ${NSD_CreateRadioButton} 10u 22u 270u 14u \
    "Install for all users  (Program Files - requires administrator rights)"
  Pop $ScopeAllUsers

  ${NSD_CreateRadioButton} 10u 40u 270u 14u \
    "Install only for me  (AppData - no administrator rights required)"
  Pop $ScopeCurrentUser

  ${NSD_CreateLabel} 10u 60u 270u 24u \
    "Settings and downloads (models, add-ons) are kept separately for each Windows user."
  Pop $0

  ${NSD_CreateCheckBox} 10u 90u 270u 14u "Put a shortcut on the desktop"
  Pop $ScopeDesktop
  ${If} $DesktopShortcut == "1"
    ${NSD_Check} $ScopeDesktop
  ${EndIf}

  ; Pre-select based on current scope (set in .onInit).
  ${If} $InstallScope == "all"
    ${NSD_Check} $ScopeAllUsers
  ${Else}
    ${NSD_Check} $ScopeCurrentUser
  ${EndIf}

  nsDialogs::Show
FunctionEnd

Function ScopePageLeave
  ${NSD_GetState} $ScopeDesktop $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $DesktopShortcut "1"
  ${Else}
    StrCpy $DesktopShortcut "0"
  ${EndIf}

  ${NSD_GetState} $ScopeAllUsers $0
  ${If} $0 == ${BST_CHECKED}
    ${If} $IsAdmin == "0"
      ; Not elevated: start this installer again with administrator rights. The elevated copy
      ; skips Welcome and Scope and goes straight to the folder page.
      StrCpy $R1 "/SCOPE=all /ELEVATED"
      ${If} $DesktopShortcut == "0"
        StrCpy $R1 "/SCOPE=all /ELEVATED /NODESKTOP"
      ${EndIf}
      ; /D= must be the last argument, and it is not quoted.
      ${If} $DirGiven == "1"
        StrCpy $R1 "$R1 /D=$INSTDIR"
      ${EndIf}
      !insertmacro RelaunchElevated "$R1"
      ${If} $0 > 32
        Quit
      ${EndIf}
      MessageBox MB_OK|MB_ICONSTOP \
        "Installing for all users needs administrator rights. Choose 'Install only for me', or run the installer again as an administrator."
      Abort
    ${EndIf}
    StrCpy $InstallScope "all"
    SetShellVarContext all
    ${If} $DirGiven == "0"
      StrCpy $INSTDIR "$PROGRAMFILES64\TeachersTeammate"
    ${EndIf}
  ${Else}
    StrCpy $InstallScope "user"
    SetShellVarContext current
    ${If} $DirGiven == "0"
      StrCpy $INSTDIR "$LOCALAPPDATA\Programs\TeachersTeammate"
    ${EndIf}
  ${EndIf}
FunctionEnd

; Explorer starts the app as the signed-in user, never elevated, even after an
; over-the-shoulder elevation.
Function StartAppUnelevated
  Exec '"$WINDIR\explorer.exe" "$INSTDIR\teachers-teammate.exe"'
FunctionEnd
!endif

; ── Install sections ─────────────────────────────────────────────────────────

!ifndef BUILD_UNINSTALLER
Section "Core (required)" SecCore
  SectionIn RO

  StrCpy $CheckDir $INSTDIR
  Call CheckAppClosed

  ; An update removes the old program files itself; the old uninstaller is never run, so
  ; nothing it might delete can be lost.
  ${If} $Mode == "update"
    !insertmacro RemoveProgramFiles "$INSTDIR"
  ${EndIf}

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

  ; The uninstaller pass 1 wrote, packed as a file: a remote signer can sign it before the
  ; setup is built, and the setup is the same whether or not it was signed.
  File /oname=Uninstall.exe "${UNINSTALLER_OUT}"

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
  ; On an update, leave an existing shortcut alone and do not create a missing one: the
  ; teacher may have deleted it on purpose.
  ${If} $Mode == "fresh"
  ${AndIf} $DesktopShortcut == "1"
    CreateShortcut "$DESKTOP\Teacher's Teammate.lnk" "$INSTDIR\teachers-teammate.exe"
  ${EndIf}
SectionEnd
!endif

; ── Uninstaller data-cleanup page ────────────────────────────────────────────

!ifdef BUILD_UNINSTALLER
; NSIS refuses a script without an install section, although .onInit quits before one runs.
Section "-Helper"
SectionEnd

Function un.UnDataPageCreate
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  ; The elevated run may be another account's (a second administrator approving the prompt).
  ; Its user data is then that account's, so the page names the account it will remove.
  ReadEnvStr $R0 "USERNAME"

  ${NSD_CreateLabel} 0 0 280u 20u \
    "Choose what else to remove for the Windows account $\"$R0$\":"
  Pop $0

  ${NSD_CreateCheckBox} 10u 26u 270u 14u "Downloaded add-ons and model files"
  Pop $UnDelPackages
  ${NSD_Check} $UnDelPackages

  ${NSD_CreateLabel} 22u 40u 258u 24u \
    "(built-in reader, page models, name detection, packages for online AI services)"
  Pop $0

  ${NSD_CreateCheckBox} 10u 70u 270u 14u "Settings, API keys, benchmark history and results cache"
  Pop $UnDelCache
  ; unchecked by default — user may want to keep settings if reinstalling

  ${NSD_CreateLabel} 10u 94u 270u 40u \
    "AI models you downloaded in Ollama are kept: they belong to Ollama.$\r$\nRemove Ollama itself in Windows Settings > Apps."
  Pop $0

  nsDialogs::Show
FunctionEnd

Function un.UnDataPageLeave
  ${NSD_GetState} $UnDelPackages $DoDelPackages
  ${NSD_GetState} $UnDelCache    $DoDelCache
FunctionEnd

; ── Uninstaller ──────────────────────────────────────────────────────────────

; The scope is the one whose install folder this uninstaller sits in. HKLM no longer wins
; just because it exists: a per-user copy next to an all-users copy must remove itself.
Function un.DetectScope
  StrCpy $UninstScope "user"
  StrCpy $R0 $INSTDIR
  StrCpy $R1 $R0 1 -1
  ${If} $R1 == "\"
    StrCpy $R0 $R0 -1
  ${EndIf}
  ReadRegStr $R1 HKLM "${REG_KEY}" "InstallDir"
  ${If} $R1 != ""
    StrCpy $R2 $R1 1 -1
    ${If} $R2 == "\"
      StrCpy $R1 $R1 -1
    ${EndIf}
    ${If} $R1 == $R0
      StrCpy $UninstScope "all"
    ${EndIf}
  ${EndIf}
FunctionEnd

Function un.onInit
  Call un.IsElevated
  Call un.DetectScope

  ; An all-users copy needs one elevated run. The elevated copy finds itself elevated and
  ; carries on; the unelevated one is done.
  ${If} $UninstScope == "all"
  ${AndIf} $IsAdmin == "0"
    ${If} ${Silent}
      SetErrorLevel 14
      Quit
    ${EndIf}
    ${GetParameters} $R0
    System::Call 'shell32::ShellExecuteW(i $HWNDPARENT, w "runas", w "$INSTDIR\Uninstall.exe", w "$R0", w "$INSTDIR", i 1) i.r0'
    ${If} $0 <= 32
      MessageBox MB_OK|MB_ICONSTOP "Removing the program for all users needs administrator rights."
    ${EndIf}
    Quit
  ${EndIf}

  ; A silent uninstall keeps every piece of user data unless it is asked for.
  ${If} ${Silent}
    StrCpy $DoDelPackages ${BST_UNCHECKED}
    StrCpy $DoDelCache    ${BST_UNCHECKED}
    ${GetParameters} $R0
    ${GetOptions} $R0 "/DELDATA" $R1
    ${IfNot} ${Errors}
      StrCpy $DoDelPackages ${BST_CHECKED}
    ${EndIf}
    ${GetOptions} $R0 "/DELSETTINGS" $R1
    ${IfNot} ${Errors}
      StrCpy $DoDelCache ${BST_CHECKED}
    ${EndIf}
  ${EndIf}
FunctionEnd

Section "Uninstall"
  ${If} $UninstScope == "all"
    SetShellVarContext all
  ${Else}
    SetShellVarContext current
  ${EndIf}

  StrCpy $CheckDir $INSTDIR
  Call un.CheckAppClosed

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
    ; The built-in reader's model files (domain/local_models.py) live next to the packages.
    RMDir /r "$LOCALAPPDATA\teachers_teammate\models"
  ${EndIf}

  ; Delete settings and cache if the user selected the checkbox. The API keys live in the
  ; Windows Credential Manager, which no uninstaller can enumerate: the app deletes them.
  ${If} $DoDelCache == ${BST_CHECKED}
    ExecWait '"$INSTDIR\teachers-teammate.exe" --_forget_keys'
    DeleteRegKey HKCU "Software\TeachersTeammate\TeachersTeammate"
    ; An all-users uninstall does not touch HKCU below; do not leave the empty parent behind.
    DeleteRegKey /ifempty HKCU "${REG_KEY}"
    RMDir /r "$LOCALAPPDATA\teachers_teammate\Cache"
    ; The settings file, the benchmark history and the speed data sit beside packages\ and
    ; models\ (the config and data folders are the same on Windows). Earlier versions kept them
    ; in Cache\, which the line above already removed.
    Delete "$LOCALAPPDATA\teachers_teammate\ocr.toml"
    RMDir /r "$LOCALAPPDATA\teachers_teammate\benchmark"
    RMDir /r "$LOCALAPPDATA\teachers_teammate\speed"
    RMDir "$LOCALAPPDATA\teachers_teammate"
  ${EndIf}

  ; Remove program files, then the registry values of the scope we are removing.
  !insertmacro RemoveProgramFiles "$INSTDIR"

  ${If} $UninstScope == "all"
    !insertmacro RemoveInstallValues HKLM
  ${Else}
    !insertmacro RemoveInstallValues HKCU
  ${EndIf}
SectionEnd
!endif
