# Installs or updates HASC on this PC, from the unzipped bundle folder: double-click install.cmd.
# First install: creates the hasc account, the folders, the data, the services and the firewall rule.
# Update (HASC already installed): replaces the code and Python, keeps the data and settings.
# Every question can be answered with a parameter instead (used by CI with -NonInteractive).
param(
    [string]$InstallDir = "C:\HASC",
    [string]$Subnet = "",
    [System.Security.SecureString]$HascPassword,
    [System.Security.SecureString]$OpenRouterKey,
    [string]$KnownNames = "",
    [string]$AdminPassword = "",
    [string]$ImportDb = "",
    [string]$ImportPhotos = "",
    [switch]$FreshStart,
    [switch]$SkipKeyExport,
    [switch]$Yes,
    [switch]$NonInteractive
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

if (-not (Test-Admin)) {
    if ($NonInteractive) { throw "Run this as Administrator." }
    Restart-Elevated $PSCommandPath (Get-ForwardArguments $PSBoundParameters)
    exit
}

$Bundle = $PSScriptRoot
$Data = Join-Path $InstallDir "data"
$Config = Join-Path $InstallDir "config"
$Logs = Join-Path $InstallDir "logs"
$Tools = Join-Path $InstallDir "tools"
$Db = Join-Path $Data "hasc.db"
$Py = Join-Path $InstallDir "python\python.exe"
$Code = Join-Path $InstallDir "HASC-OCR"
$Nssm = Join-Path $InstallDir "bin\nssm.exe"
$ToolFiles = @("common.ps1", "install.ps1", "update.ps1", "delete.ps1", "update.cmd", "delete.cmd")

function Set-Folder([string]$Path, [string]$HascRights) {
    if (-not (Test-Path $Path)) { New-Item -ItemType Directory -Path $Path | Out-Null }
    # Set the folder's own ACL, then make everything inside inherit it. (icacls /T would give files
    # nothing: the (OI)(CI) flags do not apply to files, and their inherited entries are gone.)
    Invoke-Native icacls.exe @($Path, "/inheritance:r", "/grant:r",
        "${Account}:(OI)(CI)$HascRights", "${AdminsSid}:(OI)(CI)F", "${SystemSid}:(OI)(CI)F", "/Q")
    if (Get-ChildItem $Path -Force | Select-Object -First 1) {
        Invoke-Native icacls.exe @("$Path\*", "/reset", "/T", "/C", "/Q")
    }
}

function Test-Encrypted([string]$Path) {
    return ((Get-Item -LiteralPath $Path -Force).Attributes -band [IO.FileAttributes]::Encrypted) -ne 0
}

function Wait-Website {
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $r = Invoke-WebRequest "https://127.0.0.1/login" -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw "The website did not answer on https://127.0.0.1/login. See $Logs\web.log"
}

function New-Shortcut([string]$Path, [string]$Target, [string]$Description) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $Target
    # Not inside C:\HASC: a window open in that folder would stop delete from removing it.
    $link.WorkingDirectory = $env:SystemRoot
    $link.Description = $Description
    $link.Save()
}

