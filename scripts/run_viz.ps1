# Run in YOUR OWN PowerShell window.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\run_viz.ps1
# Produces a SHORT continuous run (default 3 days) with overnight virtual settlement,
# then builds the HTML animation INCLUDING the night mother-trucks (kind=mother).
# Open the printed real_map_visualization.html and drag the time slider to a midnight
# (minute 1440 / 2880) to watch the big "mother" trucks move between districts.
#
# After the final Pareto picks a working point, edit $TL/$TH/$TRUCKS below to that point
# and re-run. Mother-truck VISIBILITY is parameter-independent, so this is runnable now.

$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = "C:\Users\lojas\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.10_qbz5n2kfra8p0\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# ---- working point (FINAL Pareto pick: band 0.40/0.60, 104 trucks demand-allocated) ----
$TL = 0.40          # target_low_ratio
$TH = 0.60          # target_high_ratio
$TRUCKS = 104       # total fleet, allocated proportional to demand (Pareto knee, PoE=0.601)
$DAYS = 3           # 3 days = 2 midnights of mother-truck settlement
$SEED = 20260603
$SUB = "exp16_viz"

Write-Host "1/2  Running $DAYS-day continuous virtual sim (P7, demand $TRUCKS, band $TL/$TH)..." -ForegroundColor Cyan
& $py -u scenarios/real_system_scenario.py --hours 20 --profile weekday `
  --snapshot-csv data/snapshots/initial_bikes_4am_weekday.csv --start-minute 240 --duty-windows 240-1440 `
  --seed $SEED --dispatch-policy pair_coord --truck-allocation demand --total-trucks $TRUCKS `
  --days $DAYS --overnight-mode virtual `
  --dispatch-config-override "target_low_ratio=$TL,target_high_ratio=$TH" `
  --report-subdir $SUB --label mother_viz

$rd = Get-ChildItem -Path "report/$SUB" -Directory | Sort-Object LastWriteTime | Select-Object -Last 1
Write-Host "2/2  Building HTML animation for $($rd.FullName)..." -ForegroundColor Cyan
& $py -u scripts/visualize_real_system.py --report-dir $rd.FullName

Write-Host "`n=== DONE. Open this in a browser: ===" -ForegroundColor Green
Write-Host (Join-Path $rd.FullName "real_map_visualization.html") -ForegroundColor Green
Write-Host "Tip: drag the time slider to minute 1440 or 2880 (midnight) and press play to see the mother-trucks." -ForegroundColor Green
