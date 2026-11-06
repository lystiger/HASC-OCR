# Installs or updates HASC on this PC. Run as Administrator from the unzipped bundle folder:
#   right-click install.cmd, Run as administrator
# First install: creates the hasc account, the folders, the data, the services and the firewall rule.
# Update (HASC already installed): replaces the code and Python, keeps the data and settings.
# Every question can be answered with a parameter instead (used by CI).
param(
    [string]$InstallDir = "C:\HASC",
    [string]$Subnet = "",
    [System.Security.SecureString]$HascPassword,
    [System.Security.SecureString]$OpenRouterKey,
    [string]$KnownNames = "",
    [string]$AdminPassword = "",
    [string]$ImportDb = "",
    [string]$ImportPhotos = "",
    [string]$OldPhotosPrefix = "",
    [switch]$SkipKeyExport,
    [switch]$NonInteractive
)
$ErrorActionPreference = "Stop"

$Bundle = $PSScriptRoot
$Data = Join-Path $InstallDir "data"
$Config = Join-Path $InstallDir "config"
$Logs = Join-Path $InstallDir "logs"
$Db = Join-Path $Data "hasc.db"
$Py = Join-Path $InstallDir "python\python.exe"
$Code = Join-Path $InstallDir "HASC-OCR"
$Nssm = Join-Path $InstallDir "bin\nssm.exe"
$Account = "hasc"
$Services = @("hasc-web", "hasc-worker")
# Well-known SIDs, so the script works whatever language Windows is in.
$AdminsSid = "*S-1-5-32-544"
$SystemSid = "*S-1-5-18"

function Step([string]$Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }

function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

function Ask([string]$Question, [string]$Default) {
    if ($NonInteractive) { return $Default }
    $answer = Read-Host "$Question [$Default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer.Trim()
}

function Get-Plain([System.Security.SecureString]$Secure) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Get-HascCredential {
    if (-not $script:HascPassword) {
        $script:HascPassword = Read-Host "Password for the Windows account '$Account'" -AsSecureString
    }
    return New-Object System.Management.Automation.PSCredential (".\$Account", $script:HascPassword)
}

