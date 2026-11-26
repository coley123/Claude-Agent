# IM1 Slack bridge setup for Windows. Safe to run more than once.
# Usage (PowerShell):
#   irm https://raw.githubusercontent.com/coley123/Claude-Agent/slack-bridge/slack_bridge/setup.ps1 | iex
# or, with a different IM1 folder:
#   $env:IM1_DIR = "D:\path\to\IM1"; irm https://raw.githubusercontent.com/coley123/Claude-Agent/slack-bridge/slack_bridge/setup.ps1 | iex

$ErrorActionPreference = "Stop"
$IM1 = if ($env:IM1_DIR) { $env:IM1_DIR } else { "$env:USERPROFILE\ObsidianVault\Projects\cross-model-collective-research\IM1" }
if (-not (Test-Path $IM1)) { Write-Host "IM1 folder not found: $IM1`nSet `$env:IM1_DIR to the right folder and run again." -ForegroundColor Red; return }
$Collab = Join-Path $IM1 "collab"
$Bridge = Join-Path $Collab "slack_bridge"
New-Item -ItemType Directory -Force -Path $Bridge | Out-Null
Write-Host "== IM1 folder: $IM1" -ForegroundColor Cyan

# 1. Download the bridge (only bridge files are replaced; your bridge.toml and .env are kept)
$zip = Join-Path $env:TEMP "im1-bridge.zip"
$tmp = Join-Path $env:TEMP "im1-bridge"
Invoke-WebRequest "https://github.com/coley123/Claude-Agent/archive/refs/heads/slack-bridge.zip" -OutFile $zip
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
Expand-Archive $zip -DestinationPath $tmp
$src = Get-ChildItem $tmp -Directory | Select-Object -First 1
Copy-Item "$($src.FullName)\slack_bridge\*" $Bridge -Recurse -Force -Exclude "bridge.toml", ".env"
Copy-Item "$($src.FullName)\docs\IM1-compatibility-assessment.md" $Collab -Force
Write-Host "== Bridge files in $Bridge" -ForegroundColor Cyan

# 2. Python environment
$py = (Get-Command py -ErrorAction SilentlyContinue).Source
$pyArgs = @("-3")
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source; $pyArgs = @() }
if (-not $py) { Write-Host "Python not found. Install Python 3.11+ and run again." -ForegroundColor Red; return }
$venv = Join-Path $Bridge ".venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) { & $py @pyArgs -m venv $venv }
& "$venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& "$venv\Scripts\python.exe" -m pip install --quiet -r (Join-Path $Bridge "requirements.txt")
Write-Host "== Python environment ready" -ForegroundColor Cyan

# 3. Settings: bridge.toml pointed at this IM1 folder (kept if it already exists)
$toml = Join-Path $Bridge "bridge.toml"
if (-not (Test-Path $toml)) {
  $fwd = $IM1 -replace '\\', '/'
  (Get-Content (Join-Path $Bridge "bridge.example.toml") -Raw) `
    -replace 'C:/Users/coley/IM1/collab', "$fwd/collab" `
    -replace 'C:/Users/coley/IM1', $fwd | Set-Content $toml -Encoding UTF8
  Write-Host "== Created bridge.toml" -ForegroundColor Cyan
} else { Write-Host "== Kept existing bridge.toml" -ForegroundColor Cyan }

# 4. Token file (you paste the six Slack tokens; never shared in chat)
$envFile = Join-Path $Bridge ".env"
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $Bridge ".env.example") $envFile }

# 5. Hermes: answer @mentions from the other bots (backup first, add the line once)
$hermesEnvs = @("$env:LOCALAPPDATA\hermes\profiles\gargoyle\.env", "$env:USERPROFILE\.hermes\profiles\gargoyle\.env") | Where-Object { Test-Path $_ }
if ($hermesEnvs) {
  $h = $hermesEnvs[0]
  if (-not (Select-String -Path $h -Pattern '^\s*SLACK_ALLOW_BOTS\s*=' -Quiet)) {
    Copy-Item $h "$h.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Add-Content $h "`nSLACK_ALLOW_BOTS=mentions"
    Write-Host "== Hermes gargoyle: added SLACK_ALLOW_BOTS=mentions (backup saved). Restart the Hermes gateway when no job is running." -ForegroundColor Yellow
  } else { Write-Host "== Hermes gargoyle: SLACK_ALLOW_BOTS already set; left unchanged" -ForegroundColor Cyan }
} else { Write-Host "== Hermes gargoyle profile .env not found; add SLACK_ALLOW_BOTS=mentions to its .env yourself" -ForegroundColor Yellow }

# 6. Check the three model apps
foreach ($c in @("claude", "codex", "grok")) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { Write-Host "   found: $c" -ForegroundColor Green }
  else { Write-Host "   MISSING: $c (install and sign in; see README)" -ForegroundColor Red }
}

# 7. Start script
$start = Join-Path $Collab "start-bridge.cmd"
Set-Content $start "@echo off`r`ncd /d `"$Bridge`"`r`n`".venv\Scripts\python.exe`" bridge.py bridge.toml`r`npause" -Encoding ASCII

Write-Host ""
Write-Host "NEXT:" -ForegroundColor Green
Write-Host " 1. Create the three Slack apps (Claude, GPT, Grok) from $Bridge\manifests (see README)."
Write-Host " 2. Paste the six tokens into: $envFile   (opening it now)"
Write-Host " 3. Check:  `"$venv\Scripts\python.exe`" `"$Bridge\bridge.py`" `"$toml`" --check"
Write-Host " 4. Start:  double-click $start"
notepad $envFile
