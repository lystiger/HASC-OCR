# Asks GitHub whether a newer HASC release exists and writes the answer to tools\update-status.json,
# which the website shows to admins as a banner (spec 1.1.0 job C, P146). It never downloads or
# installs anything: updating stays a click on HASC update. Run daily and at startup by the
# "HASC update check" task as SYSTEM; never fails the task, an error is written to the file instead.
param(
    [string]$InstallDir = "C:\HASC",
    [string]$Repo = "lystiger/HASC-OCR",
    [string]$Token = ""
)
$ErrorActionPreference = "Stop"
$NonInteractive = $true
. (Join-Path $PSScriptRoot "common.ps1")

$StatusFile = Join-Path $InstallDir "tools\update-status.json"
$CheckTokenFile = Join-Path $InstallDir "tools\github-token-check.dat"
$now = (Get-Date).ToUniversalTime().ToString("o")

$previous = $null
try { $previous = Get-Content $StatusFile -Raw | ConvertFrom-Json } catch { }
$installed = (Get-Content (Join-Path $InstallDir "VERSION.txt") -ErrorAction SilentlyContinue | Select-Object -First 1)
$status = [ordered]@{ installed = $installed; latest = $null; available = $false; checked_at = $now; ok_at = $null; error = $null }

try {
    if (-not $Token) {
        if (-not (Test-Path $CheckTokenFile)) { throw "No GitHub token yet: run HASC update once as an administrator." }
        Add-Type -AssemblyName System.Security
        $bytes = [Security.Cryptography.ProtectedData]::Unprotect([Convert]::FromBase64String((Get-Content $CheckTokenFile -Raw).Trim()), $null, "LocalMachine")
        $Token = [Text.Encoding]::UTF8.GetString($bytes)
    }
    # releases/latest never returns a draft or a prerelease.
    $release = Invoke-GitHub "https://api.github.com/repos/$Repo/releases/latest" "application/vnd.github+json" "" $Token @($CheckTokenFile) | ConvertFrom-Json
    $status.latest = $release.tag_name
    $status.available = [bool]($release.tag_name -and $release.tag_name -ne $installed)
    $status.ok_at = $now
} catch {
    $status.error = $_.Exception.Message
    if ($previous) {
        $status.latest = $previous.latest
        $status.ok_at = $previous.ok_at
        $status.available = [bool]($previous.latest -and $previous.latest -ne $installed)
    }
}

# UTF-8 without a byte order mark, which Windows PowerShell 5.1's Set-Content would add.
try { [IO.File]::WriteAllText($StatusFile, ($status | ConvertTo-Json), (New-Object Text.UTF8Encoding $false)) }
catch { Write-Host "Could not write $($StatusFile): $($_.Exception.Message)" }
Write-Host "installed $($status.installed), latest $($status.latest), available $($status.available)$(if ($status.error) { ", error: $($status.error)" })"
exit 0