function Invoke-AsHasc([string]$Command, [switch]$Visible) {
    # Runs a command as hasc with its profile loaded, so EFS encrypts to hasc's key.
    $style = "Hidden"
    if ($Visible) { $style = "Normal" }
    $p = Start-Process cmd.exe -ArgumentList "/c $Command" -Credential (Get-HascCredential) -LoadUserProfile `
        -WorkingDirectory $Logs -WindowStyle $style -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "As ${Account}: '$Command' failed with exit code $($p.ExitCode)" }
}

function Grant-ServiceLogonRight([string]$User) {
    $sid = (New-Object System.Security.Principal.NTAccount($User)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    $cfg = Join-Path $env:TEMP "hasc-rights.inf"
    $sdb = Join-Path $env:TEMP "hasc-rights.sdb"
    Invoke-Native secedit.exe @("/export", "/cfg", $cfg, "/areas", "USER_RIGHTS", "/quiet")
    $lines = Get-Content $cfg
    $line = $lines | Where-Object { $_ -like "SeServiceLogonRight*" }
    if ($line -and $line -like "*$sid*") { return }
    if ($line) {
        $lines = $lines | ForEach-Object { if ($_ -like "SeServiceLogonRight*") { "$_,*$sid" } else { $_ } }
    } else {
        $lines = $lines | ForEach-Object { $_; if ($_ -eq "[Privilege Rights]") { "SeServiceLogonRight = *$sid" } }
    }
    Set-Content $cfg $lines -Encoding Unicode
    Invoke-Native secedit.exe @("/configure", "/db", $sdb, "/cfg", $cfg, "/areas", "USER_RIGHTS", "/quiet")
    Remove-Item $cfg, $sdb -Force -ErrorAction SilentlyContinue
}

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

function Wait-Website {
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $r = Invoke-WebRequest "http://127.0.0.1:8080/login" -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw "The website did not answer on http://127.0.0.1:8080/login. See $Logs\web.log"
}

# --- Checks ---------------------------------------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "Run this as Administrator (right-click install.cmd, Run as administrator)." }
foreach ($part in @("python\python.exe", "HASC-OCR\hasc", "bin\nssm.exe")) {
    if (-not (Test-Path (Join-Path $Bundle $part))) { throw "$part is missing next to install.ps1. Unzip the whole bundle first." }
}
if ((Resolve-Path $Bundle).Path.TrimEnd("\") -eq $InstallDir.TrimEnd("\")) {
    throw "Unzip the bundle somewhere else (for example Downloads), not into $InstallDir."
}
$edition = (Get-CimInstance Win32_OperatingSystem).Caption
$hasEfs = $edition -notmatch "Home"
$update = (Test-Path $Db) -and (Get-Service $Services[0] -ErrorAction SilentlyContinue)
$version = (Get-Content (Join-Path $Bundle "VERSION.txt") -ErrorAction SilentlyContinue)
if ($update) { $mode = "Updating" } else { $mode = "Installing" }
Write-Host "$mode HASC $version in $InstallDir on $edition"
if (-not $hasEfs) { Write-Host "Windows Home has no EFS: the data folder is protected by permissions only." -ForegroundColor Yellow }

# --- Code and Python (install and update) ---------------------------------------------------------
Step "Stopping the services"
foreach ($s in $Services) {
    if (Get-Service $s -ErrorAction SilentlyContinue) { Stop-Service $s -Force }
}

Step "Copying Python, the code and NSSM into $InstallDir"
if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir | Out-Null }
foreach ($part in @("python", "HASC-OCR", "bin")) {
    & robocopy.exe (Join-Path $Bundle $part) (Join-Path $InstallDir $part) /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying $part failed (robocopy exit code $LASTEXITCODE)" }
}
Copy-Item (Join-Path $Bundle "VERSION.txt") $InstallDir -Force -ErrorAction SilentlyContinue
Get-ChildItem $InstallDir -Recurse -File | Unblock-File

# --- The hasc account (first install) -------------------------------------------------------------
if (-not (Get-LocalUser $Account -ErrorAction SilentlyContinue)) {
    Step "Creating the Windows account '$Account'"
    if (-not $HascPassword) {
        Write-Host "Choose a strong password and keep it in your password manager."
        Write-Host "Never reset it from another account later: that destroys the encryption keys and the data."
        $HascPassword = Read-Host "New password for '$Account'" -AsSecureString
        $again = Read-Host "Type it again" -AsSecureString
        if ((Get-Plain $HascPassword) -cne (Get-Plain $again)) { throw "The passwords do not match." }
    }
    try {
        New-LocalUser -Name $Account -Password $HascPassword -PasswordNeverExpires -UserMayNotChangePassword `
            -Description "Runs the HASC website and worker" | Out-Null
    } catch [Microsoft.PowerShell.Commands.InvalidPasswordException] {
        throw "Windows rejected the password for '$Account': make it longer, mix letters, digits and symbols, and do not include '$Account'."
    }
}
# A password that expired would stop both services at the next restart.
Set-LocalUser -Name $Account -PasswordNeverExpires $true
Grant-ServiceLogonRight $Account

Step "Setting folder permissions"
Set-Folder $Data "F"
Set-Folder $Config "F"
Set-Folder $Logs "F"
foreach ($part in @("python", "HASC-OCR", "bin")) { Set-Folder (Join-Path $InstallDir $part) "RX" }

