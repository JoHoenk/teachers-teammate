<#
.SYNOPSIS
    Installs, updates and uninstalls Teacher's Teammate and checks what is left behind.

.DESCRIPTION
    Runs the NSIS installer the way a teacher and an IT department do: a fresh per-user
    install, an update over it, an install while the app is open, an uninstall that keeps
    the data, an uninstall that removes it, a folder that belongs to somebody else, and the
    two scopes side by side. Every step asserts on the registry, the folders and the exit
    code, so a regression in tools/packaging/windows.nsi fails the build instead of a
    teacher's computer.

    The runner must be elevated (GitHub's windows-latest is), which is why the per-user runs
    pass /SCOPE=user: without it an elevated installer would default to all users.

.PARAMETER Setup
    The installer to test, e.g. dist\teachers-teammate-1.2.3-w64-setup.exe.

.PARAMETER Version
    The version the installer was built with, e.g. 1.2.3.

.PARAMETER SkipOldVersion
    Skip the update-from-an-older-release step (it downloads v0.5.6 from GitHub).

.EXAMPLE
    pwsh -File tools/packaging/test_windows_installer.ps1 `
        -Setup dist\teachers-teammate-1.2.3-w64-setup.exe -Version 1.2.3
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [Parameter(Mandatory = $true)][string]$Version,
    [switch]$SkipOldVersion
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Setup = (Resolve-Path $Setup).Path

$AppName = "Teacher's Teammate"
$RegKey = 'Software\TeachersTeammate'
$UninstKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\TeachersTeammate'
# The installer is a 32-bit process and does not call SetRegView, so an all-users install
# writes into the 32-bit view of HKLM.
$UninstKey32 = 'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\TeachersTeammate'
$RegKey32 = 'SOFTWARE\WOW6432Node\TeachersTeammate'

$UserDir = Join-Path $env:LOCALAPPDATA 'Programs\TeachersTeammate'
$AllUsersDir = Join-Path $env:ProgramFiles 'TeachersTeammate'
$DataDir = Join-Path $env:LOCALAPPDATA 'teachers_teammate'
$QtKey = 'HKCU:\Software\TeachersTeammate\TeachersTeammate'

$script:Step = 0

function Write-Step([string]$Text) {
    $script:Step++
    Write-Host ''
    Write-Host "── $($script:Step). $Text" -ForegroundColor Cyan
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw "FAILED: $Message"
    }
    Write-Host "   ok  $Message" -ForegroundColor DarkGray
}

function Assert-Equal($Expected, $Actual, [string]$Message) {
    if ("$Expected" -ne "$Actual") {
        throw "FAILED: $Message (expected '$Expected', got '$Actual')"
    }
    Write-Host "   ok  $Message" -ForegroundColor DarkGray
}

function Assert-Missing([string]$Path, [string]$Message) {
    Assert-True (-not (Test-Path -LiteralPath $Path)) $Message
}

function Assert-Present([string]$Path, [string]$Message) {
    Assert-True (Test-Path -LiteralPath $Path) $Message
}

function Invoke-Setup {
    <# Run the installer silently and return its exit code. #>
    param([string[]]$Arguments = @())
    $all = @('/S') + $Arguments
    $process = Start-Process -FilePath $Setup -ArgumentList $all -Wait -PassThru
    return $process.ExitCode
}

function Invoke-Uninstaller {
    <# Run the installed uninstaller silently and wait until it has finished. #>
    param([string]$Directory, [string[]]$Arguments = @(), [string]$AppsKey = "HKCU:\$UninstKey")
    $uninstaller = Join-Path $Directory 'Uninstall.exe'
    if (-not (Test-Path -LiteralPath $uninstaller)) {
        throw "FAILED: no uninstaller in $Directory"
    }
    $all = @('/S') + $Arguments
    Start-Process -FilePath $uninstaller -ArgumentList $all -Wait | Out-Null
    # An NSIS uninstaller copies itself to %TEMP% and returns at once. It removes the program
    # files first and the registry values last, so wait for both before the caller asserts.
    for ($i = 0; $i -lt 120; $i++) {
        $filesGone = -not (Test-Path -LiteralPath (Join-Path $Directory 'teachers-teammate.exe'))
        $entryGone = -not (Test-Path -LiteralPath $AppsKey)
        if ($filesGone -and $entryGone) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "FAILED: the uninstaller did not finish removing $Directory"
}

function Get-UninstallValue([string]$Key, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Key)) {
        return $null
    }
    $item = Get-ItemProperty -LiteralPath $Key -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        return $null
    }
    $property = $item.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Assert-SmokeTestPasses([string]$Directory) {
    <# The installed copy must be complete: run its own smoke test. #>
    $result = Join-Path $env:TEMP "tt-smoke-$([guid]::NewGuid().ToString('N')).json"
    $process = Start-Process -FilePath (Join-Path $Directory 'teachers-teammate.exe') `
        -ArgumentList @('--_smoke_test', $result) -Wait -PassThru
    $json = if (Test-Path -LiteralPath $result) { Get-Content -LiteralPath $result -Raw } else { '' }
    Remove-Item -LiteralPath $result -Force -ErrorAction SilentlyContinue
    Assert-Equal 0 $process.ExitCode "the installed copy passes its smoke test"
    Assert-True ($json -match '"ok":\s*true') "the smoke test result says ok"
}

