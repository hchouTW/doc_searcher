# Purpose: Authenticode-sign a built DocSearcher.exe (release CI only).
# What the code does:
#   - Decodes the code-signing certificate to a temporary .pfx, signs the executable with
#     SHA-256 and an RFC 3161 timestamp using signtool from the Windows SDK, deletes the .pfx,
#     and fails unless Get-AuthenticodeSignature reports a Valid signature.
# Usage notes, dependencies, or assumptions:
#   - powershell -File packaging\sign_win.ps1 -Path dist\DocSearcher-Windows-x64.exe
#   - Environment: WINDOWS_CERTIFICATE_PFX_BASE64, WINDOWS_CERTIFICATE_PASSWORD, optional
#     WINDOWS_TIMESTAMP_URL (default http://timestamp.digicert.com). Nothing is printed from them.
#   - Any failure throws, which blocks the release.

param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = 'Stop'

foreach ($name in 'WINDOWS_CERTIFICATE_PFX_BASE64', 'WINDOWS_CERTIFICATE_PASSWORD') {
    if (-not [Environment]::GetEnvironmentVariable($name)) { throw "[sign_win] missing required secret: $name" }
}
$timestamp = if ($env:WINDOWS_TIMESTAMP_URL) { $env:WINDOWS_TIMESTAMP_URL } else { 'http://timestamp.digicert.com' }

$signtool = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe' |
    Sort-Object FullName | Select-Object -Last 1
if (-not $signtool) { throw '[sign_win] signtool.exe not found (Windows 10 SDK required)' }

$pfx = Join-Path ([IO.Path]::GetTempPath()) ("signing-" + [guid]::NewGuid() + '.pfx')
try {
    [IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:WINDOWS_CERTIFICATE_PFX_BASE64))
    & $signtool.FullName sign /fd SHA256 /tr $timestamp /td SHA256 /f $pfx /p $env:WINDOWS_CERTIFICATE_PASSWORD $Path
    if ($LASTEXITCODE -ne 0) { throw "[sign_win] signtool failed ($LASTEXITCODE)" }
}
finally {
    Remove-Item -Force -ErrorAction SilentlyContinue $pfx
}

$signature = Get-AuthenticodeSignature $Path
if ($signature.Status -ne 'Valid') { throw "[sign_win] signature status: $($signature.Status) - $($signature.StatusMessage)" }
Write-Host "[sign_win] signed: $Path ($($signature.SignerCertificate.Subject))"
