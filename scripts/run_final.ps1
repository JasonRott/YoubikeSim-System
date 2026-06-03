# Run in YOUR OWN PowerShell window (not via Claude Code). No 2-min kill there.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\run_final.ps1
# Sequence: 30-day virtual -> 30-day full -> P7 tuning.  ~30-60 min total.

$ErrorActionPreference = "Continue"
# Derive project root from this script's own location (handles non-ASCII path safely).
Set-Location (Split-Path $PSScriptRoot -Parent)

# Correct python (has simpy etc.); NOT the WindowsApps 'python' alias.
$py = "C:\Users\lojas\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.10_qbz5n2kfra8p0\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$common = @("--hours","20","--profile","weekday","--snapshot-csv","data/snapshots/initial_bikes_4am_weekday.csv",
            "--start-minute","240","--duty-windows","240-1440","--seed","20260603",
            "--dispatch-policy","pair_coord","--trucks-per-district","8","--days","30")

function Log($m) {
    $line = "$((Get-Date).ToString('HH:mm:ss'))  $m"
    Write-Host $line -ForegroundColor Cyan
    $line | Out-File report\_final_progress.txt -Append -Encoding utf8
}

Remove-Item report\exp12_cont30_virtual,report\exp12_cont30_full -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item report\exp13_tune_s1,report\exp13_tune_s2,report\exp13_tune_sweep -Recurse -Force -ErrorAction SilentlyContinue
"" | Out-File report\_final_progress.txt -Encoding utf8

Log "START all final tasks"
Log "[1/3] 30-day virtual ..."
& $py -u scenarios/real_system_scenario.py @common --overnight-mode virtual --report-subdir exp12_cont30_virtual --label c30virt
Log "[1/3] virtual DONE"

Log "[2/3] 30-day full ..."
& $py -u scenarios/real_system_scenario.py @common --overnight-mode full --report-subdir exp12_cont30_full --label c30full
Log "[2/3] full DONE"

Log "[3/3] P7 tuning (about 1-2 hours) ..."
& $py -u scripts/tune_p7.py
Log "[3/3] tuning DONE"
Log "ALL DONE"
Write-Host "`n=== ALL DONE. Go back to Claude Code and ask it to analyze the results. ===" -ForegroundColor Green