function Remove-Install([string]$Directory, [string]$UninstallKey) {
    <# Best effort: leave no copy behind for the next step. #>
    if (Test-Path -LiteralPath (Join-Path $Directory 'Uninstall.exe')) {
        try { Invoke-Uninstaller -Directory $Directory -Arguments @('/DELDATA', '/DELSETTINGS') -AppsKey $UninstallKey } catch { }
    }
    Remove-Item -LiteralPath $Directory -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $UninstallKey -Recurse -Force -ErrorAction SilentlyContinue
}

# ── Start from a clean slate ────────────────────────────────────────────────

Write-Step 'Clean up anything an earlier run left behind'
Remove-Install -Directory $UserDir -UninstallKey "HKCU:\$UninstKey"
Remove-Install -Directory $AllUsersDir -UninstallKey "HKLM:\$UninstKey32"
Remove-Item -LiteralPath $DataDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $QtKey -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'C:\tt-foreign' -Recurse -Force -ErrorAction SilentlyContinue

# ── 1. A fresh per-user install ─────────────────────────────────────────────

Write-Step 'Fresh per-user install'
Assert-Equal 0 (Invoke-Setup @('/SCOPE=user')) 'the installer exits 0'
Assert-Present (Join-Path $UserDir 'teachers-teammate.exe') 'the exe is under %LOCALAPPDATA%\Programs'
Assert-Equal $Version (Get-UninstallValue "HKCU:\$UninstKey" 'DisplayVersion') 'the Apps entry has the version'
Assert-Equal $UserDir (Get-UninstallValue "HKCU:\$RegKey" 'InstallDir') 'the install folder is recorded'
Assert-Equal 'user' (Get-UninstallValue "HKCU:\$RegKey" 'Scope') 'the scope is recorded'
Assert-Present (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$AppName\Teacher's Teammate.lnk") 'the Start-menu shortcut exists'
Assert-SmokeTestPasses $UserDir

# ── 2. Markers that an update must not touch ────────────────────────────────

Write-Step 'Write markers an update must keep'
New-Item -Path $QtKey -Force | Out-Null
Set-ItemProperty -LiteralPath $QtKey -Name 'ci_marker' -Value 'keep me'
foreach ($sub in @('packages', 'models', 'Cache')) {
    New-Item -ItemType Directory -Path (Join-Path $DataDir $sub) -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $DataDir "$sub\marker.txt") -Value 'keep me'
}
# The settings file, the benchmark history and the speed data (PR 7) sit beside packages\.
Set-Content -LiteralPath (Join-Path $DataDir 'ocr.toml') -Value 'keep me'
foreach ($sub in @('benchmark', 'speed')) {
    New-Item -ItemType Directory -Path (Join-Path $DataDir $sub) -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $DataDir "$sub\marker.txt") -Value 'keep me'
}

