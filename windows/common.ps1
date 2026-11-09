# Shared by install.ps1, update.ps1 and delete.ps1: elevation, small dialog windows, and helpers.
# Dot-source it; the caller sets $NonInteractive (CI) before calling the dialogs.

$Account = "hasc"
$Services = @("hasc-web", "hasc-worker", "hasc-redirect")
$WebsiteUrl = "https://localhost/login"
# The authority the installer makes for the website's HTTPS (hasc/certs.py), trusted on this PC.
$CaSubject = "CN=HASC local CA"
# Well-known SIDs, so the scripts work whatever language Windows is in.
$AdminsSid = "*S-1-5-32-544"
$SystemSid = "*S-1-5-18"
# Windows PowerShell 5.1 downloads very slowly while it draws a progress bar.
$ProgressPreference = "SilentlyContinue"
# Windows PowerShell 5.1 may still offer only old TLS versions, which the HTTPS website refuses.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Test-Admin {
    return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Restart-Elevated([string]$ScriptPath, [string[]]$Arguments) {
    # Opens the Windows "allow this app" prompt and runs the script again as Administrator.
    $list = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$ScriptPath`"") + $Arguments
    Start-Process powershell.exe -Verb RunAs -ArgumentList $list
}

function Get-ForwardArguments($Bound) {
    # The script's own parameters ($PSBoundParameters), for Restart-Elevated. Secure strings are left
    # out: they are encrypted to this window's user, so the elevated run asks for them again.
    $list = @()
    foreach ($k in $Bound.Keys) {
        $v = $Bound[$k]
        # Plain-text secrets stay off the command line, which other processes can read.
        if ($k -in @("Token", "AdminPassword")) { continue }
        if ($v -is [System.Management.Automation.SwitchParameter]) { if ($v.IsPresent) { $list += "-$k" } }
        elseif ($v -isnot [System.Security.SecureString]) {
            # A trailing backslash would escape the closing quote.
            $s = "$v"
            if ($s.EndsWith("\")) { $s += "\" }
            $list += @("-$k", "`"$s`"")
        }
    }
    return $list
}

function Protect-TokenFile {
    # The saved GitHub tokens (update.ps1): administrators only. Runs after every install and update,
    # since the installer resets the tools folder. The old shared token file is no longer used.
    $tools = Join-Path $InstallDir "tools"
    Remove-Item (Join-Path $tools "github-token.dat") -Force -ErrorAction SilentlyContinue
    foreach ($f in Get-ChildItem $tools -Filter "github-token-*.dat" -ErrorAction SilentlyContinue) {
        Invoke-Native icacls.exe @($f.FullName, "/inheritance:r", "/grant:r", "${AdminsSid}:F", "${SystemSid}:F", "/Q") | Out-Null
    }
}

function Step([string]$Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }

function Invoke-Native {
    # -Secret: the arguments hold a password, so the error message leaves them out.
    param([string]$Exe, [string[]]$Arguments, [switch]$Secret)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        $shown = $Arguments -join ' '
        if ($Secret) { $shown = $Arguments[0..1] -join ' ' }
        throw "$Exe $shown failed with exit code $LASTEXITCODE"
    }
}

function Get-Plain([System.Security.SecureString]$Secure) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

# --- Dialogs -----------------------------------------------------------------------------------------
if (-not $NonInteractive) {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    [System.Windows.Forms.Application]::EnableVisualStyles()
}

function Show-Info([string]$Text) {
    Write-Host $Text
    if (-not $NonInteractive) {
        [System.Windows.Forms.MessageBox]::Show($Text, "HASC", "OK", "Information") | Out-Null
    }
}

function Show-Error([string]$Text) {
    Write-Host $Text -ForegroundColor Red
    if (-not $NonInteractive) {
        [System.Windows.Forms.MessageBox]::Show($Text, "HASC: something went wrong", "OK", "Error") | Out-Null
    }
}

