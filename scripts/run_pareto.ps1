# Run in YOUR OWN PowerShell window (not via Claude Code).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\run_pareto.ps1
# Final Pareto grid: target band x demand-allocation fleet x multi-seed.  ~2.4 hours.
# Resumable: if interrupted, just run again; it skips already-done points.

$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = "C:\Users\lojas\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.10_qbz5n2kfra8p0\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Write-Host "Starting final Pareto grid (60 runs, ~2.4h). Progress prints below and saves to report\_final_pareto.csv" -ForegroundColor Cyan
& $py -u scripts/final_pareto.py
Write-Host "`n=== Pareto grid DONE. Go back to Claude Code and ask it to plot from report\_final_pareto.csv ===" -ForegroundColor Green
