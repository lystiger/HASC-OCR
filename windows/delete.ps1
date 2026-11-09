# Removes HASC from this PC: double-click delete.cmd (in C:\HASC\tools, or the "HASC delete" entry in
# the Start menu). It asks whether to keep the database and photos (moved, readable, to a backup folder
# that install.cmd can bring back) or to erase them. Everything else goes: the services, the firewall
# rule, C:\HASC, the shortcuts and the hasc Windows account with its profile and encryption key.
param(
    [string]$InstallDir = "C:\HASC",
    [string]$KeepDataTo = "",
    [System.Security.SecureString]$HascPassword,
    # The hasc password, encrypted to this Windows user, when the script restarts itself.
    [string]$HascPasswordSaved = "",
    [switch]$EraseData,
    [switch]$NonInteractive
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

if (-not (Test-Admin) -and $NonInteractive) { throw "Run this as Administrator." }
# The whole folder is deleted, so it must never be a drive root.
$InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd("\")
if ($InstallDir -eq [IO.Path]::GetPathRoot($InstallDir).TrimEnd("\")) { throw "-InstallDir may not be a drive root: $InstallDir" }

# Run from a copy outside C:\HASC, since C:\HASC is about to be deleted: no process of ours may keep
# a file or its working folder inside it.
$here = (Resolve-Path $PSScriptRoot).Path
if ($here.StartsWith($InstallDir.TrimEnd("\"), [StringComparison]::OrdinalIgnoreCase)) {
    $copy = Join-Path $env:TEMP "hasc-delete"
    New-Item -ItemType Directory -Path $copy -Force | Out-Null
    Copy-Item (Join-Path $here "common.ps1"), (Join-Path $here "delete.ps1") $copy -Force
    $forward = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$copy\delete.ps1`"", "-InstallDir", "`"$InstallDir`"")
    if ($NonInteractive) { $forward += "-NonInteractive" }
    if ($EraseData) { $forward += "-EraseData" }
    if ($KeepDataTo) { $forward += @("-KeepDataTo", "`"$KeepDataTo`"") }
    if ($HascPassword) { $forward += @("-HascPasswordSaved", (ConvertFrom-SecureString $HascPassword)) }
    $start = @{ FilePath = "powershell.exe"; ArgumentList = $forward; WorkingDirectory = $env:TEMP }
    if (-not (Test-Admin)) { $start.Verb = "RunAs" }
    if ($NonInteractive) {
        $p = Start-Process @start -Wait -PassThru -NoNewWindow
        exit $p.ExitCode
    }
    # Not waiting lets this window (and the cmd window that started it) close, so neither holds C:\HASC\tools.
    Start-Process @start
    exit
}
if (-not (Test-Admin)) {
    Restart-Elevated $PSCommandPath (Get-ForwardArguments $PSBoundParameters)
    exit
}
if ($HascPasswordSaved) { $HascPassword = ConvertTo-SecureString $HascPasswordSaved }

$Data = Join-Path $InstallDir "data"
$Nssm = Join-Path $InstallDir "bin\nssm.exe"

function Remove-Hasc {
    Set-Location $env:SystemRoot
    [Environment]::CurrentDirectory = $env:SystemRoot
    $hasData = (Test-Path $Data) -and [bool](Get-ChildItem $Data -Recurse -File -Force | Select-Object -First 1)
    $userExists = [bool](Get-LocalUser $Account -ErrorAction SilentlyContinue)

    if (-not (Confirm-Choice "Remove HASC from this PC?`n`nThis removes the website, the worker, $InstallDir, the shortcuts and the Windows account '$Account'." $true -Warning)) { return }

    $erase = $EraseData
    if ($hasData -and -not $EraseData -and -not $KeepDataTo -and -not $NonInteractive) {
        $erase = -not (Confirm-Choice "Keep the database and photos?`n`nYes: they are moved to a backup folder on C:\, readable, so install.cmd can bring them back.`nNo: they are erased." $true)
        if ($erase -and -not (Confirm-Choice "Erase every sheet, photo and account for good? This cannot be undone." $false -Warning)) { return }
    }
    $keep = $hasData -and -not $erase
    if ($keep -and -not $KeepDataTo) { $KeepDataTo = "C:\HASC-backup-" + (Get-Date -Format "yyyyMMdd-HHmm") }
    if ($keep) {
        $KeepDataTo = [IO.Path]::GetFullPath($KeepDataTo).TrimEnd("\")
        # A backup inside the install folder would be deleted with it; one on top of an old backup would overwrite it.
        if (($KeepDataTo + "\").StartsWith($InstallDir + "\", [StringComparison]::OrdinalIgnoreCase)) { throw "Keep the data outside $InstallDir, not in $KeepDataTo. Nothing was removed." }
        if ((Test-Path $KeepDataTo) -and (Get-ChildItem $KeepDataTo -Force | Select-Object -First 1)) { throw "$KeepDataTo is not empty: pick a new folder for the backup. Nothing was removed." }
    }

    # The data is encrypted to hasc, so decrypting it needs hasc's password, before the account goes.
    if ($keep -and $userExists) {
        Step "Checking the '$Account' password"
        Test-HascPassword
    }
    # Without hasc nobody can decrypt it any more: moving it would give a backup no one can read.
    if ($keep -and -not $userExists) {
        $locked = Get-ChildItem $Data -Recurse -File -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::Encrypted } | Select-Object -First 1
        if ($locked) { throw "The '$Account' account is gone, so the encrypted data in $Data cannot be decrypted (for example $($locked.FullName)). Import the EFS key backup (hasc-efs-key.pfx) as an administrator first, or erase the data. Nothing was removed." }
    }

    Step "Stopping the services"
    foreach ($s in $Services) {
        if (Get-Service $s -ErrorAction SilentlyContinue) { Stop-Service $s -Force }
    }

    if ($keep) {
        Step "Moving the data to $KeepDataTo"
        # The backup folder is locked to administrators before any decrypted file goes into it.
        New-Item -ItemType Directory -Path $KeepDataTo -Force | Out-Null
        Invoke-Native icacls.exe @($KeepDataTo, "/inheritance:r", "/grant:r", "${AdminsSid}:(OI)(CI)F", "${SystemSid}:(OI)(CI)F", "/Q")
        # cipher /d leaves files that are not encrypted alone, so it runs whenever hasc exists.
        if ($userExists) { Invoke-AsHasc "cipher /d /s:`"$Data`" >nul" }
        & robocopy.exe $Data $KeepDataTo /E /MOVE /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Moving the data stopped part way (robocopy exit code $LASTEXITCODE). Nothing else was removed, but the data is now split between $Data and $KeepDataTo, and the website and worker are stopped. Move the files from $KeepDataTo back into $Data, then run install.cmd to start HASC again, or run delete again." }
        Invoke-Native icacls.exe @("$KeepDataTo\*", "/reset", "/T", "/C", "/Q")
    }

    Step "Removing the services"
    foreach ($s in $Services) {
        if (Get-Service $s -ErrorAction SilentlyContinue) {
            if (Test-Path $Nssm) { Invoke-Native $Nssm @("remove", $s, "confirm") }
            else { Invoke-Native sc.exe @("delete", $s) }
        }
    }

    Step "Removing the firewall rule and $InstallDir"
    Get-NetFirewallRule -DisplayName "HASC website" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -eq $CaSubject } | Remove-Item
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
        # The profile holds hasc's encryption key. Windows can keep it loaded for a while after the
        # services stop, so if it is still in use, it is removed at the next start instead.
        $removed = $false
        for ($i = 1; $i -le 5 -and -not $removed; $i++) {
            try {
                Get-CimInstance Win32_UserProfile | Where-Object { $_.SID -eq $sid } | Remove-CimInstance
                $removed = $true
            } catch { Start-Sleep -Seconds 3 }
        }
        if (-not $removed) {
            # A task that runs as SYSTEM at each start until it removes the profile, then deletes itself.
            # A failed removal stops the command before the unregister, so the next start tries again.
            $task = "HASC remove profile $sid"
            $cmd = "`$ErrorActionPreference = 'Stop'; Get-CimInstance Win32_UserProfile | Where-Object { `$_.SID -eq '$sid' } | Remove-CimInstance; Unregister-ScheduledTask -TaskName '$task' -Confirm:`$false"
            $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$cmd`""
            Register-ScheduledTask -TaskName $task -Action $action -Trigger (New-ScheduledTaskTrigger -AtStartup) `
                -Principal (New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest) -Force | Out-Null
            $script:profileLater = $true
        }
        Remove-LocalUser $Account
    }

    $done = "HASC is removed from this PC."
    if ($keep) { $done += "`n`nThe database and photos are in $KeepDataTo. To bring them back, run install.cmd and pick $KeepDataTo\hasc.db." }
    elseif ($hasData) { $done += "`n`nThe database and photos were erased." }
    if ($script:profileLater) { $done += "`n`nThe '$Account' profile (with its encryption key) was still in use: it is removed at the next restart." }
    $done += "`n`nLeft as they were: the power settings (no sleep) and the network set to Private."
    Show-Info $done
}

try {
    Remove-Hasc
} catch {
    Exit-WithError $_
}
# A caller reads $LASTEXITCODE; without this it would be the last native command's (robocopy's 1 is success).
exit 0