# --- Settings and data (first install) ------------------------------------------------------------
$workerEnv = Join-Path $Config "worker.env"
$namesTarget = Join-Path $Config "known_names.json"
$newFiles = $false
if (-not (Test-Path $workerEnv)) {
    Step "Worker settings"
    if (-not $KnownNames) { $KnownNames = Ask "Path to known_names.json (for example on the USB stick)" (Join-Path $Bundle "known_names.json") }
    if (-not (Test-Path $KnownNames)) { throw "known_names.json not found at $KnownNames" }
    Copy-Item $KnownNames $namesTarget -Force
    if (-not $OpenRouterKey) { $OpenRouterKey = Read-Host "OpenRouter API key" -AsSecureString }
    Set-Content $workerEnv -Encoding ASCII -Value @(
        "OPENROUTER_API_KEY=$(Get-Plain $OpenRouterKey)",
        "HASC_READER_MODEL=google/gemini-3.8-flash",
        "HASC_READER_FALLBACK=qwen/qwen3.8-27b",
        "HASC_KNOWN_NAMES=$namesTarget")
    $newFiles = $true
}

if (-not (Test-Path $Db)) {
    if (-not $ImportDb -and -not $NonInteractive) {
        $ImportDb = Ask "Bring existing data? Path to a hasc.db backup, or Enter for a fresh start" ""
    }
    if ($ImportDb) {
        Step "Importing $ImportDb"
        Copy-Item $ImportDb $Db
        if (-not $ImportPhotos) { $ImportPhotos = Ask "Folder with the old photos" (Join-Path (Split-Path $ImportDb -Parent) "photos") }
        if (-not $OldPhotosPrefix) { $OldPhotosPrefix = Ask "Photo folder path on the old machine, ending in /" "/home/OLD/.local/share/hasc-ocr-dev/photos/" }
        & robocopy.exe $ImportPhotos (Join-Path $Data "photos") /E /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Copying the photos failed (robocopy exit code $LASTEXITCODE)" }
        $env:HASC_OLD = $OldPhotosPrefix
        $env:HASC_NEW = (Join-Path $Data "photos") + "\"
        $env:HASC_DB = $Db
        Invoke-Native $Py @("-c", "import os, sqlite3; c = sqlite3.connect(os.environ['HASC_DB']); old, new = os.environ['HASC_OLD'], os.environ['HASC_NEW']; fix = lambda p: p and p.replace(old, new).replace('/', '\\'); [c.execute('UPDATE photos SET path = ?, small_copy_path = ? WHERE id = ?', (fix(p), fix(s), i)) for i, p, s in c.execute('SELECT id, path, small_copy_path FROM photos').fetchall()]; c.commit(); print(c.execute('SELECT path FROM photos LIMIT 3').fetchall()); c.close()")
    } else {
        Step "Creating the database and the first admin account"
        $cliArgs = @("$Code\scripts\create_user.py", "--db", $Db, "--username", "admin", "--role", "admin")
        if ($AdminPassword) { $cliArgs += @("--password", $AdminPassword) }
        Invoke-Native $Py $cliArgs
        Write-Host "Write down the admin password above: it is shown only once." -ForegroundColor Yellow
    }
    $newFiles = $true
}
New-Item -ItemType Directory -Path (Join-Path $Data "photos") -Force | Out-Null

