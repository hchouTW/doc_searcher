# Purpose: Live check of examples/center_on_screen/center_on_screen.ahk on a real Windows desktop.
# What it does: Runs the script under AutoHotkey v2, opens real WinForms windows, and verifies that
#   a standard window is centered in the work area once (and not re-centered after a move), while
#   borderless, tool, and always-on-top windows are left alone. Exits 1 on any failure.
# Usage: pwsh -File center_on_screen_live.ps1 -AutoHotkey <AutoHotkey64.exe> -Script <center_on_screen.ahk>
#   Needs an interactive desktop session (GitHub-hosted windows-latest provides one).
param(
    [Parameter(Mandatory)] [string] $AutoHotkey,
    [Parameter(Mandatory)] [string] $Script
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
Write-Host "Work area: $wa"

function Wait-Pump([int] $Milliseconds) {
    $end = [DateTime]::UtcNow.AddMilliseconds($Milliseconds)
    while ([DateTime]::UtcNow -lt $end) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 50
    }
}

function New-TestForm([string] $Border, [bool] $TopMost) {
    $form = New-Object System.Windows.Forms.Form
    $form.StartPosition = 'Manual'
    $form.Location = New-Object System.Drawing.Point(0, 0)
    $form.Size = New-Object System.Drawing.Size(400, 300)
    $form.FormBorderStyle = $Border
    $form.TopMost = $TopMost
    $form
}

$failures = @()
function Assert-Case([string] $Name, [bool] $Ok, [string] $Detail) {
    $state = if ($Ok) { 'PASS' } else { 'FAIL' }
    Write-Host "$state  $Name  $Detail"
    if (-not $Ok) { $script:failures += $Name }
}

$ahk = Start-Process -FilePath $AutoHotkey -ArgumentList "`"$Script`"" -PassThru
try {
    Start-Sleep -Seconds 2
    if ($ahk.HasExited) { throw "AutoHotkey exited early with code $($ahk.ExitCode)" }

    # Standard window: centered, then left alone after the user moves it.
    $form = New-TestForm 'Sizable' $false
    $form.Show()
    Wait-Pump 2500
    $expectX = $wa.X + [Math]::Floor(($wa.Width - $form.Width) / 2)
    $expectY = $wa.Y + [Math]::Floor(($wa.Height - $form.Height) / 2)
    Assert-Case 'standard window centered' `
        (([Math]::Abs($form.Left - $expectX) -le 2) -and ([Math]::Abs($form.Top - $expectY) -le 2)) `
        "at ($($form.Left),$($form.Top)) expected ($expectX,$expectY)"
    Assert-Case 'size preserved' (($form.Width -eq 400) -and ($form.Height -eq 300)) "$($form.Width)x$($form.Height)"
    $form.Location = New-Object System.Drawing.Point(10, 10)
    Wait-Pump 1500
    Assert-Case 'not re-centered after user move' (($form.Left -eq 10) -and ($form.Top -eq 10)) "at ($($form.Left),$($form.Top))"
    $form.Close()

    # Windows the script must ignore stay where they were opened.
    foreach ($case in @(
            @{ Name = 'borderless window ignored'; Border = 'None'; Top = $false },
            @{ Name = 'tool window ignored'; Border = 'FixedToolWindow'; Top = $false },
            @{ Name = 'always-on-top window ignored'; Border = 'Sizable'; Top = $true })) {
        $form = New-TestForm $case.Border $case.Top
        $form.Show()
        Wait-Pump 2000
        Assert-Case $case.Name (($form.Left -eq 0) -and ($form.Top -eq 0)) "at ($($form.Left),$($form.Top))"
        $form.Close()
    }

    Assert-Case 'script still running' (-not $ahk.HasExited) ''
} finally {
    if (-not $ahk.HasExited) { $ahk.Kill() }
}

if ($failures.Count) { Write-Host "Failed: $($failures -join ', ')"; exit 1 }
Write-Host 'All live checks passed.'
