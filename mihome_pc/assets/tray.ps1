# Mi Home PC —— 通知区域图标（兼容 Windows PowerShell 2.0 / Win7）。
#
# 为什么用脚本：用 ctypes 直接调 Shell_NotifyIcon 在某些启动方式下会返回
# ERROR_ACCESS_DENIED（实测本机就是），而 .NET 的 NotifyIcon 处处可用。
# 应用启动这个脚本并等它退出；图标退出 = 应用关服务器退出。
param(
  [Parameter(Mandatory=$true)][string]$Url,
  [Parameter(Mandatory=$true)][int]$AppPid,
  [string]$Icon = '',
  [string]$Title = '米家 PC',
  [string]$Folder = '',
  [string]$ReadyFile = ''
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$notify = New-Object System.Windows.Forms.NotifyIcon
if ($Icon -and (Test-Path $Icon)) {
  try { $notify.Icon = New-Object System.Drawing.Icon($Icon) }
  catch { $notify.Icon = [System.Drawing.SystemIcons]::Application }
} else {
  $notify.Icon = [System.Drawing.SystemIcons]::Application
}
$notify.Text = $Title
$notify.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip

$open = New-Object System.Windows.Forms.ToolStripMenuItem
$open.Text = '打开米家 PC'
$open.Add_Click({ [System.Diagnostics.Process]::Start($Url) })
[void]$menu.Items.Add($open)

$urlItem = New-Object System.Windows.Forms.ToolStripMenuItem
$urlItem.Text = $Url
$urlItem.Enabled = $false
[void]$menu.Items.Add($urlItem)

if ($Folder) {
  $folderItem = New-Object System.Windows.Forms.ToolStripMenuItem
  $folderItem.Text = '打开数据目录'
  $folderItem.Add_Click({ [System.Diagnostics.Process]::Start('explorer.exe', $Folder) })
  [void]$menu.Items.Add($folderItem)
}

[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))

$quit = New-Object System.Windows.Forms.ToolStripMenuItem
$quit.Text = '退出'
$quit.Add_Click({ [System.Windows.Forms.Application]::ExitThread() })
[void]$menu.Items.Add($quit)

$notify.ContextMenuStrip = $menu

# 注册成功后落一个标记文件：出问题时能一眼看出「图标到底有没有挂上去」
if ($ReadyFile) {
  try { Set-Content -Path $ReadyFile -Value ((Get-Date).ToString('s') + ' pid=' + $AppPid) -Encoding UTF8 } catch { }
}
$notify.add_DoubleClick({ [System.Diagnostics.Process]::Start($Url) })

# 应用没了就自己退出，避免留下一个点不动的图标
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 2000
$timer.Add_Tick({
  try { $null = Get-Process -Id $AppPid -ErrorAction Stop }
  catch { [System.Windows.Forms.Application]::ExitThread() }
})
$timer.Start()

try {
  [System.Windows.Forms.Application]::Run()
} finally {
  $timer.Stop()
  $notify.Visible = $false
  $notify.Dispose()
  if ($ReadyFile -and (Test-Path $ReadyFile)) { Remove-Item -Force $ReadyFile -ErrorAction SilentlyContinue }
}
