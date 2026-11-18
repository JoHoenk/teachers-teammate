<#
.SYNOPSIS
    Installs, updates and uninstalls Teacher's Teammate and checks what is left behind.

.DESCRIPTION
    Runs the NSIS installer the way a teacher and an IT department do: a fresh per-user
    install, an update over it, an install while the app is open, an uninstall that keeps
    the data, an uninstall that removes it, a folder that belongs to somebody else, the two
    scopes side by side, and an update from the newest published release. Every step asserts
    on the registry, the folders, the shortcuts and the exit code, so a regression in
    tools/packaging/windows.nsi fails the build instead of a teacher's computer.

    The first step also reads the version resource of the setup, the app exe and the
    uninstaller: a code-signing service checks that the three carry one product name and one
    product version, and the uninstaller is a file of its own (two-pass build).

    The shortcuts are read with the WScript.Shell COM object, which works on a runner without
    a desktop session. Windows may hand back an 8.3 name (GITHUB~1), so every target is
    expanded before it is compared, and the comparison is case-insensitive.

    The runner must be elevated (GitHub's windows-latest is), which is why the per-user runs
    pass /SCOPE=user: without it an elevated installer would default to all users.

.PARAMETER Setup
    The installer to test, e.g. dist\teachers-teammate-1.2.3-w64-setup.exe.

.PARAMETER Version
    The version the installer was built with, e.g. 1.2.3.

.PARAMETER SmokeResult
    Where the installed copy's smoke test writes its JSON result (default: smoke.json in the
    current folder). The file is kept, so a CI job can upload it.

.PARAMETER SkipOldVersion
    Skip the update-from-v0.5.6 step (it downloads v0.5.6 from GitHub).

.EXAMPLE
    pwsh -File tools/packaging/test_windows_installer.ps1 `
        -Setup dist\teachers-teammate-1.2.3-w64-setup.exe -Version 1.2.3
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$SmokeResult = 'smoke.json',
    [switch]$SkipOldVersion
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Setup = (Resolve-Path $Setup).Path
$SmokeResult = [System.IO.Path]::GetFullPath($SmokeResult, (Get-Location).Path)
$LatestRelease = Join-Path $PSScriptRoot 'latest_release.py'

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

# Where the installer puts its shortcuts: $SMPROGRAMS and $DESKTOP follow SetShellVarContext,
# so a per-user install writes into the user's folders and an all-users one into the common
# folders. [Environment]::GetFolderPath names the same four.
$UserStartMenu = Join-Path ([Environment]::GetFolderPath('Programs')) $AppName
$AllUsersStartMenu = Join-Path ([Environment]::GetFolderPath('CommonPrograms')) $AppName
$UserDesktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) "$AppName.lnk"
$AllUsersDesktopLink = Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) "$AppName.lnk"

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

function Wait-Removed {
    <# An NSIS uninstaller copies itself to %TEMP% and returns at once: wait for its work. #>
    param([string]$Directory, [string]$AppsKey)
    # It removes the program files first and the registry values last, so wait for both.
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

function Invoke-Uninstaller {
    <# Run the installed uninstaller silently and wait until it has finished. #>
    param([string]$Directory, [string[]]$Arguments = @(), [string]$AppsKey = "HKCU:\$UninstKey")
    $uninstaller = Join-Path $Directory 'Uninstall.exe'
    if (-not (Test-Path -LiteralPath $uninstaller)) {
        throw "FAILED: no uninstaller in $Directory"
    }
    $all = @('/S') + $Arguments
    Start-Process -FilePath $uninstaller -ArgumentList $all -Wait | Out-Null
    Wait-Removed -Directory $Directory -AppsKey $AppsKey
}

function Invoke-UninstallString {
    <# Run the Apps entry's UninstallString the way Windows Settings does: through a shell. #>
    param(
        [string]$Command,
        [string]$Directory,
        [string[]]$Arguments = @('/S'),
        [string]$AppsKey = "HKCU:\$UninstKey"
    )
    $line = (@($Command) + $Arguments) -join ' '
    $batch = Join-Path $env:TEMP "tt-uninstall-$([guid]::NewGuid().ToString('N')).cmd"
    Set-Content -LiteralPath $batch -Value "@echo off`r`n$line" -Encoding ascii
    try {
        Start-Process -FilePath $env:ComSpec -ArgumentList @('/c', "`"$batch`"") -Wait -NoNewWindow | Out-Null
    } finally {
        Remove-Item -LiteralPath $batch -Force -ErrorAction SilentlyContinue
    }
    Wait-Removed -Directory $Directory -AppsKey $AppsKey
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

