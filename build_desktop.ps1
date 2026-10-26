# Build a distributable desktop folder under dist\PERTSwimlane (venv only).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Project venv not found at venv\. Create it first: python -m venv venv"
}

& $python -m pip install -r requirements-build.txt
& $python -m PyInstaller --noconfirm --clean pert_swimlane.spec

$out = Join-Path $PSScriptRoot "dist\PERTSwimlane"
$zip = Join-Path $PSScriptRoot "dist\PERTSwimlane.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $out -DestinationPath $zip

Write-Host ""
Write-Host "Built: $out\PERTSwimlane.exe"
Write-Host "Zip:   $zip"
Write-Host "Send the zip to users; they unzip and run PERTSwimlane.exe (WebView2 required on Windows)."
