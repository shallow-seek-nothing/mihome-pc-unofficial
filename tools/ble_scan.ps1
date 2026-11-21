# BLE scanner for Mi Home TV wake-up research.
#
# Runs on Windows PowerShell 5.1 (no installs, no third-party modules).
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File ble_scan.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File ble_scan.ps1 -Seconds 20
#   powershell -NoProfile -ExecutionPolicy Bypass -File ble_scan.ps1 -Known
#
# -Known lists devices Windows already knows instead of doing a live scan;
# try it when live scanning prints nothing (e.g. radio off or driver quirk).

[CmdletBinding()]
param(
    [int] $Seconds = 15,
    [switch] $Known
)

$ErrorActionPreference = 'Stop'

# WinRT 的异步等待辅助类型不在默认程序集里，必须先显式加载
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop
} catch {
    Write-Host "加载 System.Runtime.WindowsRuntime 失败: $($_.Exception.Message)" -ForegroundColor Red
}

function Write-Section($text) {
    Write-Host ''
    Write-Host "== $text ==" -ForegroundColor Cyan
}

# PowerShell 5.1 不能直接调用泛型 AsTask，得先把对应重载取出来再 MakeGenericMethod
$script:AsTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $script:AsTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

Write-Section '蓝牙无线电状态'
try {
    $radioType = [Windows.Devices.Radios.Radio, Windows.Devices.Radios, ContentType = WindowsRuntime]
    $request = [Windows.Devices.Radios.Radio]::RequestAccessAsync()
    $status = Await $request ([Windows.Devices.Radios.RadioAccessStatus])
    Write-Host "RadioAccess: $status"
    $radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
    foreach ($r in $radios) {
        Write-Host ("  {0,-28} kind={1} state={2}" -f $r.Name, $r.Kind, $r.State)
    }
} catch {
    Write-Host "RadioAccess 查询失败: $($_.Exception.Message)"
}

if ($Known) {
    Write-Section 'Windows 已知的 BLE 设备'
    try {
        [Windows.Devices.Bluetooth.BluetoothLEDevice, Windows.Devices.Bluetooth, ContentType = WindowsRuntime] | Out-Null
        $selector = [Windows.Devices.Bluetooth.BluetoothLEDevice]::GetDeviceSelectorFromPairingState($false)
        $op = [Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector)
        $devices = Await $op ([Windows.Devices.Enumeration.DeviceInformationCollection])
        foreach ($info in $devices) {
            Write-Host ("  {0,-40} {1}" -f $info.Name, $info.Id)
        }
    } catch {
        Write-Host "枚举失败: $($_.Exception.Message)"
    }
    return
}

Write-Section '实时扫描 BLE 广播'
try {
    [Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher, Windows.Devices.Bluetooth, ContentType = WindowsRuntime] | Out-Null
    $watcher = New-Object Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher
    $watcher.ScanningMode = [Windows.Devices.Bluetooth.Advertisement.BluetoothLEScanningMode]::Active

    $script:seen = @{}
    $handler = [Windows.Foundation.TypedEventHandler[Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher, Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementReceivedEventArgs]] {
        param($sender, $args)

        $mac = ($args.BluetoothAddress).ToString('X12')
        $mac = ($mac -split '(..)' | Where-Object { $_ } | ForEach-Object { $_.ToUpper() }) -join ':'

        $name = ''
        foreach ($d in $args.Advertisement.DataSections) {
            if ($d.DataType -eq 0x09 -or $d.DataType -eq 0x08) {
                $bytes = [System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeBufferExtensions]::ToArray($d.Data)
                $name = [System.Text.Encoding]::UTF8.GetString($bytes)
            }
        }
        $svc = @()
        foreach ($u in $args.Advertisement.ServiceUuids) { $svc += $u.ToString() }
        $mfg = @()
        foreach ($m in $args.Advertisement.ManufacturerData) {
            $bytes = [System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeBufferExtensions]::ToArray($m.Data)
            $hex = ($bytes | ForEach-Object { $_.ToString('X2') }) -join ''
            $mfg += ('0x{0:X4}:{1}' -f $m.CompanyId, $hex)
        }

        $key = $args.BluetoothAddress
        if ($script:seen.ContainsKey($key)) { return }
        $script:seen[$key] = $true

        $line = "{0}  {1,3}dBm  {2}" -f $mac, $args.RawSignalStrengthInDBm, $name
        Write-Host $line -ForegroundColor Yellow
        if ($svc.Count) { Write-Host ("    services: " + ($svc -join ', ')) }
        if ($mfg.Count) { Write-Host ("    mfg:      " + ($mfg -join ' ; ')) }
    }

    $token = $watcher.add_Received($handler)
    $watcher.Start()
    Write-Host "扫描中（$Seconds 秒）… 保持电视处于关机/待机状态"
    Start-Sleep -Seconds $Seconds
    $watcher.Stop()
    # remove_Received 要的是注册令牌，不是委托本身
    if ($token) { $watcher.remove_Received($token) }
    Write-Host ''
    Write-Host ("共发现 {0} 台 BLE 设备" -f $script:seen.Count) -ForegroundColor Green
} catch {
    Write-Host "扫描失败: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host '建议改用: ble_scan.ps1 -Known'
}
