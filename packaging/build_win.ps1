# Purpose: Build the portable single-file DocSearcher.exe for Windows 10/11 from PowerShell.
# What the code does:
#   - Creates/reuses .\venv, installs the hashed lock (constraints\ci.txt, incl. PyInstaller + Pillow), generates the icon
#     if missing, and runs packaging\doc_searcher_win.spec (onefile, windowed).
# Usage notes, dependencies, or assumptions:
#   - powershell -ExecutionPolicy Bypass -File .\packaging\build_win.ps1
#   - Needs Python 3 on the build machine only; output dist\DocSearcher.exe runs without install.

$ErrorActionPreference = 'Stop'
# Run from the project root so venv\, dist\ and build\ live there.
$root = Split-Path -Parent $PSScriptRoot
Set-Location -Path $root

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe $Arguments" }
}

$py = Join-Path $root 'venv\Scripts\python.exe'

if (-not (Test-Path $py)) {
    Write-Host '[*] Creating virtual environment...'
    Invoke-Checked 'python' @('-m', 'venv', 'venv')
}

Write-Host '[*] Installing dependencies...'
Invoke-Checked $py @('-m', 'pip', 'install', '--quiet', '--upgrade', 'pip')
Invoke-Checked $py @('-m', 'pip', 'install', '--quiet', '--require-hashes', '-r', 'constraints\ci.txt')

if (-not (Test-Path 'src\doc_searcher\assets\app_icon.ico')) {
    Write-Host '[*] Generating icon...'
    Invoke-Checked $py @('scripts\generate_icon.py')
}

Write-Host '[*] Building DocSearcher.exe (1-2 minutes)...'
Invoke-Checked $py @('-m', 'PyInstaller', 'packaging\doc_searcher_win.spec', '--clean', '-y')

Write-Host ''
Write-Host '[OK] Build succeeded'
Write-Host "     $root\dist\DocSearcher.exe"
Write-Host '     Portable single file: copy it to any Windows 10/11 PC and run.'
