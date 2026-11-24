# 米家 PC 客户端（非官方）

> 本项目为**社区开发的非官方桌面客户端**，与小米公司不存在隶属、授权或背书关系。
> “米家”“Mi Home”及相关商标归其权利人所有。协议细节来自逆向分析与自有账户验证，
> 不含小米官方客户端二进制、完整插件包或反编译源码。

> 当前为 **0.7.x 公开测试版（Beta）**，不是 1.0 稳定版；设备分页与部分真机兼容性仍需验证。

Windows（Windows 7 SP1 起）与 Linux 上的米家桌面客户端：**纯标准库 Python + 手写网页 UI**，
不需要装任何第三方包，也不依赖 Electron。协议来自对 `com.xiaomi.smarthome` 11.9.705 的完整逆向。

> 当前版本 **0.7.37**。变更明细见 [CHANGELOG.md](CHANGELOG.md)，功能对照见
> [docs/04-功能对照.md](docs/04-功能对照.md)，逆向笔记在 [docs/reverse/](docs/reverse/)。

---

## 能做什么

| 能力 | 状态 | 说明 |
|---|---|---|
| 设备列表 / 卡片网格 | ✅ | 房间分组、在线状态、开关一眼可见 |
| 设备控制 | ✅ | 由 MIoT spec 自动生成控件（开关/滑杆/枚举/动作），不可用态按规则置灰 |
| 设备详情 | ✅ | 双栏：左控制、右状态与故障码；插件文案自动同步 |
| 设备信息 / 改名 | ✅ | 型号、厂商、固件、序列号、IP、MAC、token（打码）、最后上报；可改名 |
| 房间 | ✅ | 新建 / 重命名 / 删除 / 设备搬家；家庭改名 |
| 场景 | ✅ | 卡片一键执行；**新建（可多动作）与编辑** |
| 定时 / 预约 | ✅ | 列表与启停；**新建、改时间、删除**；每天 / 法定工作日 / 节假日 / 每周选周几 |
| 消息 | ✅ | 按类型的最新消息、未读数、全部标为已读、单条删除、**每台设备的历史消息** |
| 自动化 | ⚠️ | 列表、启停、改名改动作可用；**新建任意触发不可行**（见下文「已知限制」） |
| 添加设备 | — | 按需求**不做**（扫码配网留给手机 App） |
| 账号 | ✅ | 扫码登录后**无需重启**即切到账号数据；「账号与同步」里可**退出登录**（只删本机凭据，不影响手机米家） |

## 界面

下面全部是**演示模式**（未登录）的截图，设备与家庭名都是虚构的：

| 设备列表（spec 自动生成控件） | 设备详情（双栏） |
|---|---|
| ![设备列表](docs/images/home.png) | ![设备详情](docs/images/device.png) |

| 场景（执行 / 编辑 / 拖拽排序） | 消息中心 |
|---|---|
| ![场景](docs/images/scenes.png) | ![消息](docs/images/messages.png) |

## 下载与安装

