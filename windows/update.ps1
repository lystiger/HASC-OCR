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
    Restart-Elevated $PSCommandPath @()
    exit
}

$TokenFile = Join-Path $InstallDir "tools\github-token.dat"

function Get-Token {
    if ($Token) { return $Token }
    if (Test-Path $TokenFile) {
        try { return Get-Plain (Get-Content $TokenFile | ConvertTo-SecureString) }
        catch { Remove-Item $TokenFile -Force }  # saved by another Windows user: ask again
    }
    $help = "Updates come from GitHub. Paste a read-only GitHub token (asked once).`n`nTo make one: github.com, Settings, Developer settings, Fine-grained tokens, Generate. Repository access: only HASC-OCR. Permissions: Contents, Read-only."
    $plain = (Get-Plain (Read-Text $help -Secret)).Trim()
    if ($plain.Length -eq 0) { throw "No token given." }
    ConvertTo-SecureString $plain -AsPlainText -Force | ConvertFrom-SecureString | Set-Content $TokenFile -Encoding ASCII
    Protect-TokenFile
    return $plain
}

function Protect-TokenFile {
    # Administrators only. Runs again after an update, whose installer resets the tools folder.
    if (Test-Path $TokenFile) {
        Invoke-Native icacls.exe @($TokenFile, "/inheritance:r", "/grant:r", "${AdminsSid}:F", "${SystemSid}:F", "/Q") | Out-Null
    }
}

function Invoke-GitHub([string]$Url, [string]$Accept, [string]$OutFile) {
    # GitHub answers a release download with a redirect to its file store, which must be fetched
    # without the token: so redirects are followed by hand.
    $headers = @{ "Authorization" = "Bearer $(Get-Token)"; "User-Agent" = "hasc-update"; "Accept" = $Accept }
    $request = [System.Net.HttpWebRequest]::Create($Url)
    $request.AllowAutoRedirect = $false
    foreach ($k in $headers.Keys) {
        if ($k -eq "Accept") { $request.Accept = $headers[$k] }
        elseif ($k -eq "User-Agent") { $request.UserAgent = $headers[$k] }
        else { $request.Headers.Add($k, $headers[$k]) }
    }
    try { $response = $request.GetResponse() }
    catch [System.Net.WebException] {
        $code = [int]$_.Exception.Response.StatusCode
        if ($code -eq 401) {
            Remove-Item $TokenFile -Force -ErrorAction SilentlyContinue
            throw "GitHub refused the token (expired or wrong). Run the update again and paste a new read-only token."
        }
        if ($code -eq 403) { throw "GitHub refused the request (HTTP 403): the token may lack Contents read on HASC-OCR, or GitHub is limiting requests. Try again later." }
        if ($code -eq 404) { throw "GitHub found no release (HTTP 404), or the token cannot see HASC-OCR." }
        throw
    }
    $status = [int]$response.StatusCode
    if ($status -ge 300 -and $status -lt 400) {
        $location = $response.Headers["Location"]
        $response.Close()
        Invoke-WebRequest $location -OutFile $OutFile -UseBasicParsing
        return
    }
    try {
        if ($OutFile) {
            $file = [System.IO.File]::Create($OutFile)
            try { $response.GetResponseStream().CopyTo($file) } finally { $file.Close() }
            return
        }
        return (New-Object System.IO.StreamReader($response.GetResponseStream())).ReadToEnd()
    } finally {
        $response.Close()
    }
}

function Update-Hasc {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $installed = (Get-Content (Join-Path $InstallDir "VERSION.txt") -ErrorAction SilentlyContinue)
    $work = Join-Path $env:TEMP "hasc-update"
    if (Test-Path $work) { Remove-Item $work -Recurse -Force }
    New-Item -ItemType Directory -Path $work | Out-Null

    if (-not $ZipPath) {
        Step "Asking GitHub for the latest release"
        $release = Invoke-GitHub "https://api.github.com/repos/$Repo/releases/latest" "application/vnd.github+json" "" | ConvertFrom-Json
        $asset = $release.assets | Where-Object { $_.name -like "hasc-*-win64.zip" } | Select-Object -First 1
        if (-not $asset) { throw "Release $($release.tag_name) has no Windows bundle yet. Try again in a few minutes." }
        Write-Host "Latest: $($release.tag_name) ($($asset.name)); installed: $installed"
        if ($CheckOnly) { return }
        if ($release.tag_name -eq $installed -and -not $Force) {
            Show-Info "HASC is up to date ($installed)."
            return
        }
        if (-not (Confirm-Choice "Update HASC from $installed to $($release.tag_name)?`n`nThe website stops for about a minute. The data and settings are kept.")) { return }
        Step "Downloading $($asset.name)"
        $ZipPath = Join-Path $work $asset.name
        Invoke-GitHub $asset.url "application/octet-stream" $ZipPath
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