# ── 3. Update over the same install ─────────────────────────────────────────

Write-Step 'Update over the installed copy'
Assert-Equal 0 (Invoke-Setup @('/SCOPE=user')) 'the second run exits 0'
Assert-Equal 'keep me' (Get-UninstallValue $QtKey 'ci_marker') 'the Qt settings marker survives (F1)'
foreach ($sub in @('packages', 'models', 'Cache')) {
    Assert-Present (Join-Path $DataDir "$sub\marker.txt") "the $sub marker survives"
}
Assert-Present (Join-Path $DataDir 'ocr.toml') 'the settings file survives the update'
foreach ($sub in @('benchmark', 'speed')) {
    Assert-Present (Join-Path $DataDir "$sub\marker.txt") "the $sub marker survives the update"
}
Assert-Equal $Version (Get-UninstallValue "HKCU:\$UninstKey" 'DisplayVersion') 'one Apps entry, with the version'
Assert-Missing $AllUsersDir 'nothing was written to Program Files'

# ── 4. The app is still open ────────────────────────────────────────────────

Write-Step 'Update while the app is open'
$exe = Join-Path $UserDir 'teachers-teammate.exe'
$handle = [System.IO.File]::Open($exe, 'Open', 'Read', 'Read')
try {
    Assert-Equal 10 (Invoke-Setup @('/SCOPE=user')) 'the installer exits 10'
} finally {
    $handle.Dispose()
}

# ── 5. Uninstall, keeping the data ──────────────────────────────────────────

Write-Step 'Uninstall, keeping the user data'
Invoke-Uninstaller -Directory $UserDir
Assert-Missing $UserDir 'the program folder is gone'
Assert-Equal $null (Get-UninstallValue "HKCU:\$UninstKey" 'DisplayVersion') 'the Apps entry is gone'
Assert-Equal $null (Get-UninstallValue "HKCU:\$RegKey" 'InstallDir') 'the install folder value is gone'
Assert-Equal $null (Get-UninstallValue "HKCU:\$RegKey" 'Version') 'the version value is gone'
Assert-Equal $null (Get-UninstallValue "HKCU:\$RegKey" 'Scope') 'the scope value is gone'
Assert-Equal 'keep me' (Get-UninstallValue $QtKey 'ci_marker') 'the Qt settings key survives'
foreach ($sub in @('packages', 'models', 'Cache')) {
    Assert-Present (Join-Path $DataDir "$sub\marker.txt") "the $sub data survives"
}
Assert-Present (Join-Path $DataDir 'ocr.toml') 'the settings file survives the uninstall'
foreach ($sub in @('benchmark', 'speed')) {
    Assert-Present (Join-Path $DataDir "$sub\marker.txt") "the $sub data survives the uninstall"
}

# ── 6. Uninstall, removing the data ─────────────────────────────────────────

Write-Step 'Uninstall, removing the user data'
Assert-Equal 0 (Invoke-Setup @('/SCOPE=user')) 'the reinstall exits 0'
Invoke-Uninstaller -Directory $UserDir -Arguments @('/DELDATA', '/DELSETTINGS')
foreach ($sub in @('packages', 'models', 'Cache')) {
    Assert-Missing (Join-Path $DataDir $sub) "the $sub folder is gone"
}
Assert-Missing (Join-Path $DataDir 'ocr.toml') 'the settings file is gone'
foreach ($sub in @('benchmark', 'speed')) {
    Assert-Missing (Join-Path $DataDir $sub) "the $sub folder is gone"
}
Assert-Missing $QtKey 'the Qt settings key is gone'