# --- Encryption (first install, Pro only) ---------------------------------------------------------
if ($hasEfs -and $newFiles) {
    Step "Encrypting the data and settings to '$Account' (EFS)"
    Invoke-AsHasc "cipher /e /s:`"$Data`" >nul && cipher /e /s:`"$Config`" >nul"
    if (-not $SkipKeyExport) {
        Step "Backing up the encryption key"
        Write-Host "A window opens as '$Account'. Type a password for the key file there (twice)."
        $pfx = Join-Path $Logs "hasc-efs-key"
        Invoke-AsHasc "cipher /x `"$pfx`"" -Visible
        Write-Host "Copy $pfx.pfx to a USB stick and keep it away from this PC." -ForegroundColor Yellow
        Write-Host "Without it, a broken '$Account' profile means the data is lost."
        if (-not $NonInteractive) {
            Read-Host "Press Enter once it is copied; the file is then deleted from this PC" | Out-Null
            Remove-Item "$pfx.pfx" -Force
        }
    }
}

# --- Services --------------------------------------------------------------------------------------
Step "Installing the services"
$plain = Get-Plain (Get-HascCredential).Password
foreach ($s in $Services) {
    if (Get-Service $s -ErrorAction SilentlyContinue) { Invoke-Native $Nssm @("remove", $s, "confirm") }
}
Invoke-Native $Nssm @("install", "hasc-web", $Py, "-m uvicorn hasc.web:create_app --factory --host 0.0.0.0 --port 8080")
Invoke-Native $Nssm @("set", "hasc-web", "AppEnvironmentExtra", "HASC_DB_PATH=$Db")
Invoke-Native $Nssm @("install", "hasc-worker", $Py, "-u -m hasc.worker")
Invoke-Native $Nssm @("set", "hasc-worker", "AppEnvironmentExtra", "HASC_DB_PATH=$Db", "HASC_WORKER_ENV=$workerEnv")
foreach ($s in $Services) {
    $log = Join-Path $Logs ($s.Replace("hasc-", "") + ".log")
    Invoke-Native $Nssm @("set", $s, "AppDirectory", $Code)
    Invoke-Native $Nssm @("set", $s, "ObjectName", ".\$Account", $plain)
    Invoke-Native $Nssm @("set", $s, "Start", "SERVICE_AUTO_START")
    Invoke-Native $Nssm @("set", $s, "AppStdout", $log)
    Invoke-Native $Nssm @("set", $s, "AppStderr", $log)
    Invoke-Native $Nssm @("set", $s, "AppRotateFiles", "1")
    Invoke-Native $Nssm @("set", $s, "AppRotateBytes", "10485760")
}
$plain = $null

# --- Network and power (first install; safe to repeat) --------------------------------------------
Step "Firewall and power"
if (-not $Subnet) {
    $ip = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -ne "WellKnown" -and $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1
    $guess = "192.168.1.0/24"
    if ($ip -and $ip.PrefixLength -eq 24) { $guess = ($ip.IPAddress -replace "\.\d+$", ".0") + "/24" }
    $Subnet = Ask "Office network that may reach the website" $guess
}
Get-NetFirewallRule -DisplayName "HASC website" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "HASC website" -Direction Inbound -Protocol TCP -LocalPort 8080 `
    -RemoteAddress $Subnet -Profile Private, Domain -Action Allow | Out-Null
foreach ($net in Get-NetConnectionProfile) {
    if ($net.NetworkCategory -eq "Public") {
        $answer = Ask "Network '$($net.Name)' is Public, so phones cannot reach the website. Set it to Private? (y/n)" "y"
        if ($answer -like "y*") { Set-NetConnectionProfile -InterfaceIndex $net.InterfaceIndex -NetworkCategory Private }
    }
}
Invoke-Native powercfg.exe @("/change", "standby-timeout-ac", "0")
Invoke-Native powercfg.exe @("/change", "hibernate-timeout-ac", "0")

# --- Start and check -------------------------------------------------------------------------------
Step "Starting the services"
foreach ($s in $Services) { Start-Service $s }
Wait-Website
Start-Sleep -Seconds 5
foreach ($s in $Services) {
    $status = (Get-Service $s).Status
    if ($status -ne "Running") { throw "$s is $status. See the log in $Logs" }
}

$addresses = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" }).IPAddress
Write-Host ""
Write-Host "HASC $version is running." -ForegroundColor Green
foreach ($a in $addresses) { Write-Host "  Phones on the office network: http://${a}:8080/login" }
Write-Host "  Logs: $Logs"
if (-not $update) {
    Write-Host ""
    Write-Host "Still to do by hand:" -ForegroundColor Yellow
    Write-Host "  - BIOS: set 'Restore on AC power loss' to Power On."
    Write-Host "  - Router: give this PC a fixed address (DHCP reservation)."
    Write-Host "  - Windows Update: pause it; install updates during planned shutdowns."
}
