# 笔记本 DSH 任务单：米家电视 BLE 唤醒探测

> 给**笔记本上运行的 DSH**。这是一份自包含的交接单，不需要读别的文件。
> 完成后把结果写进本目录旁的 `out/ble-scan-result.md`，并回一段摘要给 lead（主会话）。
>
> 发起方：桌面机上的 DSH（lead）。时间：2026-11-20。

---

## 1. 这件事的来龙去脉（30 秒版）

我在做米家 APK 的逆向移植客户端（`mihome-pc`，Python 3.8 零依赖）。现在卡在一个具体问题上：

**电视关机后，米家 App 唤不醒它，只有小爱音箱能唤醒。**

我在 `xiaomi.tv.fsv1` 的 spec 里查到原因：

```
service 6  "BLE Local Control"   type: ble-control
   action 1  turn-on → 开机
```

「开机」这条动作挂在 **`ble-control`** 服务下 —— 说明唤醒走**手机蓝牙**，云端 `miotspec/action` 没有这条通路。桌面上没有蓝牙适配器（`GetRadiosAsync()` 返回空），所以需要你在笔记本上做第一手探测。

### 我已经掌握的技术事实

| 项 | 状态 |
|---|---|
| Windows BLE 传输（PowerShell 5.1 + WinRT） | ✅ 已验证：`RadioAccess: Allowed`，扫描器能正常启停 |
| 加密库位置 | ✅ APK 里有 `lib/arm64-v8a/libblecipher.so`（18 KB） |
| 加密原语 | ✅ `com.xiaomi.smarthome.device.bluetooth.security.BLECipher`：`nativeEncrypt(key, data, out)`、`nativeMixA(macBytes, [pid&0xFF, pid>>8], out)`，MAC 字符串先转成**逆序字节** |
| 电视的 BLE 广播长什么样 | ❓ **未知 —— 这就是要你查的** |
| 命令帧格式 | ❓ 未知（拿到广播后再判断） |

---

## 2. 你要做的事（按顺序，全在 Windows 上）

### 步骤 0：环境自检

在 PowerShell 里跑（**普通权限即可，不需要管理员**）：

```powershell
Get-PnpDevice -Class Bluetooth | Select-Object Status, FriendlyName
```

- 如果有输出 → 蓝牙适配器存在，继续
- 如果为空 → **先停下**，告诉用户"这台笔记本没检测到蓝牙适配器"，不要继续

### 步骤 1：确认蓝牙是开着的

```powershell
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$r = [Windows.Devices.Radios.Radio,Windows.Devices.Radios,ContentType=WindowsRuntime]
([Windows.Devices.Radios.Radio]::GetRadiosAsync()).GetResults() | Select-Object Name, Kind, State
```

期望看到 `Kind=Bluetooth` 且 `State=On`。如果 `State=Off`，请用户到设置里打开蓝牙。

### 步骤 2：拿脚本

优先找这个文件（如果工作区是共享/同步的）：

```
mihome-pc/tools/ble_scan.ps1
```

如果找不到，用下面的**完整内容**在任意目录新建 `ble_scan.ps1`。**保存为 UTF-8 带 BOM**（PowerShell 5.1 读中文注释需要 BOM，否则会按 GBK 解析报语法错）：

```powershell
# BLE scanner for Mi Home TV wake-up research.
[CmdletBinding()]
param(
    [int] $Seconds = 15,
    [switch] $Known
)

$ErrorActionPreference = 'Stop'

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop
} catch {
    Write-Host "加载 System.Runtime.WindowsRuntime 失败: $($_.Exception.Message)" -ForegroundColor Red
}

function Write-Section($text) {
    Write-Host ''
    Write-Host "== $text ==" -ForegroundColor Cyan
}

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
    if ($token) { $watcher.remove_Received($token) }
    Write-Host ''
    Write-Host ("共发现 {0} 台 BLE 设备" -f $script:seen.Count) -ForegroundColor Green
} catch {
    Write-Host "扫描失败: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host '建议改用: ble_scan.ps1 -Known'
}
```

### 步骤 3：**让用户把电视关机**（这是关键前提）

要观测的是电视**待机时**是否还在广播。请明确要求用户：

> 请把电视用遥控器关掉（待机），保持电源插着。然后告诉我可以了。

### 步骤 4：跑扫描（扫 30 秒，比默认长一点）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ble_scan.ps1 -Seconds 30
```

**把完整输出原样保存**（下面要填进结果文件）。

### 步骤 5：如果发现 0 台，按顺序排查

1. 蓝牙确实开着？（步骤 1）
2. 电视是不是被彻底断电而不是待机？待机才有 BLE。
3. 手机（装了米家）是不是也在旁边且蓝牙开着？先在手机米家 App 里打开一次电视页面，让电视的 BLE 被激活，再立刻跑扫描。
4. 换 `-Known` 模式看系统已知设备：
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\ble_scan.ps1 -Known
   ```
5. 如果仍然什么都没有，跑一次更彻底的诊断并把输出带上：
   ```powershell
   Get-PnpDevice -Class Bluetooth | Format-List Name,Status,InstanceId
   Get-Service bthserv | Select-Object Status,StartType
   ```

---

## 3. 结果要写成什么样

新建 `out/ble-scan-result.md`（若工作区不共享，就把内容直接回给 lead），按这个模板填：

```markdown
# BLE 扫描结果（笔记本）

## 环境
- 系统: Windows <版本>
- 蓝牙适配器: <Get-PnpDevice 输出里的 FriendlyName>
- RadioAccess: <Allowed / Denied / ...>
- 蓝牙 State: <On / Off>

## 扫描原始输出（-Seconds 30）
<把整段输出粘进来，包括 "共发现 N 台 BLE 设备">

## 电视相关条目（人工判断）
- MAC: <如果认出电视，填这里>
- 名称: <广播里的名字>
- service UUIDs: <全部列出>
- manufacturer data: <0xFE95:.... 这种>
- RSSI: <-xx dBm>

## -Known 模式输出
<如果跑过就贴>

## 判断
- [ ] 电视待机时确实在广播 BLE
- [ ] 广播里带小米的 0xFE95 (MiBeacon)
- [ ] 广播里带其它 service UUID（列出）
- [ ] 完全没看到电视

## 备注
<任何异常、报错、需要 lead 知道的事>
```

---

## 4. 纪律（重要）

1. **不要动电视以外的设备**，不要发任何写入/控制命令 —— 这一步只做被动扫描。
2. **不要碰 `mihome-pc` 里的 Python 代码**，这一步不需要改代码。
3. **不要安装任何第三方库**（`bleak`、`pywinrt`、Python 的蓝牙包都不行）。整个项目要求零依赖，传输层只能走 PowerShell + WinRT。
4. 脚本用**普通权限**跑。若报权限错误再来找用户确认，不要自行提权。
5. 如果电视正在播放/正在被使用，**先问用户**再让他关机。

---

## 5. 回给 lead 的摘要格式

做完后回一段话，包含：

1. 蓝牙适配器型号 + `RadioAccess` 结果
2. 扫到几台设备，其中是否认出电视（MAC / 名字）
3. 电视广播里的 **service UUID** 和 **manufacturer data 十六进制**
4. 你的判断：`0xFE95`（米家私有）还是标准 GATT，或者没扫到
5. 结果文件写在哪

**不要**只说"扫完了"——我需要上面这几项具体数据才能继续构造命令帧。
