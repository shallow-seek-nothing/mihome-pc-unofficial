# Windows 7 SP1 x64／x86 实测报告（0.7.36）

日期：2026-11-24  
被测版本：`mihome-pc 0.7.36`  
结论：**Windows 7 SP1 已从“理论兼容”推进为“有条件通过 Demo 实测”**。

这里的“通过”指：在 Windows 7 SP1 x64／x86 虚拟机上，安装 UCRT 并重启后，便携包内的 Python、本地服务、演示接口和 Firefox 界面能够运行。它不等同于裸系统开箱即用，也不等同于真实米家账号、云端链路和真实设备控制已经完整验证。

## 1. 环境与被测物

| 项目 | x64 | x86 |
|---|---|---|
| 虚拟化 | VirtualBox 7.2.22 | VirtualBox 7.2.22 |
| 系统 | 官方 MSDN 中文 Windows 7 SP1，家庭普通版，6.1.7601 | 官方 MSDN 中文 Windows 7 SP1，旗舰版，6.1.7601 |
| 被测包 | `mihome-pc-0.7.36-portable-win64.zip` | 本次构建的 `mihome-pc-0.7.36-portable-win32.zip` |
| 内置解释器 | CPython 3.8.10 x64 | CPython 3.8.10 x86，现场复核确认 `32 bit` |
| 数据提供者 | `DemoProvider` | `DemoProvider` |

> 当前版本继续支持 32 位 Windows，并提供独立 win32 便携包；后续更新会尽量延续 32 位构建与基础兼容性验证。若运行环境或关键依赖变化影响支持范围，将在发布说明中明确注明。

## 2. 测试矩阵

| 阶段 | 系统状态 | Python／标准库 | 服务与接口 | 界面 |
|---|---|---|---|---|
| A | 裸 Windows 7 SP1 | `python.rc=-1073741515`，即 `0xC0000135`；缺少 UCRT | 无法启动 | 未进入 |
| B1 | 安装 UCRT 后、重启前 | Python 可启动，但 `_ssl`、`_ctypes`、`_sqlite3`、`_socket` 等仍出现 DLL 加载失败 | 未通过 | 未通过 |
| B2 | 安装 UCRT 并重启 | Python 3.8.10 正常，标准库检查通过 | 全部通过 | Firefox 115 ESR 完整渲染 Demo |
| C | 系统自带 IE8 | 服务端可正常运行 | 服务端正常 | 仅显示静态外壳，停留在“连接中…” |

x64 与 x86 的 A→B2 结果一致。重启要求对应本轮 `dism/msi` 系统安装路径及其 `3010` 返回状态，不应外推为所有 UCRT 部署方式都必然需要重启。

## 3. 复核证据

本轮先由自动化测试平台完成 A／B／C 阶段测试，随后对留存环境进行独立复核。

### x86 现场复核

- 系统为 Windows 7 SP1 x86，UAC 保持默认开启。
- Python 输出 `3.8.10 | 32 bit`，返回码为 0。
- `ssl`、`ctypes`、`sqlite3`、`socket`、`zlib` 全部导入成功，OpenSSL 为 1.1.1k。
- `/api/status`、`/api/devices`、`/api/meta`、`/` 均可在客户机内访问。
- `/api/devices` 长度为 67002；`/` 长度为 8281，且包含 `app.js`。
- `/api/meta` 返回版本 `0.7.36`、提供者 `DemoProvider`。
- Firefox 115 ESR 当前画面仍显示完整的 6 台演示设备、房间和开关。

### x64 重新解包冒烟

- 重新解包后共检查到 93 个文件，便携 Python 与包目录均存在。
- Python 3.8.10 启动成功，SSL／SQLite／ctypes／zlib 检查通过。
- 服务成功启动且未提前退出。
- `/api/status`、`/api/devices`、`/api/meta`、`/` 检查通过；设备数据和页面长度与 x86 一致。
- x64 界面证据采用测试平台留存的 Firefox 115 ESR 截图；本次独立新增的是运行时和接口复测。

## 4. 已确认的前置条件

### UCRT

裸 Windows 7 SP1 不预装 UCRT，而便携包内的 Python 3.8 需要它。当前测试包内：

- `api-ms-win-crt-*.dll` 数量为 0；
- `ucrtbase.dll` 不存在。

因此，**当前包仍依赖系统安装 UCRT**。通过系统中可见 15 个 UCRT shim 和 `ucrtbase.dll`。随包携带 UCRT DLL 是后续改进项，本轮尚未实现和验证，不能宣称已经支持免前置安装。

### 浏览器

- IE8 不支持当前前端。
- Firefox 115.21.0 ESR 已在 x64／x86 验证 Demo 界面。
- Chrome／Chromium 109 未在本轮单独实测，不应写成已经验证。

## 5. 尚不能下的结论

### KB2533623 是否必需，尚未分离验证

两台最终通过测试的系统均显示 `kb2533623=True`。虽然它们同时显示 `api-ms-win-core-path-l1-1-0.dll=False`，这只能证明该 core-path shim 不是运行所必需，**不能推出 KB2533623 整体非必需**。

若需下结论，必须增加“仅安装 UCRT、明确无 KB2533623、重启后”的 x64／x86 对照组。

### 真实米家功能尚未覆盖

本轮接口返回明确包含 `demo=true`、`DemoProvider`、未登录和无凭据状态。以下项目不在通过范围内：

- 真实账号扫码登录与凭据保存；
- 米家云端 HTTPS／TLS 链路；
- 真实设备列表、控制、场景和定时；
- 不同安全补丁、证书与系统区域设置组合。

### 宿主机端口映射另有独立问题

复核时，客户机内服务与接口正常，但宿主机访问 `127.0.0.1:18790` 持续出现连接重置。该现象不计为客户机应用冒烟失败，但“宿主机可直接浏览 Demo”目前不成立，需要单独检查服务监听地址和 VirtualBox NAT 映射。

## 6. 汇报口径

推荐表述：

> `mihome-pc 0.7.36` 已在 Windows 7 SP1 x64／x86 虚拟机上完成 Demo 实测。安装 UCRT 并按系统安装要求重启后，内置 Python、本地服务、演示接口和 Firefox 115 ESR 界面均可运行。IE8 不支持；真实账号与设备、Chrome 109、无 KB2533623 对照以及随包内置 UCRT 仍待后续验证。

不推荐表述：

- “裸 Windows 7 SP1 解压即用”；
- “Windows 7 全功能兼容”；
- “KB2533623 已证明非必需”；
- “UCRT 已随包携带”；
- “Chrome 109 已实测通过”。
