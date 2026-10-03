param([switch]$NoAutoStart, [switch]$NoTray, [string]$StateDir)
$ErrorActionPreference = 'Stop'
if (-not $StateDir) { $StateDir = if ($env:CODEX_SUIXING_STATE_DIR) { $env:CODEX_SUIXING_STATE_DIR } else { Join-Path $PSScriptRoot '.state' } }
if ($NoTray) { $env:CODEX_SUIXING_NO_TRAY = '1' }
$viewerPython = if ($env:CODEX_SUIXING_PYTHON) { $env:CODEX_SUIXING_PYTHON } else { (& py -3 -c 'import sys;print(sys.executable)').Trim() }
& $viewerPython (Join-Path $PSScriptRoot 'companion.py') start --state-dir $StateDir
if ($LASTEXITCODE -ne 0) { throw 'Sync startup failed; check private launcher.log' }
if (-not $NoAutoStart) { & $viewerPython (Join-Path $PSScriptRoot 'companion.py') autostart --state-dir $StateDir }
