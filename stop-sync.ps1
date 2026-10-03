param([switch]$KeepAutoStart, [switch]$KeepTray, [string]$StateDir)
$ErrorActionPreference = 'Stop'
if (-not $StateDir) { $StateDir = if ($env:CODEX_SUIXING_STATE_DIR) { $env:CODEX_SUIXING_STATE_DIR } else { Join-Path $PSScriptRoot '.state' } }
if (-not (Test-Path -LiteralPath $StateDir)) { return }
Set-Content -LiteralPath (Join-Path $StateDir 'sync-stop.flag') -Value 'stop' -Encoding ascii
Set-Content -LiteralPath (Join-Path $StateDir 'stop.request') -Value 'stop' -Encoding ascii
foreach ($viewerName in @('supervisor.pid','sync.pid')) {
    $viewerPidFile = Join-Path $StateDir $viewerName
    if (-not (Test-Path -LiteralPath $viewerPidFile)) { continue }
    $viewerNumber = [int](Get-Content -LiteralPath $viewerPidFile)
    $viewerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$viewerNumber"
    if ($viewerProcess -and $viewerProcess.CommandLine -and ($viewerProcess.CommandLine.Contains((Join-Path $PSScriptRoot 'companion.py')) -or $viewerProcess.CommandLine.Contains((Join-Path $PSScriptRoot 'sync_supervisor.py')) -or $viewerProcess.CommandLine.Contains((Join-Path $PSScriptRoot 'viewer.py')))) {
        $viewerChildren = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$viewerNumber" | Where-Object { $_.CommandLine -and ($_.CommandLine.Contains((Join-Path $PSScriptRoot 'desktop_bridge.mjs')) -or ($_.Name -eq 'ssh.exe' -and $_.CommandLine.Contains('exchange-stream'))) })
        Stop-Process -Id $viewerNumber
        foreach ($viewerChild in $viewerChildren) { Stop-Process -Id $viewerChild.ProcessId -ErrorAction SilentlyContinue }
    }
}
if (-not $KeepTray) { Set-Content -LiteralPath (Join-Path $StateDir 'tray-stop.flag') -Value 'stop' -Encoding ascii }
if (-not $KeepAutoStart) {
    $viewerPython = if ($env:CODEX_SUIXING_PYTHON) { $env:CODEX_SUIXING_PYTHON } else { (& py -3 -c 'import sys;print(sys.executable)').Trim() }
    & $viewerPython (Join-Path $PSScriptRoot 'companion.py') autostart --disable --state-dir $StateDir
}
