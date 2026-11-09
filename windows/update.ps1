# Updates HASC to the latest GitHub release: double-click update.cmd (in C:\HASC\tools, or the
# "HASC update" shortcut). It downloads the release's Windows bundle and runs that bundle's installer,
# which keeps the data and settings. The repository is private, so the first run asks for a
# read-only GitHub token and keeps it, encrypted to this Windows user.
param(
    [string]$InstallDir = "C:\HASC",
    [string]$Repo = "lystiger/HASC-OCR",
    [string]$Token = "",
    [string]$ZipPath = "",
    [System.Security.SecureString]$HascPassword,
    [switch]$CheckOnly,
    [switch]$Force,
    [switch]$NonInteractive
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

if (-not (Test-Admin)) {
    if ($NonInteractive) { throw "Run this as Administrator." }
    Restart-Elevated $PSCommandPath (Get-ForwardArguments $PSBoundParameters)
    exit
}

# One file per Windows user, since each is encrypted to the user who saved it.
$TokenFile = Join-Path $InstallDir "tools\github-token-$env:USERNAME.dat"
$CheckTokenFile = Join-Path $InstallDir "tools\github-token-check.dat"

function Save-CheckToken([string]$Plain) {
    # The daily update check runs as SYSTEM, which cannot read a token encrypted to this user: it gets
    # a copy encrypted to this PC, readable by administrators and SYSTEM only (Protect-TokenFile).
    Add-Type -AssemblyName System.Security
    $bytes = [Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($Plain), $null, "LocalMachine")
    Set-Content $CheckTokenFile -Value ([Convert]::ToBase64String($bytes)) -Encoding ASCII
    Protect-TokenFile
}

function Get-Token {
    if ($Token) { return $Token }
    if (Test-Path $TokenFile) {
        try {
            $saved = Get-Plain (Get-Content $TokenFile | ConvertTo-SecureString)
            # Installs from before the update check have no copy for it yet.
            if (-not (Test-Path $CheckTokenFile)) { Save-CheckToken $saved }
            return $saved
        }
        catch { Remove-Item $TokenFile -Force }  # unreadable (this user's key changed): ask again
    }
    $help = "Updates come from GitHub. Paste a read-only GitHub token (asked once).`n`nTo make one: github.com, Settings, Developer settings, Fine-grained tokens, Generate. Repository access: only HASC-OCR. Permissions: Contents, Read-only."
    $plain = (Get-Plain (Read-Text $help -Secret)).Trim()
    if ($plain.Length -eq 0) { throw "No token given." }
    ConvertTo-SecureString $plain -AsPlainText -Force | ConvertFrom-SecureString | Set-Content $TokenFile -Encoding ASCII
    Save-CheckToken $plain
    Protect-TokenFile
    return $plain
}

function Update-Hasc {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $installed = (Get-Content (Join-Path $InstallDir "VERSION.txt") -ErrorAction SilentlyContinue)
    $work = Join-Path $env:TEMP "hasc-update"
    if (Test-Path $work) { Remove-Item $work -Recurse -Force }
    New-Item -ItemType Directory -Path $work | Out-Null

    if (-not $ZipPath) {
        Step "Asking GitHub for the latest release"
        $release = Invoke-GitHub "https://api.github.com/repos/$Repo/releases/latest" "application/vnd.github+json" "" (Get-Token) @($TokenFile, $CheckTokenFile) | ConvertFrom-Json
        $asset = $release.assets | Where-Object { $_.name -like "hasc-*-win64.zip" } | Select-Object -First 1
        if (-not $asset) { throw "Release $($release.tag_name) has no Windows bundle yet. Try again in a few minutes." }
        Write-Host "Latest: $($release.tag_name) ($($asset.name)); installed: $installed"
        if ($CheckOnly) { return }
        if ($release.tag_name -eq $installed -and -not $Force) {
            Show-Info "HASC is up to date ($installed)."
            return
        }
        if (-not (Confirm-Choice "Update HASC from $installed to $($release.tag_name)?`n`nThe website stops for about a minute. The data and settings are kept.")) { return }
        # The zip runs as Administrator, so it must match the checksum CI published next to it.
        $sum = $release.assets | Where-Object { $_.name -eq "$($asset.name).sha256" } | Select-Object -First 1
        if (-not $sum) { throw "Release $($release.tag_name) has no checksum ($($asset.name).sha256), so it cannot be checked. Nothing was changed." }
        Step "Downloading $($asset.name)"
        $ZipPath = Join-Path $work $asset.name
        Invoke-GitHub $asset.url "application/octet-stream" $ZipPath (Get-Token) @($TokenFile, $CheckTokenFile)
        $sumPath = Join-Path $work $sum.name
        Invoke-GitHub $sum.url "application/octet-stream" $sumPath (Get-Token) @($TokenFile, $CheckTokenFile)
        $expected = ((Get-Content $sumPath -Raw).Trim() -split "\s+")[0]
        $actual = (Get-FileHash $ZipPath -Algorithm SHA256).Hash
        if ($actual -ne $expected) { throw "The downloaded $($asset.name) does not match its checksum (expected $expected, got $actual). Nothing was changed." }
    }

    Step "Unpacking"
    $bundle = Join-Path $work "bundle"
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $bundle
    Get-ChildItem $bundle -Recurse -File | Unblock-File
    $installArgs = @{ InstallDir = $InstallDir; Yes = $true }
    if ($HascPassword) { $installArgs.HascPassword = $HascPassword }
    if ($NonInteractive) { $installArgs.NonInteractive = $true }
    # The new release's own installer does the update; it asks before it changes anything.
    & (Join-Path $bundle "install.ps1") @installArgs
    $code = $LASTEXITCODE
    Protect-TokenFile
    Set-Location $env:TEMP
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    if ($code -ne 0 -and $null -ne $code) { exit $code }
}

try {
    Update-Hasc
} catch {
    Exit-WithError $_
}
# A caller reads $LASTEXITCODE; without this it would be the last native command's.
exit 0