function Confirm-Choice([string]$Text, [bool]$Default = $true, [switch]$Warning) {
    if ($NonInteractive) { return $Default }
    $icon = [System.Windows.Forms.MessageBoxIcon]::Question
    if ($Warning) { $icon = [System.Windows.Forms.MessageBoxIcon]::Warning }
    # Enter picks the default button, so it must be No when the safe answer is No.
    $button = [System.Windows.Forms.MessageBoxDefaultButton]::Button1
    if (-not $Default) { $button = [System.Windows.Forms.MessageBoxDefaultButton]::Button2 }
    return [System.Windows.Forms.MessageBox]::Show($Text, "HASC", [System.Windows.Forms.MessageBoxButtons]::YesNo, $icon, $button) -eq "Yes"
}

function New-Dialog([string]$Text, [int]$Height) {
    $form = New-Object System.Windows.Forms.Form
    $form.Text = "HASC"
    $form.Size = New-Object System.Drawing.Size(460, $Height)
    $form.StartPosition = "CenterScreen"
    $form.FormBorderStyle = "FixedDialog"
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.TopMost = $true
    $form.Font = New-Object System.Drawing.Font("Segoe UI", 10)
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $Text
    $label.Location = New-Object System.Drawing.Point(16, 14)
    $label.Size = New-Object System.Drawing.Size(410, ($Height - 130))
    $form.Controls.Add($label)
    return $form
}

function Add-Buttons($Form, [switch]$NoCancel) {
    $y = $Form.ClientSize.Height - 44
    $ok = New-Object System.Windows.Forms.Button
    $ok.Text = "OK"
    $ok.DialogResult = "OK"
    $ok.Location = New-Object System.Drawing.Point(244, $y)
    $ok.Size = New-Object System.Drawing.Size(90, 30)
    $Form.Controls.Add($ok)
    $Form.AcceptButton = $ok
    if (-not $NoCancel) {
        $cancel = New-Object System.Windows.Forms.Button
        $cancel.Text = "Cancel"
        $cancel.DialogResult = "Cancel"
        $cancel.Location = New-Object System.Drawing.Point(340, $y)
        $cancel.Size = New-Object System.Drawing.Size(90, 30)
        $Form.Controls.Add($cancel)
        $Form.CancelButton = $cancel
    }
}

function Read-Text([string]$Text, [string]$Default = "", [switch]$Secret) {
    # Returns a string, or a SecureString with -Secret. Cancel stops the script.
    if ($NonInteractive) { return $Default }
    $form = New-Dialog $Text 230
    $box = New-Object System.Windows.Forms.TextBox
    $box.Location = New-Object System.Drawing.Point(16, ($form.ClientSize.Height - 84))
    $box.Size = New-Object System.Drawing.Size(414, 28)
    $box.Text = $Default
    if ($Secret) { $box.UseSystemPasswordChar = $true }
    $form.Controls.Add($box)
    Add-Buttons $form
    $form.Add_Shown({ $box.Focus() })
    if ($form.ShowDialog() -ne "OK") { throw "Cancelled." }
    $value = $box.Text
    $form.Dispose()
    # A secret is kept exactly as typed; a path dragged into the box arrives in quotes.
    if ($Secret) {
        if ($value.Length -eq 0) { return (New-Object System.Security.SecureString) }
        return (ConvertTo-SecureString $value -AsPlainText -Force)
    }
    return $value.Trim().Trim('"')
}

function Show-Secret([string]$Text, [string]$Value) {
    # Shows a value once, in a box it can be copied from. Never written to the console.
    if ($NonInteractive) { return }
    $form = New-Dialog $Text 230
    $box = New-Object System.Windows.Forms.TextBox
    $box.Location = New-Object System.Drawing.Point(16, ($form.ClientSize.Height - 84))
    $box.Size = New-Object System.Drawing.Size(414, 28)
    $box.Text = $Value
    $box.ReadOnly = $true
    $box.Font = New-Object System.Drawing.Font("Consolas", 12)
    $form.Controls.Add($box)
    Add-Buttons $form -NoCancel
    $form.ShowDialog() | Out-Null
    $form.Dispose()
}