function Install-Hasc {
    # --- Checks -----------------------------------------------------------------------------------
    foreach ($part in @("python\python.exe", "HASC-OCR\hasc", "bin\nssm.exe")) {
        if (-not (Test-Path (Join-Path $Bundle $part))) { throw "$part is missing next to install.ps1. Unzip the whole bundle first." }
    }
    if ((Resolve-Path $Bundle).Path.TrimEnd("\").StartsWith($InstallDir.TrimEnd("\"), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unzip the bundle somewhere else (for example Downloads), not into $InstallDir."
    }
    $edition = (Get-CimInstance Win32_OperatingSystem).Caption
    $hasEfs = $edition -notmatch "Home"
    $update = (Test-Path $Db) -and (Get-Service $Services[0] -ErrorAction SilentlyContinue)
    $version = (Get-Content (Join-Path $Bundle "VERSION.txt") -ErrorAction SilentlyContinue)
    $installed = (Get-Content (Join-Path $InstallDir "VERSION.txt") -ErrorAction SilentlyContinue)
    if ($update) {
        if (-not $Yes -and -not (Confirm-Choice "Update HASC from $installed to $version?`n`nThe website stops for about a minute. The data and settings are kept.")) { throw "Cancelled." }
    } else {
        $note = ""
        if (-not $hasEfs) { $note = "`n`nThis is $edition, which has no EFS: the data is protected by folder permissions only, so give office staff standard (not administrator) Windows accounts." }
        if (-not (Confirm-Choice "Install HASC $version on this PC, in $InstallDir?$note")) { throw "Cancelled." }
    }
    Write-Host "HASC $version, $edition"

    # --- The hasc account (asked first, so a wrong password changes nothing) ----------------------
    if (-not (Get-LocalUser $Account -ErrorAction SilentlyContinue)) {
        Step "Creating the Windows account '$Account'"
        if (-not $script:HascPassword) {
            $intro = "HASC runs under its own Windows account, '$Account'. Choose a password for it (at least 8 characters, not containing '$Account') and keep it in your password manager: updates ask for it.`n`nNever reset it from another account later: that destroys the encryption keys and the data."
            $script:HascPassword = Read-Text $intro -Secret
            $again = Read-Text "Type the '$Account' password again." -Secret
            if ((Get-Plain $script:HascPassword) -cne (Get-Plain $again)) { throw "The passwords do not match. Nothing was changed." }
        }
        if ((Get-Plain $script:HascPassword).Length -lt 8) { throw "Use a password of at least 8 characters for '$Account'." }
        if ((Get-Plain $script:HascPassword).Contains('"')) { throw "Do not use the character '`"' in the '$Account' password." }
        try {
            New-LocalUser -Name $Account -Password $script:HascPassword -PasswordNeverExpires -UserMayNotChangePassword `
                -Description "Runs the HASC website and worker" | Out-Null
        } catch [Microsoft.PowerShell.Commands.InvalidPasswordException] {
            throw "Windows rejected the password for '$Account': make it longer, mix letters, digits and symbols, and do not include '$Account'."
        }
    } else {
        Step "Checking the '$Account' password"
        Test-HascPassword
    }
    # A password that expired would stop both services at the next restart.
    Set-LocalUser -Name $Account -PasswordNeverExpires $true
    Set-ServiceLogonRight (Get-AccountSid $Account)

    # --- Questions for a first install, before anything is copied ---------------------------------
    $workerEnv = Join-Path $Config "worker.env"
    $namesTarget = Join-Path $Config "known_names.json"
    $needSettings = -not (Test-Path $workerEnv)
    if ($needSettings) {
        if (-not $KnownNames) {
            Show-Info "Next, pick the file known_names.json (the customer and product names), for example on the USB stick."
            $KnownNames = Select-File "Pick known_names.json" "known_names.json|known_names.json|JSON files|*.json" (Join-Path $Bundle "known_names.json")
        }
        if (-not (Test-Path -LiteralPath $KnownNames)) { throw "known_names.json not found at $KnownNames" }
        if (-not $OpenRouterKey) { $OpenRouterKey = Read-Text "Paste the OpenRouter API key (it starts with sk-or-). It is stored encrypted, readable only by '$Account'." -Secret }
        if ((Get-Plain $OpenRouterKey).Trim().Length -eq 0) { throw "The OpenRouter key is empty." }
    }
    $needData = -not (Test-Path $Db)
    if ($needData -and -not $ImportDb -and -not $FreshStart -and -not $NonInteractive) {
        if (Confirm-Choice "Bring existing data (a hasc.db backup and its photos folder)?`n`nYes: pick the backup. No: start with an empty database." $false) {
            $ImportDb = Select-File "Pick the hasc.db backup" "hasc.db|hasc.db|SQLite database|*.db"
            $guess = Join-Path (Split-Path $ImportDb -Parent) "photos"
            if (Test-Path $guess) { $ImportPhotos = $guess }
            else { $ImportPhotos = Select-Folder "Pick the photos folder that belongs to this backup" }
        }
    }

    # --- Code and Python (install and update) -----------------------------------------------------
    Step "Stopping the services"
    foreach ($s in $Services) {
        if (Get-Service $s -ErrorAction SilentlyContinue) { Stop-Service $s -Force }
    }
    if ($update) {
        # The new code migrates the database when it starts: keep a copy to go back to if that fails.
        # Copied as hasc, since the database is encrypted to it; the copy stays encrypted in $Data.
        $backup = Join-Path $Data ("backups\before-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
        Step "Backing up the database to $backup"
        $copy = "mkdir `"$backup`""
        foreach ($suffix in @("", "-wal", "-shm")) {
            if (Test-Path "$Db$suffix") { $copy += " && copy /y `"$Db$suffix`" `"$backup`" >nul" }
        }
        Invoke-AsHasc $copy
        # Only the last 5 are kept, so the disk does not fill up over the years. A locked old backup is
        # left for the next update rather than stopping this one with the website down.
        Get-ChildItem (Join-Path $Data "backups") -Directory -Filter "before-*" | Sort-Object Name -Descending |
            Select-Object -Skip 5 | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }

    Step "Copying Python, the code and NSSM into $InstallDir"
    if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir | Out-Null }
    foreach ($part in @("python", "HASC-OCR", "bin")) {
        & robocopy.exe (Join-Path $Bundle $part) (Join-Path $InstallDir $part) /MIR /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Copying $part failed (robocopy exit code $LASTEXITCODE)" }
    }
    New-Item -ItemType Directory -Path $Tools -Force | Out-Null
    foreach ($f in $ToolFiles) { Copy-Item (Join-Path $Bundle $f) $Tools -Force }
    Copy-Item (Join-Path $Bundle "VERSION.txt") $InstallDir -Force -ErrorAction SilentlyContinue
    foreach ($part in @("python", "HASC-OCR", "bin", "tools")) { Get-ChildItem (Join-Path $InstallDir $part) -Recurse -File | Unblock-File }

    Step "Setting folder permissions"
    # C:\HASC itself: staff may not add, rename or replace anything in it.
    Invoke-Native icacls.exe @($InstallDir, "/inheritance:r", "/grant:r",
        "${Account}:(OI)(CI)RX", "${AdminsSid}:(OI)(CI)F", "${SystemSid}:(OI)(CI)F", "/Q")
    Set-Folder $Data "F"
    Set-Folder $Config "F"
    Set-Folder $Logs "F"
    foreach ($part in @("python", "HASC-OCR", "bin", "tools")) { Set-Folder (Join-Path $InstallDir $part) "RX" }
    Protect-TokenFile

    # --- Settings and data (first install) --------------------------------------------------------
    if ($needSettings) {
        Step "Worker settings"
        Copy-Item -LiteralPath $KnownNames $namesTarget -Force
        Set-Content $workerEnv -Encoding ASCII -Value @(
            "OPENROUTER_API_KEY=$((Get-Plain $OpenRouterKey).Trim())",
            "HASC_READER_MODEL=google/gemini-3.8-flash",
            "HASC_READER_FALLBACK=qwen/qwen3.8-27b",
            "HASC_KNOWN_NAMES=$namesTarget")
    }

    if ($needData) {
        if ($ImportDb) {
            Step "Importing $ImportDb"
            foreach ($suffix in @("", "-wal", "-shm")) {
                if (Test-Path -LiteralPath "$ImportDb$suffix") { Copy-Item -LiteralPath "$ImportDb$suffix" "$Db$suffix" }
            }
            if ($ImportPhotos -and (Test-Path -LiteralPath $ImportPhotos)) {
                & robocopy.exe $ImportPhotos (Join-Path $Data "photos") /E /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
                if ($LASTEXITCODE -ge 8) { throw "Copying the photos failed (robocopy exit code $LASTEXITCODE)" }
            }
            # Stored photo paths point at the old machine's photos folder: keep the part after
            # ".../photos/" and put it under this PC's photos folder.
            $env:HASC_NEW = (Join-Path $Data "photos") + "\"
            $env:HASC_DB = $Db
            Invoke-Native $Py @("-c", "import os, re, sqlite3; c = sqlite3.connect(os.environ['HASC_DB']); new = os.environ['HASC_NEW']; fix = lambda p: p and (new + re.split(r'(?i)(?:^|[\\/])photos[\\/]', p)[-1].replace('/', '\\') if re.search(r'(?i)(?:^|[\\/])photos[\\/]', p) else p); rows = c.execute('SELECT id, path, small_copy_path FROM photos').fetchall(); [c.execute('UPDATE photos SET path = ?, small_copy_path = ? WHERE id = ?', (fix(p), fix(s), i)) for i, p, s in rows]; c.commit(); print(len(rows), 'photo paths updated'); c.close()")
        } else {
            Step "Creating the database and the first admin account"
            $cliArgs = @("$Code\scripts\create_user.py", "--db", $Db, "--username", "admin", "--owner")
            if ($AdminPassword) { $cliArgs += @("--password", $AdminPassword) }
            $out = (& $Py @cliArgs 2>&1) | Out-String
            if ($LASTEXITCODE -ne 0) { throw "Creating the admin account failed." }
            $m = [regex]::Match($out, "Temporary password: (\S+)")
            if ($m.Success) {
                Show-Secret "The first account is 'admin'. Its first password is below: write it down now, it is shown only once. You choose your own password at the first login." $m.Groups[1].Value
            }
        }
    }
    New-Item -ItemType Directory -Path (Join-Path $Data "photos") -Force | Out-Null

    # --- Encryption (Pro only) --------------------------------------------------------------------
    # Decided from the files themselves, so a run that stopped half way still encrypts on the next run.
    $plainFiles = @($Data, $Config, $Db, $workerEnv, $namesTarget) | Where-Object { -not (Test-Encrypted $_) }
    if ($hasEfs -and $plainFiles) {
        Step "Encrypting the data and settings to '$Account' (EFS)"
        Invoke-AsHasc "cipher /e /s:`"$Data`" >nul && cipher /e /s:`"$Config`" >nul" $Logs
        if (-not $SkipKeyExport) {
            Step "Backing up the encryption key"
            $pfx = Join-Path $Logs "hasc-efs-key"
            Show-Info "The data is now encrypted. Next, back up its key.`n`nA black window opens as '$Account': type a password for the key file there, twice. Keep that password with the '$Account' password."
            Invoke-AsHasc "cipher /x `"$pfx`"" $Logs -Visible
            if (-not $NonInteractive) {
                Start-Process explorer.exe "/select,`"$pfx.pfx`""
                Show-Info "Copy hasc-efs-key.pfx (the selected file) to a USB stick now, and keep the stick away from this PC. Without it, a broken '$Account' profile means the data is lost.`n`nPress OK once it is copied: the file is then deleted from this PC."
                Remove-Item "$pfx.pfx" -Force -ErrorAction SilentlyContinue
            }
        }
    }

    # --- HTTPS certificates -------------------------------------------------------------------------
    # Made as hasc, so on Pro the keys are encrypted to it like the rest of the config folder. The two
    # certificates are public, so they are decrypted for administrators to trust the authority. An
    # update keeps the authority (phones trust it) and renews the PC's certificate only if needed.
    Step "HTTPS certificates"
    $ips = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" }).IPAddress
    $certs = "$Py -m hasc.certs --dir `"$Config`" --name $env:COMPUTERNAME" + (($ips | ForEach-Object { " --ip $_" }) -join "")
    if ($hasEfs) { $certs += " && cipher /d /a `"$Config\ca.crt`" `"$Config\server.crt`" >nul" }
    Invoke-AsHasc $certs $Code
    Import-Certificate -FilePath (Join-Path $Config "ca.crt") -CertStoreLocation Cert:\LocalMachine\Root | Out-Null

    # --- Services ---------------------------------------------------------------------------------
    Step "Installing the services"
    $plain = Get-Plain (Get-HascCredential).Password
    foreach ($s in $Services) {
        if (Get-Service $s -ErrorAction SilentlyContinue) { Invoke-Native $Nssm @("remove", $s, "confirm") }
    }
    Invoke-Native $Nssm @("install", "hasc-web", $Py, "-m uvicorn hasc.web:create_app --factory --host 0.0.0.0 --port 443 --ssl-keyfile `"$Config\server.key`" --ssl-certfile `"$Config\server.crt`"")
    Invoke-Native $Nssm @("set", "hasc-web", "AppEnvironmentExtra", "HASC_DB_PATH=$Db", "HASC_HTTPS=1")
    # Port 8080 only sends browsers on to HTTPS and hands out the authority (hasc/web/redirect.py).
    Invoke-Native $Nssm @("install", "hasc-redirect", $Py, "-m uvicorn hasc.web.redirect:app --host 0.0.0.0 --port 8080")
    Invoke-Native $Nssm @("set", "hasc-redirect", "AppEnvironmentExtra", "HASC_CA_CERT=$Config\ca.crt")
    Invoke-Native $Nssm @("install", "hasc-worker", $Py, "-u -m hasc.worker")
    Invoke-Native $Nssm @("set", "hasc-worker", "AppEnvironmentExtra", "HASC_DB_PATH=$Db", "HASC_WORKER_ENV=$workerEnv")
    foreach ($s in $Services) {
        $log = Join-Path $Logs ($s.Replace("hasc-", "") + ".log")
        Invoke-Native $Nssm @("set", $s, "AppDirectory", $Code)
        Invoke-Native $Nssm @("set", $s, "ObjectName", ".\$Account", $plain) -Secret
        Invoke-Native $Nssm @("set", $s, "Start", "SERVICE_AUTO_START")
        Invoke-Native $Nssm @("set", $s, "AppStdout", $log)
        Invoke-Native $Nssm @("set", $s, "AppStderr", $log)
        Invoke-Native $Nssm @("set", $s, "AppRotateFiles", "1")
        Invoke-Native $Nssm @("set", $s, "AppRotateBytes", "10485760")
    }
    $plain = $null

    # --- Network and power ------------------------------------------------------------------------
    Step "Firewall and power"
    $rule = Get-NetFirewallRule -DisplayName "HASC website" -ErrorAction SilentlyContinue
    if ($rule -and -not $Subnet) {
        Write-Host "Keeping the firewall rule for $(($rule | Get-NetFirewallAddressFilter).RemoteAddress)"
        # 1.0.x opened 8080 only; HTTPS adds 443.
        Set-NetFirewallRule -DisplayName "HASC website" -LocalPort 443, 8080
    } else {
        if (-not $Subnet) {
            $ip = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -ne "WellKnown" -and $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1
            $guess = "192.168.1.0/24"
            if ($ip -and $ip.PrefixLength -eq 24) { $guess = ($ip.IPAddress -replace "\.\d+$", ".0") + "/24" }
            $Subnet = Read-Text "Which network may open the website? The guess below is this PC's own office network; keep it unless you know better." $guess
        }
        if ($rule) { $rule | Remove-NetFirewallRule }
        New-NetFirewallRule -DisplayName "HASC website" -Direction Inbound -Protocol TCP -LocalPort 443, 8080 `
            -RemoteAddress $Subnet -Profile Private, Domain -Action Allow | Out-Null
    }
    if (-not $update) {
        foreach ($net in Get-NetConnectionProfile) {
            if ($net.NetworkCategory -eq "Public") {
                # No by default: a network that is not the office's should stay Public.
                if (Confirm-Choice "Windows treats the network '$($net.Name)' as Public, so phones cannot reach the website.`n`nSet it to Private? (Choose Yes for the office network.)" $false) {
                    Set-NetConnectionProfile -InterfaceIndex $net.InterfaceIndex -NetworkCategory Private
                }
            }
        }
    }
    Invoke-Native powercfg.exe @("/change", "standby-timeout-ac", "0")
    Invoke-Native powercfg.exe @("/change", "hibernate-timeout-ac", "0")

    # --- Shortcuts --------------------------------------------------------------------------------
    if (-not $NonInteractive) {
        $desktop = [Environment]::GetFolderPath("Desktop")
        $menu = Join-Path ([Environment]::GetFolderPath("Programs")) "HASC"
        New-Item -ItemType Directory -Path $menu -Force | Out-Null
        foreach ($dir in @($desktop, $menu)) {
            Set-Content (Join-Path $dir "HASC website.url") -Encoding ASCII -Value @("[InternetShortcut]", "URL=$WebsiteUrl")
            New-Shortcut (Join-Path $dir "HASC update.lnk") (Join-Path $Tools "update.cmd") "Update HASC to the latest release"
        }
        New-Shortcut (Join-Path $menu "HASC delete.lnk") (Join-Path $Tools "delete.cmd") "Remove HASC from this PC"
    }

    # --- Start and check --------------------------------------------------------------------------
    Step "Starting the services"
    foreach ($s in $Services) { Start-Service $s }
    Wait-Website
    Start-Sleep -Seconds 5
    foreach ($s in $Services) {
        $status = (Get-Service $s).Status
        if ($status -ne "Running") { throw "$s is $status. See the log in $Logs" }
    }

    $addresses = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" }).IPAddress
    $phones = ($addresses | ForEach-Object { "  https://${_}/login" }) -join "`n"
    $trust = ($addresses | ForEach-Object { "  http://${_}:8080/ca.crt" }) -join "`n"
    $done = "HASC $version is running.`n`nPhones on the office network open:`n$phones`n`nEach phone trusts the website once, from (see the README):`n$trust"
    if (-not $update) {
        $done += "`n`nStill to do by hand:`n- BIOS: set 'Restore on AC power loss' to Power On.`n- Router: give this PC a fixed address.`n- Windows Update: pause it; install updates in planned shutdowns."
    }
    if (-not $NonInteractive) { Start-Process $WebsiteUrl }
    Show-Info $done
}

try {
    Install-Hasc
} catch {
    Exit-WithError $_ "The log of each service is in $Logs."
}
# update.ps1 and CI read $LASTEXITCODE; without this it would be the last native command's.
exit 0
