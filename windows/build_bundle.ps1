# Builds hasc-<version>-win64.zip: the code, a portable Python 3.12 with every library installed,
# nssm.exe and the installer. Runs on Windows (CI). Usage: .\windows\build_bundle.ps1 -Version v1.0.2
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$OutDir = (Join-Path (Split-Path $PSScriptRoot -Parent) "dist")
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$PythonVersion = "3.12.10"
$NssmUrl = "https://nssm.cc/ci/nssm-2.24-101-g897c7ad.zip"

function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

$repo = Split-Path $PSScriptRoot -Parent
$name = "hasc-$Version-win64"
$stage = Join-Path $OutDir $name
$work = Join-Path $OutDir "work"
foreach ($dir in @($stage, $work)) {
    if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
    New-Item -ItemType Directory -Path $dir | Out-Null
}

# Portable Python: the NuGet package is a complete Python that runs from any folder, no installer.
Invoke-WebRequest "https://www.nuget.org/api/v2/package/python/$PythonVersion" -OutFile "$work\python.zip"
Expand-Archive "$work\python.zip" "$work\python"
Move-Item "$work\python\tools" "$stage\python"
$py = "$stage\python\python.exe"
Invoke-Native $py @("-m", "ensurepip")
Invoke-Native $py @("-m", "pip", "install", "--no-warn-script-location", "--disable-pip-version-check", "-r", "$repo\requirements.txt")

# The code, exactly as committed.
Invoke-Native git @("-C", $repo, "archive", "--format=zip", "-o", "$work\code.zip", "HEAD")
Expand-Archive "$work\code.zip" "$stage\HASC-OCR"

Invoke-WebRequest $NssmUrl -OutFile "$work\nssm.zip"
Expand-Archive "$work\nssm.zip" "$work\nssm"
New-Item -ItemType Directory -Path "$stage\bin" | Out-Null
Copy-Item (Get-ChildItem "$work\nssm" -Recurse -Filter nssm.exe | Where-Object { $_.FullName -like "*\win64\*" } | Select-Object -First 1).FullName "$stage\bin\nssm.exe"

Copy-Item "$PSScriptRoot\install.ps1", "$PSScriptRoot\install.cmd" $stage
Set-Content "$stage\VERSION.txt" $Version -Encoding ASCII

$zip = Join-Path $OutDir "$name.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$stage\*" -DestinationPath $zip
Remove-Item $work -Recurse -Force
Write-Host "Built $zip"