# ── 7. A folder that belongs to somebody else ───────────────────────────────

Write-Step 'Refuse a folder that holds other files'
New-Item -ItemType Directory -Path 'C:\tt-foreign' -Force | Out-Null
Set-Content -LiteralPath 'C:\tt-foreign\keep.txt' -Value 'not ours'
Assert-Equal 12 (Invoke-Setup @('/SCOPE=user', '/D=C:\tt-foreign')) 'the installer exits 12'
Assert-Present 'C:\tt-foreign\keep.txt' 'the other file is still there'
Remove-Item -LiteralPath 'C:\tt-foreign' -Recurse -Force

# ── 8. Both scopes side by side ─────────────────────────────────────────────

Write-Step 'An all-users install, then a per-user run'
Assert-Equal 0 (Invoke-Setup @('/SCOPE=all')) 'the all-users install exits 0'
Assert-Present (Join-Path $AllUsersDir 'teachers-teammate.exe') 'the exe is in Program Files'
Assert-Equal $Version (Get-UninstallValue "HKLM:\$UninstKey32" 'DisplayVersion') 'the all-users Apps entry has the version'
Assert-Equal 13 (Invoke-Setup @('/SCOPE=user')) 'a per-user run next to it exits 13'
Invoke-Uninstaller -Directory $AllUsersDir -AppsKey "HKLM:\$UninstKey32"
Assert-Missing $AllUsersDir 'the all-users copy is gone'

# ── 9. Update from the shipped v0.5.6 ───────────────────────────────────────

if ($SkipOldVersion) {
    Write-Host ''
    Write-Host '── 9. Update from v0.5.6: skipped' -ForegroundColor Yellow
} else {
    Write-Step 'Update from the shipped v0.5.6'
    $old = Join-Path $env:TEMP 'tt-old-setup'
    Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $old -Force | Out-Null
    gh release download v0.5.6 --pattern '*-w64-setup.exe' --dir $old
    $oldSetup = Get-ChildItem -LiteralPath $old -Filter '*-w64-setup.exe' | Select-Object -First 1
    Assert-True ($null -ne $oldSetup) 'the v0.5.6 installer was downloaded'
    # v0.5.6 has no /SCOPE: elevated, it installs for all users.
    $oldProcess = Start-Process -FilePath $oldSetup.FullName -ArgumentList @('/S') -Wait -PassThru
    Assert-Equal 0 $oldProcess.ExitCode 'v0.5.6 installs'
    Assert-Present (Join-Path $AllUsersDir 'teachers-teammate.exe') 'v0.5.6 landed in Program Files'
    # A marker only the old uninstaller would remove: the new installer must not run it.
    New-Item -Path $QtKey -Force | Out-Null
    Set-ItemProperty -LiteralPath $QtKey -Name 'ci_marker' -Value 'keep me'
    $update = @('/SCOPE=all')
    if ([version]($Version -replace '-.*$', '') -le [version]'0.5.6') {
        # A build without a release tag is 0.1.0, older than the shipped v0.5.6. The installer
        # refuses that with exit 11 and changes nothing; /ALLOWDOWNGRADE then replaces it in
        # place, which is the same file and registry work as a real update.
        Assert-Equal 11 (Invoke-Setup $update) 'an older build is refused without /ALLOWDOWNGRADE'
        $update += '/ALLOWDOWNGRADE'
    }
    Assert-Equal 0 (Invoke-Setup $update) 'the new installer updates it in place'
    Assert-Equal $Version (Get-UninstallValue "HKLM:\$UninstKey32" 'DisplayVersion') 'one Apps entry, with the new version'
    Assert-Equal 'keep me' (Get-UninstallValue $QtKey 'ci_marker') 'the old uninstaller was not run'
    Invoke-Uninstaller -Directory $AllUsersDir -AppsKey "HKLM:\$UninstKey32"
    Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ''
Write-Host "All $($script:Step) steps passed." -ForegroundColor Green