**最新版从这里下载** —— [Releases · v0.7.37](https://github.com/shallow-seek-nothing/mihome-pc-unofficial/releases/tag/v0.7.37)

| 平台 | 文件 | 怎么用 |
|---|---|---|
| **Windows 64 位** | `mihome-pc-0.7.37-portable-win64.zip` | 解压后双击 `mihome.vbs`；目录里有 `使用须知.txt`，不需要装 Python |
| **Windows 32 位** | `mihome-pc-0.7.37-portable-win32.zip` | 同样解压即用；Win7 前置条件见下方实测说明 |
| Windows（安装版） | 解压 zip 后运行 `install.ps1` | 装到 `%LOCALAPPDATA%\Programs\MiHomePC`，桌面自动放快捷方式；`-Quiet` 静默 |
| Linux | 暂无（等 Windows 反馈稳定后单独发） | 之后是 `.deb` 与 `.tar.gz` 两种 |

> **32 位支持**：当前版本继续支持 32 位 Windows，并提供独立 win32 便携包；后续更新会尽量延续
> 32 位构建与基础兼容性验证。若运行环境或关键依赖变化影响支持范围，将在发布说明中明确注明。
>
> 绿色版内置 Python 3.8，在满足系统前置条件时**解压即用**；当前是 **0.7.x Beta**，标记为预发布。
>
> **Win7 SP1 实测（0.7.36）**：x64／x86 均已在虚拟机中跑通 Demo。裸 Win7 SP1 需先安装
> UCRT（KB2999226），并按系统安装要求重启；IE8 不支持，已验证 Firefox 115 ESR。
> 本轮覆盖 Demo 服务、接口与界面，不等同于真实账号和真实设备全功能验证。
> 0.7.37 的改动只在登录切换与账号面板，未在 Win7 复测。
> 详见 [Win7 SP1 x64／x86 实测报告](docs/win7-sp1-compatibility.md)。

### 从源码跑

```powershell
git clone https://github.com/shallow-seek-nothing/mihome-pc-unofficial.git
cd mihome-pc-unofficial
python -m mihome_pc serve        # 未登录时是演示数据
```

只需要 Python 3.8+，**没有任何第三方依赖**。

运行时会在右下角（Linux 在系统托盘）出现米家图标：左键打开界面，**右键可打开数据目录 / 退出**。

## 登录与退出登录

* 点右上角「扫码登录」，用米家 App 扫码。**确认后界面会自动切到账号数据，不用重启、
  也不用手动刷新**；凭据写在数据目录的 `credentials.json`，下次启动自动登录。
* 换账号 / 退出：侧栏「账号与同步」→「退出登录」（点两下确认）。它只删**本机**凭据，
  不吊销 `passToken`，手机上的米家 App 不受影响；退出后立刻回到演示模式。
* 0.7.36 及以前的**新装用户**扫码后仍停在演示模式（issue #1：进程只在启动时读一次凭据），
  那条「手动刷新一次」的土办法其实只有重启才起作用。0.7.37 已修，升级即可。
* 凭据文件损坏时界面会显示「凭据文件无法读取，请重新扫码登录」，不再是白屏或整页报错。

## 命令行

```powershell
python -m mihome_pc app          # 桌面模式：浏览器 app 窗口 + 托盘
python -m mihome_pc serve        # 只起本地网页（未登录时是演示数据）
python -m mihome_pc login        # 扫码登录，凭据存 ~/.mihome-pc/credentials.json
python -m mihome_pc devices      # 列出设备
python -m mihome_pc view DID     # 打印该设备 spec 生成的控件
python -m mihome_pc probe DID    # 可用性矩阵探测（默认演练，--commit 才写入）
python -m mihome_pc selftest     # 离线自检（不需要账号）
python -m mihome_pc selftest --env   # 附带数据目录/凭据/spec/插件文案的状态
```

网页支持深链接：`/?did=<did>` 设备页、`/?view=scenes`、`/?view=messages`、
`/?view=account`、`/?room=客厅`、`/?theme=dark`。

键盘：`/` 聚焦搜索、`Esc` 关闭弹窗/返回列表、搜索框里 `Enter` 直接打开唯一匹配的设备。

想看看「空账号」长什么样：`MIHOME_PC_DEMO_EMPTY=1 python -m mihome_pc serve`。

## 型号名与卡片布局（可选，本地生成）

**默认界面是通用的**：设备名用设备自己的 `model` 字段（例如 `minij.washer.v5`），
卡片使用通用布局。仓库与安装包**不包含**中文型号目录和逐型号卡片布局——那两份额外数据来自米家安装包，
本项目没有再分发权（见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 风险 1）。

如果你拥有米家安装包，可以**在你自己的机器上**生成，仅本机使用、不要公开再分发：

```powershell
python tools\make_catalog.py          # -> <数据目录>\catalog\catalog.json
python tools\make_card_layouts.py     # -> <数据目录>\catalog\card_layouts.json
```

* 数据目录默认 `%USERPROFILE%\.mihome-pc`（绿色版为程序目录旁的 `data\`）；
* 程序**优先加载**该目录里的文件，没有就自动用通用界面；
* **升级安装不会删除**这些文件（安装脚本只覆盖程序目录，数据目录保持不动）；
* 生成的 JSON 请不要提交到公开仓库或再次分发。

## 许可证

MIT，版权人 **shallow-seek-nothing**。见 [LICENSE](LICENSE)。

MIT **只覆盖本项目拥有权利的原创代码、测试与原创文档**。
第三方资源（MIoT spec 夹具、型号目录、卡片布局等）的来源与边界见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，它们不因放在本仓库中而自动转为 MIT。

> 第三方说明：本项目不包含小米的任何二进制、插件包或反编译产物；运行时下载的插件文案仅缓存在本地
> `data/` 目录、不随仓库或安装包分发。米家、Mi Home、小米是其各自权利人的商标，本项目与小米无隶属关系。

## 已知限制（与 `docs/04-功能对照.md` 一致，都写清楚了，不假装支持）

1. **设备列表只请求一页**（`limit=200`）：超过 200 台可能静默缺失；分页游标仍需协议证据与真机验证。
2. **场景 / 定时的新建依赖账号里已有一条同类场景作模板**（服务端校验 `sc_id` 与动作信封，手拼会被 `-998` 拒）；
   空账号是明确降级提示，**不是无条件新建**。执行与编辑不受影响。
3. **参数动作**：设备详情页能按 spec 生成 inline 输入并下发；**场景 / 定时编辑器不支持带参数动作**
   ——前端不列出、后端在写云端之前拒绝（fail-closed）。
4. **inline 动作的真机边界**：随包夹具没有 inline 动作，合成夹具通过**不等于**所有真实型号已验证。
5. **「下次执行时间」**：每天/每周精确；工作日按周一至周五估算，**未计法定节假日与调休**；
   法定节假日**不显示本地确定日期**（由云端判定）。
6. **自动化任意触发**、**新消息实时推送**、**添加设备**仍然不做，理由见 `docs/阶段总结.md`。

### 其它限制

* **新建自动化（设备事件触发）**：服务端会校验触发里的 `sc_id` 是否对应当前设备的
  spec-scene 表；手拼触发和原样复制已有触发都被 `-998 CheckTriggerConditionSrcKey…` 拒绝。
  这张表不在 miot-spec JSON 里，需要另做逆向。定时类自动化不受影响（`sc_id: 12` 是固定的）。
* **添加设备**：按需求不做。
* **消息推送**：没有常驻长连接；侧栏未读数每 45 秒轮询一次（页面不可见时跳过），不是实时推送。
* **Win7**：0.7.36 已在 Win7 SP1 x64／x86 虚拟机完成 **Demo 实测**；当前包需系统安装 UCRT
  并按安装要求重启，IE8 不支持，Firefox 115 ESR 已验证。真实账号与设备、Chrome 109、
  无 KB2533623 对照及随包内置 UCRT 尚未验证，详见 [实测报告](docs/win7-sp1-compatibility.md)。

## 架构

```
mihome_pc/
  crypto/        RC4 + AES(CBC/GCM)              协议层
  xiaomi/        sign / cloud / auth / spec / arsc  协议层（纯函数，可离线测）
  domain/        model（spec→控件）/ availability / scene / timer /
                 automation / message / plugin_text   纯逻辑，无 IO
  web/           server（http.server）+ static（手写 JS，无框架）
  tray*.py       Windows 托盘（PowerShell 优先，ctypes 兜底）
  packaging/     三个平台产物 + 结构校验
```

界面完全由 spec 驱动：新增一台设备不需要写任何 UI 代码。

## 开发

```powershell
python -m unittest discover -s tests   # 400+ 用例，全部离线
python -m ruff check .                 # 目标 py38；注意别显式给路径，会绕过 exclude
python packaging\build_win.py --clean  # 出 Windows x64 产物
python packaging\build_win32.py --clean  # 出 Windows x86 产物
python packaging\build_deb.py          # 出 Linux 产物
python packaging\verify_deb.py         # 产物结构校验
python packaging\check_release.py      # 发版前一致性检查（版本/产物/哈希）
```

每个功能都走了同一条验证线：单元测试 → `ruff` → `selftest` → **真账号实测**（包括改完再复原），
然后才打包。真机验证时对真实设备做的改动都会还原（改房间名、搬设备、建场景、改定时等都会复原或删除）。

## 目录

* [CHANGELOG.md](CHANGELOG.md) —— 每个版本做了什么、踩了什么坑
* [docs/发布检查清单.md](docs/发布检查清单.md) —— 发版前照这张表走（`python packaging/check_release.py`）
* [docs/阶段总结.md](docs/阶段总结.md) —— **逐条对照目标要求**：做到了什么、证据在哪、还剩什么
* [docs/03-现状与待办.md](docs/03-现状与待办.md) —— 当前状态与下一步
* [docs/04-功能对照.md](docs/04-功能对照.md) —— 对照米家 App 的功能矩阵
* [docs/win7-sp1-compatibility.md](docs/win7-sp1-compatibility.md) —— Win7 SP1 x64／x86 实测结论、前置条件与未验证边界
* [docs/reverse/](docs/reverse/) —— 协议、spec、插件、局域网四层逆向笔记
* [packaging/README.md](packaging/README.md) —— 三个平台的打包细节与坑