function Get-Shortcut {
    <# Read a .lnk with the WScript.Shell COM object; it needs no desktop session. #>
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    $shell = New-Object -ComObject WScript.Shell
    try {
        $link = $shell.CreateShortcut($Path)
        return [pscustomobject]@{ Target = $link.TargetPath; Arguments = $link.Arguments }
    } finally {
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    }
}

function Expand-ShortPath {
    <# Expand 8.3 components (GITHUB~1) to the names Windows shows; a missing path stays as is. #>
    param([string]$Path)
    if ([string]::IsNullOrEmpty($Path)) {
        return ''
    }
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($full)
    $parts = $full.Substring($root.Length).Split(
        [System.IO.Path]::DirectorySeparatorChar, [System.StringSplitOptions]::RemoveEmptyEntries)
    $current = $root
    foreach ($part in $parts) {
        $next = Join-Path $current $part
        if (Test-Path -LiteralPath $next) {
            # The directory listing carries the long name; the path we were handed may not.
            $match = Get-ChildItem -LiteralPath $current -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -ieq $part } | Select-Object -First 1
            if ($null -ne $match) {
                $next = $match.FullName
            }
        }
        $current = $next
    }
    return $current
}

function Assert-Shortcut {
    <# The shortcut exists and points at $Target; both sides are expanded and compared as text. #>
    param([string]$Path, [string]$Target, [string]$Message)
    $link = Get-Shortcut $Path
    Assert-True ($null -ne $link) "$Message (no shortcut at $Path)"
    Assert-Equal (Expand-ShortPath $Target) (Expand-ShortPath $link.Target) $Message
}

function Assert-UpdateKeptShortcuts {
    <# After an update of the all-users copy: the Start menu points at it, the desktop link stays. #>
    $exe = Join-Path $AllUsersDir 'teachers-teammate.exe'
    Assert-Shortcut (Join-Path $AllUsersStartMenu "Teacher's Teammate.lnk") $exe `
        'the Start-menu shortcut points at the updated copy'
    Assert-Shortcut $AllUsersDesktopLink $exe 'the update keeps the desktop shortcut'
}

function Assert-SmokeTestPasses([string]$Directory) {
    <# The installed copy must be complete: run its own smoke test and keep the result. #>
    $process = Start-Process -FilePath (Join-Path $Directory 'teachers-teammate.exe') `
        -ArgumentList @('--_smoke_test', $SmokeResult) -Wait -PassThru
    $json = if (Test-Path -LiteralPath $SmokeResult) { Get-Content -LiteralPath $SmokeResult -Raw } else { '' }
    Assert-Equal 0 $process.ExitCode "the installed copy passes its smoke test"
    Assert-True ($json -match '"ok":\s*true') "the smoke test result says ok"
}

