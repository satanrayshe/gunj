# Stage 3 harvest, in the order fixed by PROTOCOL.md section 14 step 3.
# Run it only after the "Pre-registration" commit exists.
# It is safe to run again after a shutdown or crash: the harvester skips streams that are already done.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_stage3.ps1
#
# Steps: SYNTHETIC negatives (made locally, nothing is downloaded), threshold-half real negatives,
# Test in the seeded order of PROTOCOL section 19 (10:00 deviation) with the M5 cuts, then report-half real negatives.
# It stops at the first bot check / HTTP 429 (harvester exit 3) or if another harvester holds the lock (exit 4).

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:OPENBLAS_NUM_THREADS = '1'
$py = '.venv\Scripts\python.exe'

$pre = git log --format=%s | Select-String -SimpleMatch 'Pre-registration'
if (-not $pre) { Write-Error 'No Pre-registration commit yet. Shrey must approve PROTOCOL.md first.'; exit 2 }

$thr = (Import-Csv data\splits.csv |
        Where-Object { $_.split -eq 'negative' -and $_.half -eq 'threshold' } |
        ForEach-Object { $_.video_id }) -join ','

$steps = @(
    @{ name = 'synthetic';     args = @('--splits', 'negative_synthetic', '--extractors', 'v2,v1,naive') },
    @{ name = 'neg_threshold'; args = @('--splits', 'negative', "--only=$thr", '--extractors', 'v2,v1,naive') },
    @{ name = 'test';          args = @('--splits', 'test', '--extractors', 'v2,v1,naive', '--cuts', '--order-seed', '2026110701') },
    @{ name = 'neg_report';    args = @('--splits', 'negative', '--extractors', 'v2,v1,naive') }
)

foreach ($s in $steps) {
    "$(Get-Date -Format s) start $($s.name)" | Add-Content logs\run_stage3.log
    & $py scripts\harvest.py @($s.args) --log "logs\harvest_$($s.name).log"
    $code = $LASTEXITCODE
    "$(Get-Date -Format s) end $($s.name) exit $code" | Add-Content logs\run_stage3.log
    if ($code -eq 3 -or $code -eq 4) { exit $code }
}
"$(Get-Date -Format s) all steps finished" | Add-Content logs\run_stage3.log
