# Removes HASC from this PC: double-click delete.cmd (in C:\HASC\tools, or the "HASC delete" entry in
# the Start menu). It asks whether to keep the database and photos (moved, readable, to a backup folder
# that install.cmd can bring back) or to erase them. Everything else goes: the services, the firewall
# rule, C:\HASC, the shortcuts and the hasc Windows account with its profile and encryption key.
param(
    [string]$InstallDir = "C:\HASC",
    [string]$KeepDataTo = "",
    [System.Security.SecureString]$HascPassword,
    [switch]$EraseData,
    [switch]$NonInteractive
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

# Run from a copy outside C:\HASC, since C:\HASC is about to be deleted.
$here = (Resolve-Path $PSScriptRoot).Path
if ($here.StartsWith($InstallDir.TrimEnd("\"), [StringComparison]::OrdinalIgnoreCase)) {
    $copy = Join-Path $env:TEMP "hasc-delete"
    New-Item -ItemType Directory -Path $copy -Force | Out-Null
    Copy-Item (Join-Path $here "common.ps1"), (Join-Path $here "delete.ps1") $copy -Force
    $forward = @("-InstallDir", "`"$InstallDir`"")
    if ($NonInteractive) { $forward += "-NonInteractive" }
    if ($EraseData) { $forward += "-EraseData" }
    if ($KeepDataTo) { $forward += @("-KeepDataTo", "`"$KeepDataTo`"") }
    if (Test-Admin) {
        & (Join-Path $copy "delete.ps1") -InstallDir $InstallDir -KeepDataTo $KeepDataTo -HascPassword $HascPassword -EraseData:$EraseData -NonInteractive:$NonInteractive
        exit $LASTEXITCODE
    }
    Restart-Elevated (Join-Path $copy "delete.ps1") $forward
    exit
}
if (-not (Test-Admin)) {
    if ($NonInteractive) { throw "Run this as Administrator." }
    Restart-Elevated $PSCommandPath @()
    exit
}

$Data = Join-Path $InstallDir "data"
$Nssm = Join-Path $InstallDir "bin\nssm.exe"

function Remove-Hasc {
    Set-Location $env:SystemRoot
    $hasData = Test-Path (Join-Path $Data "hasc.db")
    $userExists = [bool](Get-LocalUser $Account -ErrorAction SilentlyContinue)

    if (-not (Confirm-Choice "Remove HASC from this PC?`n`nThis removes the website, the worker, $InstallDir, the shortcuts and the Windows account '$Account'." $true -Warning)) { return }

    $erase = $EraseData
    if ($hasData -and -not $EraseData -and -not $KeepDataTo -and -not $NonInteractive) {
        $erase = -not (Confirm-Choice "Keep the database and photos?`n`nYes: they are moved to a backup folder on C:\, readable, so install.cmd can bring them back.`nNo: they are erased." $true)
        if ($erase -and -not (Confirm-Choice "Erase every sheet, photo and account for good? This cannot be undone." $false -Warning)) { return }
    }
    $keep = $hasData -and -not $erase
    if ($keep -and -not $KeepDataTo) { $KeepDataTo = "C:\HASC-backup-" + (Get-Date -Format "yyyyMMdd-HHmm") }

    # The data is encrypted to hasc, so decrypting it needs hasc's password, before the account goes.
    if ($keep -and $userExists) {
        Step "Checking the '$Account' password"
        Test-HascPassword
    }

    Step "Stopping and removing the services"
    foreach ($s in $Services) {
        if (Get-Service $s -ErrorAction SilentlyContinue) {
            Stop-Service $s -Force
            if (Test-Path $Nssm) { Invoke-Native $Nssm @("remove", $s, "confirm") }
            else { Invoke-Native sc.exe @("delete", $s) }
        }
    }

    if ($keep) {
        Step "Moving the data to $KeepDataTo"
        if ($userExists) { Invoke-AsHasc "cipher /d /s:`"$Data`" >nul" }
        & robocopy.exe $Data $KeepDataTo /E /MOVE /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Moving the data failed (robocopy exit code $LASTEXITCODE). Nothing else was removed; the services are stopped." }
        # Only administrators may open the backup.
        Invoke-Native icacls.exe @($KeepDataTo, "/inheritance:r", "/grant:r", "${AdminsSid}:(OI)(CI)F", "${SystemSid}:(OI)(CI)F", "/Q")
        Invoke-Native icacls.exe @("$KeepDataTo\*", "/reset", "/T", "/C", "/Q")
    }

    Step "Removing the firewall rule and $InstallDir"
    Get-NetFirewallRule -DisplayName "HASC website" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    if (Test-Path $InstallDir) {
        Remove-Item $InstallDir -Recurse -Force
    }

    Step "Removing the shortcuts"
    $desktop = [Environment]::GetFolderPath("Desktop")
    Remove-Item (Join-Path $desktop "HASC website.url"), (Join-Path $desktop "HASC update.lnk") -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path ([Environment]::GetFolderPath("Programs")) "HASC") -Recurse -Force -ErrorAction SilentlyContinue

    if ($userExists) {
        Step "Removing the Windows account '$Account' and its profile"
        $sid = Get-AccountSid $Account
        Set-ServiceLogonRight $sid -Remove
        Remove-LocalUser $Account
        # Windows may hold the profile for a few seconds after the last process that used it.
        for ($i = 1; $i -le 5; $i++) {
            try { Get-CimInstance Win32_UserProfile | Where-Object { $_.SID -eq $sid } | Remove-CimInstance; break }
            catch { if ($i -eq 5) { Write-Host "The '$Account' profile folder is still in use; it goes at the next restart, or delete C:\Users\$Account by hand." -ForegroundColor Yellow } else { Start-Sleep -Seconds 3 } }
        }
    }

    $done = "HASC is removed from this PC."
    if ($keep) { $done += "`n`nThe database and photos are in $KeepDataTo. To bring them back, run install.cmd and pick $KeepDataTo\hasc.db." }
    elseif ($hasData) { $done += "`n`nThe database and photos were erased." }
    $done += "`n`nLeft as they were: the power settings (no sleep) and the network set to Private."
    Show-Info $done
}

try {
    Remove-Hasc
} catch {
    Show-Error $_.Exception.Message
    exit 1
}