function Get-LatestReleaseSetup([string]$Directory) {
    <# Download the newest published release's setup; a missing release fails, never skips. #>
    $asset = & python $LatestRelease download --pattern '*-w64-setup.exe' --dir $Directory
    if ($LASTEXITCODE -ne 0) {
        throw "FAILED: could not download the latest release's setup ($LASTEXITCODE)"
    }
    $path = @($asset) | Where-Object { $_ } | Select-Object -Last 1
    if ([string]::IsNullOrEmpty($path)) {
        throw 'FAILED: latest_release.py named no setup'
    }
    return $path.Trim()
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
Remove-Item -LiteralPath $UserStartMenu -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $AllUsersStartMenu -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $UserDesktopLink -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $AllUsersDesktopLink -Force -ErrorAction SilentlyContinue

# ── 1. A fresh per-user install ─────────────────────────────────────────────

Write-Step 'Fresh per-user install'
Assert-Equal 0 (Invoke-Setup @('/SCOPE=user')) 'the installer exits 0'
Assert-Present (Join-Path $UserDir 'teachers-teammate.exe') 'the exe is under %LOCALAPPDATA%\Programs'
Assert-Present (Join-Path $UserDir 'NOTICE.txt') 'the notice is installed next to the exe (LGPL)'
Assert-Present (Join-Path $UserDir 'third_party_license.txt') 'the license list is installed next to the exe'
Assert-Present (Join-Path $UserDir 'Uninstall.exe') 'the uninstaller is installed as its own file'
Assert-Equal $Version (Get-UninstallValue "HKCU:\$UninstKey" 'DisplayVersion') 'the Apps entry has the version'
Assert-Equal $UserDir (Get-UninstallValue "HKCU:\$RegKey" 'InstallDir') 'the install folder is recorded'
Assert-Equal 'user' (Get-UninstallValue "HKCU:\$RegKey" 'Scope') 'the scope is recorded'
Assert-SmokeTestPasses $UserDir

# The shortcuts: a per-user install writes into the user's Start menu and desktop, and both
# point at the installed copy, not at the setup.
Assert-Shortcut (Join-Path $UserStartMenu "Teacher's Teammate.lnk") `
    (Join-Path $UserDir 'teachers-teammate.exe') 'the Start-menu shortcut starts the installed exe'
Assert-Shortcut (Join-Path $UserStartMenu 'Uninstall.lnk') `
    (Join-Path $UserDir 'Uninstall.exe') 'the Start-menu uninstall shortcut starts the uninstaller'
Assert-Shortcut $UserDesktopLink (Join-Path $UserDir 'teachers-teammate.exe') `
    'the desktop shortcut starts the installed exe'

# What Windows Settings reads out of the Apps entry, beyond the version.
$displayIcon = Get-UninstallValue "HKCU:\$UninstKey" 'DisplayIcon'
Assert-True (-not [string]::IsNullOrEmpty($displayIcon)) 'the Apps entry has a DisplayIcon'
Assert-Present $displayIcon 'DisplayIcon names a file that exists'
Assert-Equal (Join-Path $UserDir 'teachers-teammate.exe') $displayIcon 'DisplayIcon is the installed exe'
$estimatedSize = Get-UninstallValue "HKCU:\$UninstKey" 'EstimatedSize'
Assert-True ($estimatedSize -gt 0) 'EstimatedSize is more than zero'
$uninstallString = Get-UninstallValue "HKCU:\$UninstKey" 'UninstallString'
Assert-Equal "`"$(Join-Path $UserDir 'Uninstall.exe')`"" $uninstallString `
    'UninstallString is the quoted path of the installed uninstaller'

# The three files a code-signing service checks: one product name, one product version. The
# uninstaller gets its keys from the helper that wrote it, the exe from its version resource.
foreach ($file in @('teachers-teammate.exe', 'Uninstall.exe')) {
    $info = (Get-Item -LiteralPath (Join-Path $UserDir $file)).VersionInfo
    Assert-Equal $AppName $info.ProductName "$file carries the product name"
    Assert-Equal $Version $info.ProductVersion "$file carries the product version"
    Assert-Equal 'Teacher''s Teammate Contributors' $info.CompanyName "$file carries the publisher"
}
# The signing check reads the Apps entry's Publisher and the exe's company; they must agree.
$company = (Get-Item -LiteralPath (Join-Path $UserDir 'teachers-teammate.exe')).VersionInfo.CompanyName
Assert-Equal $company (Get-UninstallValue "HKCU:\$UninstKey" 'Publisher') `
    'Publisher is the company the version resource carries'
$setupInfo = (Get-Item -LiteralPath $Setup).VersionInfo
Assert-Equal $AppName $setupInfo.ProductName 'the setup carries the product name'
Assert-Equal $Version $setupInfo.ProductVersion 'the setup carries the product version'

# ── 2. Markers that an update must not touch ────────────────────────────────

Write-Step 'Write markers an update must keep'
New-Item -Path $QtKey -Force | Out-Null
Set-ItemProperty -LiteralPath $QtKey -Name 'ci_marker' -Value 'keep me'
foreach ($sub in @('packages', 'models', 'Cache')) {
    New-Item -ItemType Directory -Path (Join-Path $DataDir $sub) -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $DataDir "$sub\marker.txt") -Value 'keep me'
}
# The settings file, the benchmark history, the speed data and the teacher's own work sit
# beside packages\.
Set-Content -LiteralPath (Join-Path $DataDir 'ocr.toml') -Value 'keep me'
foreach ($sub in @('benchmark', 'speed', 'work')) {
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
foreach ($sub in @('benchmark', 'speed', 'work')) {
    Assert-Present (Join-Path $DataDir "$sub\marker.txt") "the $sub marker survives the update"
}
Assert-Equal $Version (Get-UninstallValue "HKCU:\$UninstKey" 'DisplayVersion') 'one Apps entry, with the version'
Assert-Missing $AllUsersDir 'nothing was written to Program Files'
# SecDesktop: an update leaves an existing shortcut alone, so it is still there afterwards.
Assert-Shortcut $UserDesktopLink (Join-Path $UserDir 'teachers-teammate.exe') `
    'the update keeps the desktop shortcut'

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
# Through the Apps entry's own UninstallString, which is what Windows Settings runs: the value
# has to be quoted, or a folder with a space in it would break.
Invoke-UninstallString -Command $uninstallString -Directory $UserDir
Assert-Missing $UserDir 'the program folder is gone'
Assert-Equal $null (Get-UninstallValue "HKCU:\$UninstKey" 'DisplayVersion') 'the Apps entry is gone'
Assert-Equal $null (Get-UninstallValue "HKCU:\$RegKey" 'InstallDir') 'the install folder value is gone'
Assert-Equal $null (Get-UninstallValue "HKCU:\$RegKey" 'Version') 'the version value is gone'
Assert-Equal $null (Get-UninstallValue "HKCU:\$RegKey" 'Scope') 'the scope value is gone'
Assert-Missing $UserStartMenu 'the Start-menu folder is gone'
Assert-Missing $UserDesktopLink 'the desktop shortcut is gone'
Assert-Equal 'keep me' (Get-UninstallValue $QtKey 'ci_marker') 'the Qt settings key survives'
foreach ($sub in @('packages', 'models', 'Cache')) {
    Assert-Present (Join-Path $DataDir "$sub\marker.txt") "the $sub data survives"
}
Assert-Present (Join-Path $DataDir 'ocr.toml') 'the settings file survives the uninstall'
foreach ($sub in @('benchmark', 'speed', 'work')) {
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
foreach ($sub in @('benchmark', 'speed', 'work')) {
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
# SetShellVarContext all: the shortcuts go into the common folders, not the user's.
Assert-Shortcut (Join-Path $AllUsersStartMenu "Teacher's Teammate.lnk") `
    (Join-Path $AllUsersDir 'teachers-teammate.exe') 'the all-users Start-menu shortcut is in the common folder'
Assert-Shortcut (Join-Path $AllUsersStartMenu 'Uninstall.lnk') `
    (Join-Path $AllUsersDir 'Uninstall.exe') 'the all-users uninstall shortcut is in the common folder'
Assert-Missing $UserStartMenu 'nothing was written to the user Start menu'
Assert-Equal 13 (Invoke-Setup @('/SCOPE=user')) 'a per-user run next to it exits 13'
Invoke-Uninstaller -Directory $AllUsersDir -AppsKey "HKLM:\$UninstKey32"
Assert-Missing $AllUsersDir 'the all-users copy is gone'
Assert-Missing $AllUsersStartMenu 'the common Start-menu folder is gone'

# The other way round: a per-user copy plus /SCOPE=all.
Assert-Equal 0 (Invoke-Setup @('/SCOPE=user')) 'the per-user install exits 0'
Assert-Equal 13 (Invoke-Setup @('/SCOPE=all')) 'an all-users run next to it exits 13'
Assert-Missing $AllUsersDir 'the refused run wrote nothing to Program Files'
Invoke-Uninstaller -Directory $UserDir
Assert-Missing $UserDir 'the per-user copy is gone'

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
    Assert-UpdateKeptShortcuts
    Invoke-Uninstaller -Directory $AllUsersDir -AppsKey "HKLM:\$UninstKey32"
    Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
}

# ── 10. Update from the newest published release ────────────────────────────

Write-Step 'Update from the newest published release'
$latest = Join-Path $env:TEMP 'tt-latest-setup'
Remove-Item -LiteralPath $latest -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $latest -Force | Out-Null
$latestSetup = Get-LatestReleaseSetup $latest
Assert-Present $latestSetup 'the newest release setup was downloaded'
$latestVersion = (Split-Path $latestSetup -Leaf) -replace '^teachers-teammate-', '' -replace '-w64-setup\.exe$', ''
Write-Host "   ..  the newest release is $latestVersion" -ForegroundColor DarkGray
if ((-not $SkipOldVersion) -and $latestVersion -eq '0.5.6') {
    # Step 9 already updated from exactly this release, shortcuts included.
    Write-Host '   ..  the newest release is the one step 9 updated from: nothing more to test' -ForegroundColor DarkGray
} else {
    # Plain /S only: elevated, every version installs for all users.
    $latestProcess = Start-Process -FilePath $latestSetup -ArgumentList @('/S') -Wait -PassThru
    Assert-Equal 0 $latestProcess.ExitCode "the $latestVersion setup installs"
    Assert-Present (Join-Path $AllUsersDir 'teachers-teammate.exe') "$latestVersion landed in Program Files"
    New-Item -Path $QtKey -Force | Out-Null
    Set-ItemProperty -LiteralPath $QtKey -Name 'ci_marker' -Value 'keep me'
    $update = @('/SCOPE=all')
    if ([version]($Version -replace '-.*$', '') -lt [version]($latestVersion -replace '-.*$', '')) {
        # A test build is 0.1.0, older than anything published. Equal is a reinstall, not a refusal.
        Assert-Equal 11 (Invoke-Setup $update) "a build older than $latestVersion is refused without /ALLOWDOWNGRADE"
        $update += '/ALLOWDOWNGRADE'
    }
    Assert-Equal 0 (Invoke-Setup $update) 'the new installer updates it in place'
    Assert-Equal $Version (Get-UninstallValue "HKLM:\$UninstKey32" 'DisplayVersion') 'one Apps entry, with the new version'
    Assert-Equal 'keep me' (Get-UninstallValue $QtKey 'ci_marker') 'the old uninstaller was not run'
    # The shortcut rules after an update: the Start-menu entries point at the new copy, and the
    # desktop shortcut the old setup made is kept rather than recreated.
    Assert-UpdateKeptShortcuts
    Invoke-Uninstaller -Directory $AllUsersDir -AppsKey "HKLM:\$UninstKey32"
}
Remove-Item -LiteralPath $latest -Recurse -Force -ErrorAction SilentlyContinue

# ── 11. The desktop shortcut rules ──────────────────────────────────────────

Write-Step 'A fresh install without a desktop shortcut'
Assert-Equal 0 (Invoke-Setup @('/SCOPE=user', '/NODESKTOP')) 'the installer exits 0'
Assert-Missing $UserDesktopLink 'no desktop shortcut was created'
Assert-Shortcut (Join-Path $UserStartMenu "Teacher's Teammate.lnk") `
    (Join-Path $UserDir 'teachers-teammate.exe') 'the Start-menu shortcut is there anyway'
Invoke-Uninstaller -Directory $UserDir
Assert-Missing $UserDesktopLink 'the uninstall leaves no desktop shortcut behind'

Write-Step 'An update does not recreate a deleted desktop shortcut'
Assert-Equal 0 (Invoke-Setup @('/SCOPE=user')) 'the fresh install exits 0'
Assert-Present $UserDesktopLink 'the fresh install made a desktop shortcut'
Remove-Item -LiteralPath $UserDesktopLink -Force
Assert-Equal 0 (Invoke-Setup @('/SCOPE=user')) 'the update exits 0'
Assert-Missing $UserDesktopLink 'the update did not recreate the deleted shortcut'
Invoke-Uninstaller -Directory $UserDir

# ── 12. Two copies, and a silent run without /SCOPE= ────────────────────────

Write-Step 'Two copies, and a silent run without /SCOPE='
# The installer refuses to make a second copy (step 8 asserts exit 13 both ways), so the state
# an older installer could leave behind is built by hand: the all-users copy, plus a per-user
# one that holds the exe and the registry values .onInit reads to find a copy.
Assert-Equal 0 (Invoke-Setup @('/SCOPE=all')) 'the all-users install exits 0'
New-Item -ItemType Directory -Path $UserDir -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $AllUsersDir 'teachers-teammate.exe') -Destination $UserDir
New-Item -Path "HKCU:\$RegKey" -Force | Out-Null
Set-ItemProperty -LiteralPath "HKCU:\$RegKey" -Name 'InstallDir' -Value $UserDir
New-Item -Path "HKCU:\$UninstKey" -Force | Out-Null
Set-ItemProperty -LiteralPath "HKCU:\$UninstKey" -Name 'DisplayVersion' -Value $Version
Assert-Present (Join-Path $UserDir 'teachers-teammate.exe') 'the per-user copy is there'
Assert-Present (Join-Path $AllUsersDir 'teachers-teammate.exe') 'the all-users copy is there'
Assert-Equal 13 (Invoke-Setup) 'a silent run without /SCOPE= exits 13'
# A sentinel in the copy that must not be touched: an update writes its version only into the
# scope it was told, so the other copy keeps whatever was there.
Set-ItemProperty -LiteralPath "HKLM:\$RegKey32" -Name 'Version' -Value '9.9.9'
Assert-Equal 0 (Invoke-Setup @('/SCOPE=user')) 'a silent run with /SCOPE=user exits 0'
Assert-Equal $Version (Get-UninstallValue "HKCU:\$RegKey" 'Version') 'the per-user copy was updated'
Assert-Equal '9.9.9' (Get-UninstallValue "HKLM:\$RegKey32" 'Version') 'the all-users copy was left alone'
Set-ItemProperty -LiteralPath "HKCU:\$RegKey" -Name 'Version' -Value '9.9.9'
Assert-Equal 0 (Invoke-Setup @('/SCOPE=all')) 'a silent run with /SCOPE=all exits 0'
Assert-Equal $Version (Get-UninstallValue "HKLM:\$RegKey32" 'Version') 'the all-users copy was updated'
Assert-Equal '9.9.9' (Get-UninstallValue "HKCU:\$RegKey" 'Version') 'the per-user copy was left alone'
Invoke-Uninstaller -Directory $UserDir
Invoke-Uninstaller -Directory $AllUsersDir -AppsKey "HKLM:\$UninstKey32"

# ── 13. The uninstaller's two checkboxes, one at a time ─────────────────────

Write-Step 'Uninstall with /DELDATA only'
Assert-Equal 0 (Invoke-Setup @('/SCOPE=user')) 'the install exits 0'
New-Item -Path $QtKey -Force | Out-Null
Set-ItemProperty -LiteralPath $QtKey -Name 'ci_marker' -Value 'keep me'
foreach ($sub in @('packages', 'models', 'Cache', 'benchmark', 'speed')) {
    New-Item -ItemType Directory -Path (Join-Path $DataDir $sub) -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $DataDir "$sub\marker.txt") -Value 'keep me'
}
Set-Content -LiteralPath (Join-Path $DataDir 'ocr.toml') -Value 'keep me'
Invoke-Uninstaller -Directory $UserDir -Arguments @('/DELDATA')
foreach ($sub in @('packages', 'models')) {
    Assert-Missing (Join-Path $DataDir $sub) "the $sub folder is gone"
}
foreach ($sub in @('Cache', 'benchmark', 'speed')) {
    Assert-Present (Join-Path $DataDir "$sub\marker.txt") "the $sub data survives /DELDATA"
}
Assert-Present (Join-Path $DataDir 'ocr.toml') 'the settings file survives /DELDATA'
Assert-Equal 'keep me' (Get-UninstallValue $QtKey 'ci_marker') 'the Qt settings survive /DELDATA'

Write-Step 'Uninstall with /DELSETTINGS only'
Assert-Equal 0 (Invoke-Setup @('/SCOPE=user')) 'the install exits 0'
foreach ($sub in @('packages', 'models')) {
    New-Item -ItemType Directory -Path (Join-Path $DataDir $sub) -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $DataDir "$sub\marker.txt") -Value 'keep me'
}
Invoke-Uninstaller -Directory $UserDir -Arguments @('/DELSETTINGS')
foreach ($sub in @('packages', 'models')) {
    Assert-Present (Join-Path $DataDir "$sub\marker.txt") "the $sub data survives /DELSETTINGS"
}
Assert-Missing (Join-Path $DataDir 'ocr.toml') 'the settings file is gone'
foreach ($sub in @('Cache', 'benchmark', 'speed')) {
    Assert-Missing (Join-Path $DataDir $sub) "the $sub folder is gone"
}
Assert-Missing $QtKey 'the Qt settings key is gone'

# ── 14. The uninstaller page still takes its defaults from the defines ──────

Write-Step 'Static check: the uninstaller page takes its defaults from the defines'
# The dialog cannot be clicked on a runner, so the one thing a silent run cannot show — which
# boxes the page starts with — is read out of the script instead.
$nsi = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'windows.nsi') -Raw
Assert-True ($nsi -match '(?m)^!define UN_DEFAULT_PACKAGES\s+\$\{BST_CHECKED\}') `
    'the add-ons box is ticked by default'
Assert-True ($nsi -match '(?m)^!define UN_DEFAULT_SETTINGS\s+\$\{BST_UNCHECKED\}') `
    'the settings box is not ticked by default'
Assert-True ($nsi -match '\$\{NSD_SetState\} \$UnDelPackages \$\{UN_DEFAULT_PACKAGES\}') `
    'the add-ons box starts from UN_DEFAULT_PACKAGES'
Assert-True ($nsi -match '\$\{NSD_SetState\} \$UnDelCache\s+\$\{UN_DEFAULT_SETTINGS\}') `
    'the settings box starts from UN_DEFAULT_SETTINGS'

Write-Host ''
Write-Host "All $($script:Step) steps passed." -ForegroundColor Green
