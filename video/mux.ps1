<#
.SYNOPSIS
  Puts a recorded narration under the silent Gunj v1 video and writes video\gunj-v1.mp4.

.DESCRIPTION
  Takes a narration file (wav, m4a or mp3), cuts low rumble below 80 Hz, normalises loudness with
  ffmpeg loudnorm in two passes (-16 LUFS integrated, -1.5 dBTP), places it under
  video\gunj-v1-silent.mp4 starting at 0 s, and pads it with silence or cuts it to the video's
  length. The video stream is copied unchanged. Needs ffmpeg and ffprobe on PATH.

.PARAMETER Narration
  Path to the narration recording.

.PARAMETER TrimStart
  Optional. Seconds to cut from the start of the recording, if it began before the video did.

.PARAMETER Force
  Use a narration file even if it is older than video\NARRATION.md. Without it the script refuses,
  because an older file may speak an earlier version of the lines under the current captions.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File video\mux.ps1 "narration.m4a"

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File video\mux.ps1 "narration.m4a" -TrimStart 0.8
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Narration,
    [double]$TrimStart = 0,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$inv = [Globalization.CultureInfo]::InvariantCulture
$video = Join-Path $PSScriptRoot 'gunj-v1-silent.mp4'
$out = Join-Path $PSScriptRoot 'gunj-v1.mp4'

foreach ($tool in 'ffmpeg', 'ffprobe') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool is not on PATH." }
}
if (-not (Test-Path -LiteralPath $Narration -PathType Leaf)) { throw "Narration file not found: $Narration" }
if (-not (Test-Path -LiteralPath $video -PathType Leaf)) { throw "Silent video not found: $video" }
$Narration = (Resolve-Path -LiteralPath $Narration).Path
$lines = Join-Path $PSScriptRoot 'NARRATION.md'
if (-not $Force -and (Get-Item -LiteralPath $Narration).LastWriteTime -lt (Get-Item -LiteralPath $lines).LastWriteTime) {
    throw ("The narration file is older than video\NARRATION.md, so it may speak an earlier version of the lines. " +
           "Make it again from the current lines, or add -Force if the lines have not changed since it was made.")
}

function Get-Duration([string]$path) {
    $s = & ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 $path
    if ($LASTEXITCODE -ne 0) { throw "ffprobe could not read $path" }
    return [double]::Parse(($s | Select-Object -First 1).Trim(), $inv)
}
function Format-Num([double]$x) { return $x.ToString('0.###', $inv) }

$vDur = Get-Duration $video
$aDur = Get-Duration $Narration
$vDurS = Format-Num $vDur

$pre = 'highpass=f=80'
if ($TrimStart -gt 0) { $pre = "atrim=start=$(Format-Num $TrimStart),asetpts=PTS-STARTPTS,$pre" }
$target = 'I=-16:TP=-1.5:LRA=11'

# Pass 1: measure loudness. ffmpeg prints the measurement on stderr; read it as text.
$old = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$log = (& ffmpeg -hide_banner -nostats -i $Narration -af "$pre,loudnorm=${target}:print_format=json" -f null - 2>&1 | ForEach-Object { "$_" }) -join "`n"
$code = $LASTEXITCODE
$ErrorActionPreference = $old
if ($code -ne 0) { throw "ffmpeg could not read the narration (exit $code)." }
$json = [regex]::Match($log, '\{[^{}]*"input_i"[^{}]*\}').Value
if (-not $json) { throw 'loudnorm did not report a measurement.' }
$m = $json | ConvertFrom-Json

if ($m.input_i -match 'inf') {
    Write-Warning 'The narration is silent; it is used without loudness normalisation.'
    $norm = 'anull'
} else {
    $norm = "loudnorm=${target}:measured_I=$($m.input_i):measured_TP=$($m.input_tp):measured_LRA=$($m.input_lra):measured_thresh=$($m.input_thresh):offset=$($m.target_offset):linear=true"
}

# Pass 2: normalise, start at 0 s, pad or cut to the video length, short fade at the very end.
$fadeAt = Format-Num ([Math]::Max(0, $vDur - 0.3))
$af = "$pre,$norm,aresample=48000,apad,atrim=end=$vDurS,afade=t=out:st=${fadeAt}:d=0.3"
& ffmpeg -hide_banner -loglevel error -y -i $video -i $Narration -map 0:v:0 -map 1:a:0 -c:v copy -af $af `
    -c:a aac -b:a 192k -ar 48000 -ac 2 -t $vDurS -movflags +faststart $out
if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed (exit $LASTEXITCODE)." }

$spoken = $aDur - $TrimStart
if ($spoken -gt $vDur + 0.05) {
    Write-Warning ("The narration is {0} s longer than the video, so its end was cut. Re-record the last line faster, or trim the start." -f (Format-Num ($spoken - $vDur)))
}
Write-Host ("Wrote {0} ({1} s video, narration {2} s, measured {3} LUFS before normalising)." -f $out, (Format-Num (Get-Duration $out)), (Format-Num $spoken), $m.input_i)
