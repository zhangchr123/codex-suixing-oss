param([switch]$SelfTest, [string]$TestRoot, [string]$StateDir)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class SuixingTrayNative {
    [DllImport("user32.dll")] public static extern bool DestroyIcon(IntPtr handle);
}
'@
$script:ViewerRoot = $PSScriptRoot
$script:ViewerState = if ($StateDir) { [IO.Path]::GetFullPath($StateDir) } elseif ($env:CODEX_SUIXING_STATE_DIR) { [IO.Path]::GetFullPath($env:CODEX_SUIXING_STATE_DIR) } else { Join-Path $PSScriptRoot '.state' }
if ($TestRoot) {
    if (-not $SelfTest) { throw 'TestRoot is only available for isolated self-tests' }
    $script:ViewerRoot = [IO.Path]::GetFullPath($TestRoot)
    $script:ViewerState = Join-Path $script:ViewerRoot '.state'
}
$script:ViewerShortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'CodexSuixing.vbs'
function Write-TrayLog([string]$Message) {
    $file = Join-Path $script:ViewerState 'tray.log'
    if ((Test-Path -LiteralPath $file) -and (Get-Item -LiteralPath $file).Length -gt 1MB) { Move-Item -LiteralPath $file -Destination ($file + '.previous') -Force }
    Add-Content -LiteralPath $file -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $Message) -Encoding UTF8
}
function Get-ViewerProcess([string]$PidName, [string]$ScriptName) {
    try {
        $number = [int](Get-Content -LiteralPath (Join-Path $script:ViewerState $PidName) -Raw)
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$number"
        if ($process -and $process.CommandLine -and ($process.CommandLine.Contains((Join-Path $script:ViewerRoot $ScriptName)) -or $process.CommandLine.Contains((Join-Path $script:ViewerRoot 'companion.py')))) { return $process }
    } catch {}
    return $null
}
function Get-ViewerStatus {
    $mirror = Get-ViewerProcess 'sync.pid' 'viewer.py'
    $guard = Get-ViewerProcess 'supervisor.pid' 'sync_supervisor.py'
    $health = $null; $age = [double]::PositiveInfinity
    try { $health = Get-Content -LiteralPath (Join-Path $script:ViewerState 'sync-health.json') -Raw | ConvertFrom-Json; $age = [Math]::Max(0, ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($health.lastSuccess)).TotalSeconds) } catch {}
    $paused = Test-Path -LiteralPath (Join-Path $script:ViewerState 'sync-stop.flag')
    $tone = 'idle'; $label = '同步未运行'
    if ($paused) { $label = '同步已暂停' }
    elseif (-not $mirror -and $guard) { $label = '正在恢复同步'; $tone = 'warning' }
    elseif ($mirror) {
        $tone = 'warning'; $label = '正在连接云端'
        if ($health -and $age -le [Math]::Max(35, ([double]$health.pollInterval * 3 + 5)) -and -not $health.error) {
            if ($health.connected) { $tone = 'online'; $label = '同步正常' } else { $label = '同步正常 · CLI 未连接' }
        } elseif ($health) { $label = '连接中断 · 正在重试' }
    }
    return [PSCustomObject]@{Label=$label;Tone=$tone;Paused=$paused;MirrorRunning=[bool]$mirror;GuardRunning=[bool]$guard;LastSuccess=$health.lastSuccess;Interval=$health.pollInterval;AgeSeconds=$age}
}
function New-ViewerIcon([string]$Tone) {
    $bitmap = New-Object System.Drawing.Bitmap 64,64
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $dark = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(23,40,70))
    $white = New-Object System.Drawing.Pen ([System.Drawing.Color]::White),7
    $white.StartCap = [System.Drawing.Drawing2D.LineCap]::Round; $white.EndCap = $white.StartCap
    $badgeColor = switch ($Tone) { 'online' { [System.Drawing.Color]::FromArgb(46,190,126) }; 'warning' { [System.Drawing.Color]::FromArgb(245,171,51) }; default { [System.Drawing.Color]::FromArgb(145,157,178) } }
    $badge = New-Object System.Drawing.SolidBrush $badgeColor
    try {
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.FillEllipse($dark,3,3,58,58)
        $graphics.DrawLine($white,18,36,40,20); $graphics.DrawLine($white,29,20,40,20); $graphics.DrawLine($white,40,20,40,31)
        $graphics.FillEllipse($dark,39,39,25,25); $graphics.FillEllipse($badge,43,43,17,17)
        $handle = $bitmap.GetHicon()
        try { $wrapped = [System.Drawing.Icon]::FromHandle($handle); return $wrapped.Clone() } finally { [SuixingTrayNative]::DestroyIcon($handle) | Out-Null }
    } finally { $graphics.Dispose(); $bitmap.Dispose(); $dark.Dispose(); $white.Dispose(); $badge.Dispose() }
}
function Invoke-ViewerAction([string]$Action) {
    try {
        Write-TrayLog ('Action: ' + $Action)
        switch ($Action) {
            'start' { & (Join-Path $script:ViewerRoot 'start-sync.ps1') -NoAutoStart -NoTray -StateDir $script:ViewerState | Out-Null }
            'pause' { & (Join-Path $script:ViewerRoot 'stop-sync.ps1') -KeepAutoStart -KeepTray -StateDir $script:ViewerState | Out-Null }
            'restart' { & (Join-Path $script:ViewerRoot 'stop-sync.ps1') -KeepAutoStart -KeepTray -StateDir $script:ViewerState | Out-Null; & (Join-Path $script:ViewerRoot 'start-sync.ps1') -NoAutoStart -NoTray -StateDir $script:ViewerState | Out-Null }
            'web' { $profile = Get-Content -LiteralPath (Join-Path $script:ViewerState 'connection.json') -Raw | ConvertFrom-Json; Start-Process $profile.url }
            'logs' { Start-Process notepad.exe -ArgumentList ('"' + (Join-Path $script:ViewerState 'sync.log') + '"') }
            'folder' { Start-Process explorer.exe -ArgumentList ('"' + $script:ViewerRoot + '"') }
            'status' {
                $s = Get-ViewerStatus; $stamp = if ($s.LastSuccess) { ([DateTimeOffset]::Parse($s.LastSuccess)).LocalDateTime.ToString('HH:mm:ss') } else { '尚无成功同步' }
                $interval = if ($s.Interval) { $s.Interval.ToString() + ' 秒' } else { '等待连接' }
                [System.Windows.Forms.MessageBox]::Show(($s.Label + "`n`n最近同步：" + $stamp + "`n当前同步周期：" + $interval + "`n`n电脑需保持开机与网络连接。"), 'Codex 随行', 'OK', 'Information') | Out-Null
            }
            'exit' { & (Join-Path $script:ViewerRoot 'stop-sync.ps1') -KeepAutoStart -KeepTray -StateDir $script:ViewerState | Out-Null; $script:Context.ExitThread() }
            'tray-only' { $script:Context.ExitThread() }
        }
        Update-ViewerTray
    } catch {
        Write-TrayLog ('Action failed: ' + $_.Exception.Message)
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Codex 随行', 'OK', 'Error') | Out-Null
    }
}
function Update-ViewerTray {
    if (Test-Path -LiteralPath (Join-Path $script:ViewerState 'tray-stop.flag')) { $script:Context.ExitThread(); return }
    $status = Get-ViewerStatus
    $script:StatusItem.Text = 'Codex 随行 · ' + $status.Label
    $script:Notify.Text = 'Codex 随行 · ' + $status.Label
    if ($script:IconTone -ne $status.Tone) {
        $previous = $script:Notify.Icon; $script:Notify.Icon = New-ViewerIcon $status.Tone; $script:IconTone = $status.Tone
        if ($previous) { $previous.Dispose() }
    }
    $script:PauseItem.Text = if ($status.Paused -or -not $status.MirrorRunning -and -not $status.GuardRunning) { '恢复同步' } else { '暂停同步' }
    $script:AutoStartItem.Checked = Test-Path -LiteralPath $script:ViewerShortcutPath
    $public = @{pid=$PID;iconVisible=$script:Notify.Visible;label=$status.Label;tone=$status.Tone;mirrorRunning=$status.MirrorRunning;guardRunning=$status.GuardRunning;lastSuccess=$status.LastSuccess}
    $file = Join-Path $script:ViewerState 'tray-status.json'; $temp = $file + '.tmp'
    [IO.File]::WriteAllText($temp, ($public | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
    Move-Item -LiteralPath $temp -Destination $file -Force
}

$created = $false
$sha = [Security.Cryptography.SHA256]::Create()
try { $key = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($PSScriptRoot.ToLowerInvariant()))).Replace('-','').Substring(0,16) } finally { $sha.Dispose() }
$mutex = New-Object Threading.Mutex($true, ('Local\CodexSuixingTray-' + $key), [ref]$created)
if (-not $created) { $mutex.Dispose(); exit }
try {
    [IO.File]::WriteAllText((Join-Path $script:ViewerState 'tray.pid'), [string]$PID)
    $script:Context = New-Object System.Windows.Forms.ApplicationContext
    $script:Notify = New-Object System.Windows.Forms.NotifyIcon
    $script:Menu = New-Object System.Windows.Forms.ContextMenuStrip
    $script:StatusItem = $script:Menu.Items.Add('Codex 随行'); $script:StatusItem.Enabled = $false
    $script:Menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
    foreach ($entry in @(@('打开网页','web'),@('查看连接状态…','status'),@('重新启动同步','restart'))) {
        $item = $script:Menu.Items.Add($entry[0]); $action = $entry[1]; $item.Add_Click({ Invoke-ViewerAction $action }.GetNewClosure())
    }
    $script:PauseItem = $script:Menu.Items.Add('暂停同步')
    $script:PauseItem.Add_Click({ $s = Get-ViewerStatus; if ($s.Paused -or -not $s.MirrorRunning -and -not $s.GuardRunning) { Invoke-ViewerAction 'start' } else { Invoke-ViewerAction 'pause' } })
    $script:Menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
    foreach ($entry in @(@('打开同步日志','logs'),@('打开程序目录','folder'))) {
        $item = $script:Menu.Items.Add($entry[0]); $action = $entry[1]; $item.Add_Click({ Invoke-ViewerAction $action }.GetNewClosure())
    }
    $script:AutoStartItem = $script:Menu.Items.Add('登录时自动启动')
    $script:AutoStartItem.Add_Click({
        try {
            if (Test-Path -LiteralPath $script:ViewerShortcutPath) { Remove-Item -LiteralPath $script:ViewerShortcutPath }
            else {
                $python = if ($env:CODEX_SUIXING_PYTHON) { $env:CODEX_SUIXING_PYTHON } else { (& py -3 -c 'import sys;print(sys.executable)').Trim() }
                & $python (Join-Path $script:ViewerRoot 'companion.py') autostart --state-dir $script:ViewerState | Out-Null
            }
            Update-ViewerTray
        } catch { Write-TrayLog $_.Exception.Message }
    })
    $script:Menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
    foreach ($entry in @(@('仅隐藏托盘，继续同步','tray-only'),@('退出托盘和同步','exit'))) {
        $item = $script:Menu.Items.Add($entry[0]); $action = $entry[1]; $item.Add_Click({ Invoke-ViewerAction $action }.GetNewClosure())
    }
    $script:Notify.ContextMenuStrip = $script:Menu
    $script:Notify.Add_DoubleClick({ Invoke-ViewerAction 'web' })
    $script:Notify.Visible = $true
    Update-ViewerTray
    Write-TrayLog 'Tray icon started'
    if ($SelfTest) {
        if (-not $script:Notify.Icon -or -not $script:Notify.Visible) { throw 'Tray icon initialization failed' }
        Write-Output ('Tray initialized; menu items=' + $script:Menu.Items.Count + '; status=' + (Get-ViewerStatus).Label)
        if ($TestRoot) {
            $script:Menu.Items[4].PerformClick()
            if (-not (Test-Path -LiteralPath (Join-Path $script:ViewerState 'test-started'))) { throw 'Restart menu did not invoke start' }
            if (-not (Test-Path -LiteralPath (Join-Path $script:ViewerState 'test-stopped'))) { throw 'Restart menu did not invoke stop' }
            $script:PauseItem.PerformClick()
            Write-Output 'Isolated restart and resume menu callbacks passed'
        }
        return
    }
    $script:Timer = New-Object System.Windows.Forms.Timer
    $script:Timer.Interval = 2000
    $script:Timer.Add_Tick({ try { Update-ViewerTray } catch { Write-TrayLog ('Refresh failed: ' + $_.Exception.Message) } })
    $script:Timer.Start()
    [System.Windows.Forms.Application]::Run($script:Context)
} catch { Write-TrayLog ('Tray failure: ' + $_.Exception.Message); throw }
finally {
    if ($script:Timer) { $script:Timer.Stop(); $script:Timer.Dispose() }
    if ($script:Notify) { $script:Notify.Visible = $false; if ($script:Notify.Icon) { $script:Notify.Icon.Dispose() }; $script:Notify.Dispose() }
    if ($script:Menu) { $script:Menu.Dispose() }
    if ($script:Context) { $script:Context.Dispose() }
    $mutex.ReleaseMutex(); $mutex.Dispose()
}