function Select-File([string]$Title, [string]$Filter, [string]$Default = "") {
    if ($NonInteractive) { return $Default }
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = $Title
    $dialog.Filter = $Filter
    if ($Default -and (Test-Path -LiteralPath $Default)) { $dialog.InitialDirectory = Split-Path $Default -Parent }
    if ($dialog.ShowDialog() -ne "OK") { throw "Cancelled." }
    return $dialog.FileName
}

function Select-Folder([string]$Title) {
    if ($NonInteractive) { return "" }
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = $Title
    if ($dialog.ShowDialog() -ne "OK") { throw "Cancelled." }
    return $dialog.SelectedPath
}

# --- The hasc account --------------------------------------------------------------------------------
function Get-HascCredential {
    if (-not $script:HascPassword) {
        $script:HascPassword = Read-Text "Password of the Windows account '$Account' (the one chosen when HASC was installed)." -Secret
    }
    return New-Object System.Management.Automation.PSCredential (".\$Account", $script:HascPassword)
}

function Invoke-AsHasc([string]$Command, [string]$WorkingDirectory, [switch]$Visible) {
    # Runs a command as hasc with its profile loaded, so EFS uses hasc's key.
    $style = "Hidden"
    if ($Visible) { $style = "Normal" }
    if (-not $WorkingDirectory -or -not (Test-Path $WorkingDirectory)) { $WorkingDirectory = $env:SystemRoot }
    $p = Start-Process cmd.exe -ArgumentList "/c $Command" -Credential (Get-HascCredential) -LoadUserProfile `
        -WorkingDirectory $WorkingDirectory -WindowStyle $style -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "As ${Account}: '$Command' failed with exit code $($p.ExitCode)" }
}

function Test-HascPassword {
    try { Invoke-AsHasc "exit 0" }
    catch { $script:HascPassword = $null; throw "That is not the password of '$Account'. Nothing was changed." }
}

function Set-ServiceLogonRight([string]$Sid, [switch]$Remove) {
    $cfg = Join-Path $env:TEMP "hasc-rights.inf"
    $sdb = Join-Path $env:TEMP "hasc-rights.sdb"
    try {
        Invoke-Native secedit.exe @("/export", "/cfg", $cfg, "/areas", "USER_RIGHTS", "/quiet")
        $lines = Get-Content $cfg
        $line = $lines | Where-Object { $_ -like "SeServiceLogonRight*" }
        $pattern = "\*" + [regex]::Escape($Sid) + "(,|\s*$)"
        $has = $line -and ($line -match $pattern)
        if ($Remove) {
            if (-not $has) { return }
            $lines = $lines | ForEach-Object {
                if ($_ -like "SeServiceLogonRight*") { ($_ -replace (",?\*" + [regex]::Escape($Sid) + "(?=,|\s*$)"), "") -replace "= ,", "= " } else { $_ }
            }
        } else {
            if ($has) { return }
            if ($line) {
                $lines = $lines | ForEach-Object { if ($_ -like "SeServiceLogonRight*") { "$_,*$Sid" } else { $_ } }
            } else {
                $lines = $lines | ForEach-Object { $_; if ($_ -eq "[Privilege Rights]") { "SeServiceLogonRight = *$Sid" } }
            }
        }
        Set-Content $cfg $lines -Encoding Unicode
        Invoke-Native secedit.exe @("/configure", "/db", $sdb, "/cfg", $cfg, "/areas", "USER_RIGHTS", "/quiet")
    } finally {
        Remove-Item $cfg, $sdb -Force -ErrorAction SilentlyContinue
    }
}

function Get-AccountSid([string]$User) {
    return (New-Object System.Security.Principal.NTAccount($User)).Translate([System.Security.Principal.SecurityIdentifier]).Value
}

function Exit-WithError($ErrorRecord, [string]$Hint = "") {
    # Cancel in a dialog is not an error: stop quietly.
    if ($ErrorRecord.Exception.Message -eq "Cancelled.") { Write-Host "Cancelled."; exit 2 }
    $text = $ErrorRecord.Exception.Message
    if ($Hint) { $text += "`n`n$Hint" }
    Show-Error $text
    exit 1
}
