<#
.SYNOPSIS
    Build the Windows setup in two passes: the uninstaller first, then the setup that packs it.

.DESCRIPTION
    The uninstaller is a file of its own, so a remote signer can sign it before the setup is
    built (tools/packaging/windows.nsi says why). The first pass builds a helper that writes
    pyinstaller-work\Uninstall.exe and quits; the second pass packs that file instead of writing
    it. Both passes get the same version, because the uninstaller carries the setup's version
    resource.

    Run from the repository root, after tools/build/build_standalone.py, with makensis on the
    PATH (choco install nsis --version=3.13.0). CI runs this script (build_artifacts.yml);
    docs/deployment.md describes it.

.PARAMETER Version
    The release string without the v, e.g. 0.5.7 or 0.5.7-rc.1 (the setup's file name).

.PARAMETER WinVersion
    The numeric form without a pre-release suffix, e.g. 0.5.7 (VIProductVersion wants X.Y.Z.W).

.EXAMPLE
    pwsh tools/packaging/make_windows_installer.ps1 -Version 0.5.7-rc.1 -WinVersion 0.5.7
#>
param(
    [Parameter(Mandatory = $true)] [string] $Version,
    [Parameter(Mandatory = $true)] [string] $WinVersion
)

$ErrorActionPreference = 'Stop'

$work = Join-Path $PWD 'pyinstaller-work'
New-Item -ItemType Directory -Force -Path $work | Out-Null
$helper      = Join-Path $work 'uninstaller-helper.exe'
$uninstaller = Join-Path $work 'Uninstall.exe'

makensis /DBUILD_UNINSTALLER /DAPP_VERSION="$Version" "/DAPP_VERSION_WIN=$WinVersion.0" `
    "/DUNINSTALLER_HELPER=$helper" "/DUNINSTALLER_TARGET=$uninstaller" `
    "/DUNINSTALLER_OUT=$uninstaller" tools/packaging/windows.nsi
if ($LASTEXITCODE -ne 0) { throw "the uninstaller pass failed ($LASTEXITCODE)" }

# Start-Process -Wait: the helper is a GUI program, and `&` would not wait for it.
$run = Start-Process -FilePath $helper -Wait -PassThru
if ($run.ExitCode -ne 0) { throw "the uninstaller helper failed ($($run.ExitCode))" }
if (-not (Test-Path -LiteralPath $uninstaller)) { throw "the helper wrote no Uninstall.exe" }

makensis /DAPP_VERSION="$Version" "/DAPP_VERSION_WIN=$WinVersion.0" tools/packaging/windows.nsi
if ($LASTEXITCODE -ne 0) { throw "the setup pass failed ($LASTEXITCODE)" }
