# 更新记录

本项目遵循「能跑 + 有证据」的原则：每条结论后面都跟着真机验证或 `文件:行号` 证据。
版本号规则：`0.x.y` 阶段仍在迭代 UI 与设备细节，接口与数据格式可能变。

---

## 0.7.40 — 2026-11-26

把「日志与排障」做成一层基础设施。起因很直接：0.7.38 的 deb 装不上，
现场只有一句 dpkg 报错和自己的 `print`，没有级别、没有请求 id、没有耗时，
排障全靠猜。

### 1. 统一日志层 `mihome_pc/logs.py`（零第三方依赖）

一行一条，格式固定：

```
2026-11-26T19:26:15.007+08:00 WARNING web      ec383171 api GET /api/device -> 400: did required
└──────── 时间（ISO8601，毫秒 + 时区）· 级别 · 模块 · 请求 id · 正文 ────────┘
```

* **时间带毫秒和时区偏移**（`isoformat(timespec="milliseconds")`），跨机器不用再对时；
  原来 `web.log` 只有秒级、无时区。
* **模块**：`web` / `cloud` / `app` / `cli` / `auth` / `crash`，一眼看出是哪一层。
* **滚动**：`RotatingFileHandler`，默认 2 MiB × 5 份，日志不会无限长大。
* **级别**：`mihome-pc --log-level debug|info|warning|error`，或环境变量
  `MIHOME_PC_LOG_LEVEL`；默认 info。
* 旧的 `web.log`（`log_line()` 直接拼字符串）被这条路取代，调用点保留函数名但走统一格式。

### 2. 密钥脱敏（写盘前统一做）

`serviceToken` / `passToken` / `ssecurity` / 设备 `token` / `Cookie` / `Bearer` 一律打码成
`a1b2…(32)`（保留前 4 位 + 长度，够判断是哪一把又不可复用）。
**错误码、did、接口路径不脱敏** —— 这些恰恰是排障要看的（第一版差点把 `code` 也打掉）。

### 3. 请求可追踪：请求 id 贯穿三处

每个 HTTP 请求生成 8 位 `request_id`：

* 日志每行带 `req=xxxxxxxx`（线程作用域，云端调用自动继承）；
* 响应头 `X-Request-Id`；
* 失败 JSON 里的 `request_id`。

三处同值 → 用户把弹框里的 id 贴出来，开发 `grep` 一下就是这一次请求的完整链路。
HTTP 层同时记录访问日志（方法、路径、状态、耗时），`GET /static/*` 降级到 DEBUG 免得刷屏。

### 4. 云端调用留痕

`xiaomi/cloud.py` 每次 `post()` 都记：接口路径、HTTP 状态、协议 `code`、耗时、响应字节数；
失败记 `WARNING` 并带 code/message。**成功也记**——「到底调没调、多久、返回码」同样是排障信息。

### 5. 失败弹框升级为「诊断框」

界面里 32 处「xxx 失败：消息」的 toast 换成诊断框：标题（哪个操作）、人话说明、
`请求 ID / HTTP 状态 / 错误码 / 接口路径`，以及**一键复制**的格式化诊断文本（含版本号）。
`Esc` / 点遮罩可关；不支持 `navigator.clipboard` 时退化为 `execCommand('copy')`。

### 6. 崩溃现场与诊断包

* `install_excepthook()`：未捕获异常写 `logs/crash-<时间>.log`（环境信息 + 完整 traceback），
  pythonw 下没有控制台时这是唯一现场。
* `mihome-pc report`：把 `environment.txt`（版本/Python/系统/数据目录/日志级别/有无凭据）+
  全部日志打成 zip，**不含凭据与设备 token**；报 issue 只发这一个文件。
* `selftest --env` 增加「日志 / 日志级别 / 崩溃记录」三行。
* `serve` 现在会处理 `SIGTERM`/`SIGHUP` 并记一行「signal N -> shutdown」，所以
  `mihome-pc stop` 在 Linux 上留下的是可解释的日志，不是被硬杀。

### 7. 测试

新增 18 条日志断言（格式、请求 id 作用域与嵌套、四类脱敏、错误码不被误伤、滚动、
级别过滤、环境信息无凭据、诊断包不含凭据）+ 3 条 HTTP 断言
（400/404 都带 `X-Request-Id` 且与 body 的 `request_id` 一致、每次请求 id 不同）。
全量单测 583 条。

文档：新增 [docs/05-日志与排障.md](docs/05-日志与排障.md)（路径、格式、级别、脱敏、
弹框字段、报 bug 标准动作、排障对照表、改代码时的约定）。

---

## 0.7.39 — 2026-11-26

修 laptop 的发布阻塞单 `MH-20261126-01`：0.7.38 的 **deb 在 Debian 系装不上**，
另两条是 Linux 侧的文档与打包缺口。

### 1. [P0] deb 装不上：`data.tar` 只有文件、没有目录条目

* **现象**：`apt install` / `dpkg -i` 都报
  `无法创建 /usr/lib/mihome-pc/mihome_pc/__init__.py.dpkg-new: No such file or directory`，
  装完停在 `install ok not-installed`；`dpkg-deb -c` 显示 **63 个文件、0 个目录条目**。
* **根因**：`build_tar_gz()` 只 `os.walk` 出文件逐个写进 tar，从不写目录条目；
  dpkg 解包时**不会替文件建父目录**，于是第一个需要新建的目录就夭折。
  同一条产线的 v2.0.0 有 9 个目录条目，所以属 0.7.x 的回归，与发行版无关。
* **修法**：目录与文件都显式入包——目录条目前置排序（`./`、`./usr/`、`./usr/bin/`…），
  文件继承其后；顺带把权限归一（目录 0755、普通文件 0644、`/usr/bin/mihome-pc` 0755），
  不再把构建机（Windows）的 0666 原样打进包里。
* **控制字段去占位符**：`Maintainer` 换成真实地址，`Homepage` 指向仓库主页
  （原来分别是 `noreply@example.invalid` 和 `https://github.com/`）。
* **CI 门禁**：`packaging/verify_deb.py` 增加「目录条目 > 0」「每个文件的父目录都在包内」
  「无 0666/0777」「图标齐全」「控制字段无占位符」等断言；新增
  `.github/workflows/check.yml`，在 Debian/Ubuntu runner 上**真的 `sudo dpkg -i` 一次**
  再跑 `mihome-pc selftest`，装不上直接红。

### 2. [P1] Linux 其实没有托盘 —— 文档与实现对齐，并补 `mihome-pc stop`

* `tray.py` 是 `ctypes` + `Shell_NotifyIconW` 的 Windows 实现，文件头自己写着 Linux 下
  `available()` 为 False；README 却写「Linux 在系统托盘」。现在 README 按平台分写：
  Windows 有托盘（左键界面 / 右键数据目录与退出），**Linux 明确没有托盘**。
* 新增 `mihome-pc stop`（`mihome_pc/app.py: stop()`，锁文件逻辑抽到 `mihome_pc/lockfile.py`）：
  读数据目录的 `app.lock` 找 PID 与 URL，先 HTTP 探活再 `SIGTERM`，顺手清理过期锁。
  `serve` 现在也会写 `app.lock`（此前只有 `app` 写），所以两种启动方式都能停。
  锁用 `os.replace` 原子写入；Windows 上**不用** `os.kill(pid, 0)` 做探活
  （CPython 在 Windows 把任何非 CTRL_C 信号映射成 `TerminateProcess`）。

### 3. [P2] tarball 补桌面集成

* tarball 内新增 `share/applications/mihome-pc.desktop` 与
  `share/icons/hicolor/{48x48,128x128,256x256}/apps/mihome-pc.png`；
  `install.sh` 把它们装到 `<prefix>/share/…`，`--prefix ~/.local` 同样生效，
  并 best-effort 刷新 icon cache / desktop database。
* `.desktop` 的 `Exec` 改为 `mihome-pc app`（有窗口语义的入口），补 `TryExec`；
  deb 里也安装 PNG 图标——之前 `.desktop` 指向 `Icon=mihome-pc` 但包里根本没有 PNG。
* 图标由 `packaging/make_icons.py` 从 `static/logo.png` 生成（开发侧用 Pillow；
  产物已入库，构建本身仍然零第三方依赖）。

### 4. 顺手

* tarball 的 launcher 默认参数从 `serve` 改成 `app`，与 .desktop 一致。
* 版本号 0.7.37 → 0.7.39（0.7.38 已发但包有问题，直接跳版重发）。

---

## 0.7.37 — 2026-11-24

修 [#1](https://github.com/shallow-seek-nothing/mihome-pc-unofficial/issues/1)：
新装用户扫码登录成功后界面仍停在演示模式；顺带补上社区一直缺的**退出登录**。

### 1. 扫码登录后不再需要重启进程（issue #1）

* **根因**：`_Handler.provider` 把 `_credentials_stamp() is None`（"没有凭据文件"，
  也就是**全新安装**的正常状态）当成了「不要跟随文件」的哨兵。
  新装启动时 stamp 本来就是 `None`，于是扫码写入 `credentials.json` 之后，
  属性永远返回启动时构造的 `DemoProvider` —— 页面提示登录成功、数据还是演示，
  只有**重启程序**（启动时重新读文件）才生效。0.7.36 里「无需重启」只对
  **早就登录过**的机器成立，这正是评论区"登录后没反应、手刷一下"的来源。
* **修法**：`provider_state` 增加显式的 `follow` 字段（`provider=None` 时为 True，
  测试显式传 provider 时为 False），`stamp` 只当变更检测；
  `stamp` 由 mtime 改为 `(mtime, size)`，同一时钟刻内重写也能发现；
  切 provider 时加锁，避免并发请求重复构造 `CloudProvider`。
* `make_provider()` 遇到损坏/半写入的 `credentials.json` 记日志并退回演示，
  不再让每个请求 500；`/api/status` 增加 `provider` / `credentials` /
  `credentials_error`，一眼能分清「没登录」「被换掉」「凭据坏了」。
* `auth.save_credentials()` 改为 `os.replace` 原子替换（原来
  remove + rename 中间有空档，读者会看到"未登录"并瞬间切回演示），
  tmp 名带 pid，写完 fsync。
* 前端：扫码 ok 后不再盲等 600ms，而是轮询 `/api/status`
  直到真的不是演示模式再 `boot()`；约 3 秒仍没切过来才提示按 F5。
  `focus` / `visibilitychange` 也核对账号状态（手机上扫码后切回窗口立刻切换）；
  `boot()` 把 status 与 devices 分开兜底，云端失败时深链接与账号面板仍然可用。

### 2. 新增「退出登录」

* `POST /api/logout`：删掉本机 `credentials.json`，provider 立刻切回演示，
  并作废进行中的扫码（`LoginFlow` 加 generation 号，退出后迟到的扫码结果
  不会把凭据写回来）。**只删本机文件、不吊销 passToken** —— 账号是和手机
  米家 App 共用的，吊销会把人从手机上也踢下线。
* 界面：「账号与同步」面板里，已登录显示「退出登录」（点两下确认，防误触），
  演示模式显示「扫码登录」。面板对 `/api/meta` 失败单独兜底：云端或 token
  有问题时「退出登录」按钮仍然可用（0.7.36 里整个面板会一起报错）。
* 写入接口沿用 `_request_allowed(write=True)` 的 Host / Origin /
  Sec-Fetch-Site 校验，跨站 POST 退出返回 403。

### 证据

* 新增测试：`tests/test_web.py::TestLoginSwitchesProvider`（6 条：首次登录即切
  CloudProvider、退出回演示、再登录、换账号、跨站退出 403、follow 标志）、
  `TestBrokenCredentials`（3 条：坏 JSON 不 500、非对象 JSON 也退回演示、
  文件被外部删除回演示）、`TestLoginFlowReset`（2 条）；`tests/test_auth.py` 原子写 1 条。
* `out/verify_login_ui.py`：headless Edge 真跑「演示 → 写入凭据 → 已登录 →
  退出」三种状态并读 DOM，11 项全 PASS（截图在 `out/ui/`）。
* `out/verify_portable_0_7_37.py`：解压**打包产物**，用包内 python38 起服务，
  验证「新装是演示 → 写入凭据即切 CloudProvider → 退出回演示」，
  并跑 `tools/regression.py`（11 项只读巡检，0 failed）。x64 与 x86 两个包各跑一遍。
* 全量 **553** 条 unittest OK（跳过 3），`ruff` 全绿，`check_release.py` 全过。

### 工程

* 版本 0.7.37；测试 540 → **553**。
* 复核补丁（提交后复查发现）：账号面板曾把「退出登录」/`credentials_error`
  渲染两遍，已删掉重复；扫码线程「查 generation 再写凭据」与退出登录的
  `reset()` 改用同一把锁，迟到的扫码结果不会把文件写回来；`credentials.json`
  是合法 JSON 但不是对象（`[1, 2]`、`"x"`）时 `Credentials.from_dict` 抛的是
  `AttributeError`，现在也按「损坏」退回演示；测试里屏蔽掉切 provider 触发的
  后台插件同步，单元测试不再出网。
* `build_win.py`（x64）与 `build_win32.py`（x86）都出了便携包，外加 deb / tar.gz；
  `release_notes.py` 的产物表自动收录本版本全部文件（4 个），`check_release.py` 全过。
* 两个便携包分别用**包内解释器**验证：x64 是 64 位、x86 是 32 位，
  使用须知含「退出登录」与「不用重启程序」。

---

## 0.7.36 — 2026-11-21

按所有者决定：**Windows 安装包也不再附带** `catalog.json` / `catalog_categories.json` / `card_layouts.json`。
来源不明的再分发风险已从仓库、安装包、绿色版、Linux 包中统一移除。

### 运行时改为从用户数据目录加载

* `domain/model.py::_catalog_dirs()`：先查 `<数据目录>/catalog/`，再查内置 `assets/`（现在为空）；
  文件缺失或 JSON 损坏都返回空字典，界面用通用布局，不崩。
* 新增 `display_model()`：型号名不再直接展示 `urn:miot-spec-v2:device:washer:...`，
  而是取设备自身的 `model`（如 `minij.washer.v5`），拿不到时取 URN 里的设备类型段（`washer`）。
  `devices()` 与 `CloudProvider.view()` 的展示名回退都改用它。

### 生成工具改写到数据目录

`tools/make_catalog.py`、`tools/make_card_layouts.py` 的输出从 `mihome_pc/assets/` 改到
`<数据目录>/catalog/`，运行时会打印「仅本机使用，请勿公开再分发」。

### 测试（新 `tests/test_catalog_local.py`，5 条）

用**合成的小型目录**验证加载与降级，不依赖被剔除的 APK 数据：
缺失→空、目录存在→读到、卡片布局→读到、坏 JSON→空不崩、`display_model` 不吐完整 URN。

### 升级不删用户数据

`install.ps1` 只覆盖程序目录（`%LOCALAPPDATA%\Programs\MiHomePC`）与快捷方式，
数据目录 `%USERPROFILE%\.mihome-pc` 明确保留（卸载时也提示「数据目录没有删除」）。

### 工程

* 测试 535 → **540**（跳过 3：符号链接权限、依赖 catalog 的用例等）；`ruff` 全绿；产物 0.7.36。

---
## 0.7.35 — 2026-11-21

发布前收尾：非官方声明、MIT（署名 shallow-seek-nothing）、第三方资源边界、README 功能口径对齐。

### 1. 场景动作兜底 fail-closed（0.7.34 已完成，本轮补齐两条完整路径测试）

新增走 `create_scene()` 的两条：
* `test_create_scene_rejects_parameter_action`：带参数动作 → `ok=False`、`edit_scene` 0 次
* `test_create_scene_succeeds_for_parameterless_action`：无参数动作 → `ok=True`、`edit_scene` 1 次

### 2. 非官方定位

README 顶部加声明：社区开发的非官方客户端，与小米无隶属/授权/背书关系；
“米家”“Mi Home” 商标归其权利人；协议细节来自逆向分析与自有账户验证；不含官方二进制与反编译源码。
仓库名定为 `mihome-pc-unofficial`，展示名「米家 PC 客户端（非官方）」；**包名/命令/构建脚本不改**。
版本口径改为 **0.7.x 公开测试版（Beta）**。

### 3. LICENSE（MIT）与 THIRD_PARTY_NOTICES

* `LICENSE`：标准 MIT 全文，版权人 **shallow-seek-nothing**。
* `THIRD_PARTY_NOTICES.md`：逐项列出 resources 的来源、生成方式、再分发依据与结论；
  明确 MIT 只覆盖原创代码/测试/原创文档，第三方不因入库而转 MIT。

### 4. 第三方资源审计结论（含一个阻断项）

| 资源 | 来源 | 结论 |
|---|---|---|
| logo.png / favicon.png / mihome.ico | 本项目自制（ico 由 logo 生成） | MIT |
| dynamic_ranges.json | 本项目实测（探针逐档写入回读） | MIT |
| 插件文案、场景图标 | 运行时不随包分发 | 不涉及再分发 |
| MIoT spec 夹具 / demo spec | 公开规范页面 | 未确认，按公开规范数据使用，不声称 MIT |
| **catalog.json / catalog_categories.json / card_layouts.json** | 由**米家 APK 内置配置**提取 | **再分发依据未确认 → 发布风险 1** |

**处置**：这三个文件已加入 `.gitignore`，**源码仓库与源码 ZIP 不再包含**；
代码对缺失是优雅降级（无中文型号名/通用卡片布局）。需要完整型号目录的用户可
`python tools/make_catalog.py && python tools/make_card_layouts.py` 用**自己拥有的安装包**本地生成。
Windows 安装包是否附带，留待所有者决定（见 THIRD_PARTY_NOTICES 风险 1）。

### 5. README / 文档对齐

README「已知限制」按 `docs/04` 重写为六条准确口径（分页 200 台、场景/定时新建依赖模板、
参数动作编辑器不支持且后端 fail-closed 拒绝、inline 真机边界、下次执行时间的近似与缺失、
自动化任意触发/实时推送/添加设备不做）。
`docs/发布到GitHub.md` 更新为已定 MIT + `mihome-pc-unofficial` + 第三方边界。

### 工程

* 测试 533 → **535**；`ruff` 全绿；`selftest` ok；`check_release` ok；产物 0.7.35。
* **未执行**：`git push`、tag、公开 Release（按任务要求）。

---
## 0.7.34 — 2026-11-21

聚焦复核只剩一处：场景后端兜底在「判断不了」时**放行**（fail-open）。改成失败关闭（fail-closed）。

### 问题

`_action_params_reason()` 在两种情况下返回空字符串（= 放行）：
1. `view()` 抛异常（spec 拉不到）；2. 循环里找不到匹配的 siid/aiid（动作不存在）。
复核用探针证明这两种情况都会真的调用 `edit_scene()` 一次并返回 ok=True。

### 修法

三个分支都拒绝：
* spec 读取异常 → 「无法读取设备动作定义，请刷新设备后重试」
* `view is None` → 同上
* 找到动作但**没有匹配** → 「设备没有这个动作 %d.%d，已停止创建」
* 找到且 `params` 为空 → 才是真正的放行

### 测试（3 条，走完整 `create_scene()` 路径）

* `test_create_scene_fails_closed_when_spec_unavailable`：`view()` 抛错 → `ok=False`，`edit_scene` 0 次
* `test_create_scene_rejects_unknown_action`：siid/aiid = 2.99 → `ok=False`，消息含「没有这个动作」，0 次
* `test_parameterless_action_is_allowed_by_the_guard`：无参数动作仍被放行（防误拦回归）

### 工程

* 测试 530 → **533**；`ruff` 全绿；`selftest` ok；产物 0.7.34。
* 仍未解决：P1-4 设备分页（无协议证据）、inline 动作真实型号兼容性（需真机）。

---
## 0.7.33 — 2026-11-21

开源准备：加 MIT 许可证，并把发布文档补齐（仓库名合规写法、许可证说明）。

### 新增 `LICENSE`

MIT，版权所有写 `Copyright (c) 2026 MiHome-PC contributors`。
README 增加「许可证」一节，同时写明：仓库与安装包**不包含**小米的二进制、插件包或反编译产物；
运行时缓存的插件文案只落在本地 `data/`；米家/小米是其各自权利人的商标，本项目与小米无隶属关系。

### 发布文档

`docs/发布到GitHub.md` 补了「仓库名合规写法」：GitHub 仓库名不允许斜杠，
给出的候选是 `Mijia_Win7-8.1-10-11` / `Mijia-Win7-Win10-Win11` / `mijia-pc`，标题可以照旧写全称。

### 工程

* 测试 530 条全绿；`ruff` 全绿；产物 0.7.33（`LICENSE` 已随 zip/deb 分发）。

---
## 0.7.32 — 2026-11-21

按 0.7.31 的独立验收意见返修 P1-5 的三处问题，并把文档完成度标记改准确。

### 1. 非法 inline 值不再被静默改写（真 bug）

验收意见用探针复现：布尔参数传 `"not-a-bool"` 返回 `ok=True`、发出 `False`；整数参数传 `1.5` 返回 `ok=True`、
发出 `1`。这是**静默篡改用户输入**，比直接报错危险。

改法：新增 `_strict_int()` / `_strict_bool()`，
* 整数：拒绝 bool、拒绝带小数的浮点（`1.5` 报错而不是截断）、字符串必须是纯整数；
* 布尔：只接受 `true/false/on/off/yes/no/1/0` 与 0/1，其余报错；
* 枚举：改用 `_strict_int()`，`1.5` 不会再变成合法枚举 1；
* 文本：只接受 `str`，数组/对象一律拒绝（不做 `%s` 格式化）。

### 2. 文本参数不再渲染成数字框

`param.format === "string"` → `type="text"`；数字框补 `step`（float 用 0.1，整数用 1）。

### 3. inline-only 动作不再混进场景/定时编辑器

* 前端过滤条件从 `in_refs.length` 改成 `(params.length || in_refs.length)`——纯 inline 动作 `in_refs` 为空，
  以前会漏过去并带 `inputs: []`。
* 后端 `_build_actions()` 增加兜底 `_action_params_reason()`：按 did/siid/aiid 找到 `ActionControl`，
  只要有 `params` 就返回「场景/定时编辑器暂不支持带参数的动作（…）」，**不调用** `edit_scene()`。

### 4. 文档完成度标记

`docs/04` #7/#9 从整行 ✅ 改成「执行/编辑 ✅，**新建 ⚠️** 依赖已有同类模板」，
并注明带参数动作在编辑器里前端不列、后端拒绝。

### 测试

`tests/test_action_inputs.py` 9 → **15** 条，新增：
* `test_bool_string_must_be_explicit`（`"not-a-bool"` → 失败，云端 0 次）
* `test_integer_rejects_fractional`（1.5 → 失败，0 次）
* `test_enum_rejects_fractional`（枚举 1.5 → 失败，0 次）
* `test_string_param_rejects_non_text`（数组 → 失败，0 次）
* `test_integer_rejects_bool`（True → 失败，0 次）
* `test_scene_editor_rejects_parameter_actions`（`_build_actions` 拒绝带参数动作）

### 结果

* 测试 524 → **530**；`ruff` 全绿；`selftest` ok；产物 0.7.32。
* 仍未解决：P1-4 设备分页（无协议证据）、inline 动作真实型号兼容性（需真机）。

---
## 0.7.31 — 2026-11-21

按第二轮复审的「合并修复任务」（基线 0.7.30）完成 P0 与四个 P1；P1-4 因拿不到分页协议证据而**明确保持未解决**。

### P0 无 Content-Length 的 POST 类型绕过（修好）

`_read_json()` 里 media type 校验移到**所有** Content-Length 提前返回之前。
`text/plain` + 无 Content-Length + 无 Origin → 415；`application/json` + 无 body 仍按空对象处理。
两条新测试用 `http.client.putrequest()` 手工构造（避免 `request()` 自动补 CL）。

### P1-1 属性写入先校验后调云端（修好）

`CloudProvider.set_property()` 重排为：属性存在 → 可写 → `coerce()` → `accepts()` → availability → 型号限制 →
**最后**才 `set_props()`；成功后保存**转换后**的值。抽出了共享的 `validate_write()` 与
`load_availability_evaluator()`（后者返回可调用 `is_enabled()` 的评估器，`load_availability()` 仍返回字典给 API）。
`tests/test_write_validation.py` 6 条：不存在/只读/类型错/超限/availability 全部断言 **云端调用次数为 0**，
合法值断言只调用 1 次且发的是转换后的值，云端拒绝时不写本地状态。

### P1-2 空账号新建场景/定时（改为诚实降级）

`_scene_template()` 改成返回 `(template, action, reason)`，不再返回 `None` 让调用方解包崩：
* 没有任何场景 → 「账号里还没有任何场景，新建需要先在手机 App 里建一条同类场景作为模板」
* 有场景但缺该 command 的动作 → 「账号里还没有 action 动作可作模板」
* 列表/详情读取失败 → 「读取场景列表失败（可能是网络或权限问题）」

`create_scene()` / `create_timer()` 在模板为空时返回 `{ok: false, code: "no_template", message: …}`，
**不调用** `edit_scene()`；异常分支不再回显原始异常文本，只给「创建失败（错误类型），详情见日志」。
界面空态文案同步改为说明前置条件。文档 #7/#9 已改为 ⚠️ 并写明依赖。

### P1-3 下次执行时间的可信度（修好）

触发器新增 `next_run_status`：`exact` / `approximate_weekday` / `unavailable_holiday`。
* 工作日：给估算日期，界面写「预计下次 …（按周一至周五估算，未计节假日／调休）」
* 法定节假日：`next_run` 为空，界面写「法定节假日由云端判定，本地无法计算下次执行时间」
* 每天/每周：仍是精确日期，界面不变

`next_run_approx` 保留（现在等价于 `status != "exact"`），老调用方不受影响。

### P1-4 设备分页（未解决，明确标注）

在反编译源码里**没有找到** `start_did` / `has_more` 之类游标字段的证据（递归搜 jadx，只命中 RecyclerView 的 `hasMore`）。
按任务要求「拿不到证据就不得靠调大 limit 冒充修复」，本轮**不改代码**，
文档 #1 标注「只请求一页（limit=200），>200 台会静默缺失，分页游标需真机验证」。

### P1-5 inline 动作参数（修好，真机兼容性待验证）

* `spec._Arg.to_dict()` 增加 `inline` / `unit`；`model.ActionControl` 保留**完整有序**的 `params`
  （piid/inline/label/format/value_range/value_list/unit），`in_refs` 只留真正的属性引用。
* `build_action_args(control, state, inputs)` 按 spec 顺序组装：引用参数读当前值（没有值就报错，不传 None），
  inline 参数取用户输入并校验（枚举/范围/类型）。
* `Provider.run_action` / `DemoProvider` / `CloudProvider` 增加向后兼容的 `inputs` 参数；`/api/action` 接受 `inputs`。
* 前端 `actionRow` 为 inline 参数生成控件（枚举→下拉、bool→复选框、数字→number 带 min/max），执行时按 piid 收集 `inputs`。
* `tests/test_action_inputs.py` 9 条合成 spec：无参/纯引用/纯 inline/混合顺序、缺失与越界与类型错**云端 0 次调用**、合法值参数顺序正确。
* **限制**：随包真实夹具里没有 inline 动作，真实型号兼容性标「需真机验证」；场景编辑器仍不支持参数动作，文档已写明。

### 工程

* 测试 505 → **524**；`ruff` 全绿；`selftest` ok；产物 0.7.31。

---
## 0.7.30 — 2026-11-21

第二轮外部复审的 4 条意见全部归零：#1 #2 在 0.7.29 已修，#3 本轮修，#4 是文档表述错误、已改正。

### 1. 登录脱敏测试的隔离（本轮）

复审指出 `TestLoginSecrets` 没有替换 `web_server._data_dir`：日志断言会读写真实数据目录、
而且结果受既有日志影响（不 hermetic）。已加 `setUp/tearDown` 换到 `TemporaryDirectory` 并还原。

### 2. 文档表述改正

我上一版报告写「24 条全部走真实 socket」——不准确。实际是：
`TestStaticEscape` 8 条 + `TestRequestOrigin` 13 条走真实 socket，`TestLoginSecrets` 6 条是单元级
（注入含哨兵的异常，其中 1 条连 `127.0.0.1:9` 走真实网络错误）。共 27 条，1 条跳过。

### 3. 复审确认的两件事（0.7.29）

* 畸形 Origin（`http://[::1`、`:bad`、`:99999`）现在返回 403，不再断连。
* 所有 POST（含空 body）都要求 `application/json`，`text/plain` 一律 415。

### 4. 新增 `docs/安全修复报告-0.7.30.md`

汇总两轮复审的 4 条意见与处理结果、测试构成、以及**仍未完成**的四项（浏览器 DNS rebinding、
启动令牌、`Sec-Fetch-Site` 缺失放行、首轮「中等问题」原文未拿到）。

### 结果

* 测试 505 条全绿（跳过 2）；`ruff` 全绿；`selftest` ok；`sanitize_check` 0 命中。
* 产物 0.7.30。

---
## 0.7.29 — 2026-11-21

按评审方 0.7.28 的探测脚本再修两处：畸形 Origin 会让连接被直接掐断、空 body 的 text/plain POST 会被放行。

### 1. 畸形 Origin 变成 403（原来直接断连）

`http://[::1`、`http://h:bad`、`http://h:99999` 三种输入会让 `urlsplit()` 或 `.port` 抛 `ValueError`，
异常从 `do_GET` 冒出去 → 连接被关闭，客户端只看到 `RemoteDisconnected`，**看起来像服务出错而不是被拒绝**。
现在把解析包在 `try/except ValueError` 里统一 403。

### 2. 所有 POST 都要求 `application/json`（含空 body）

原来 `Content-Length: 0` 时跳过类型检查，于是 `Content-Type: text/plain` + 空 body 的请求能通过。
`text/plain` 正是可以绕过 CORS 预检的类型，不能给它留口子。现在无论有没有 body，类型不对就 415。

> 影响：`curl -X POST http://127.0.0.1:PORT/api/messages/read` 这类命令需要加
> `-H 'Content-Type: application/json'`。网页 UI 本来就发 JSON，不受影响。

### 新增测试（3 条，来自评审方的探测脚本）

* `test_malformed_origin_is_rejected_not_dropped`：三种畸形 Origin 都必须 403。
* `test_empty_text_plain_post_is_rejected`：空 body + text/plain → 415。
* `test_duplicate_host_header_is_rejected`：两个 Host 头 → 403。

### 结果

* 评审方的 15 项探测现在全部符合预期（静态 5 项 400 且无泄露、合法静态 200、外域 Host 403、
  跨源写 403 且状态未变、畸形 Origin 403×3、空 text/plain POST 415、重复 Host 403、登录脱敏 2 项 True）。
* 测试 502 → **505**；`ruff` 全绿；`selftest` ok；产物 0.7.29。

---
## 0.7.28 — 2026-11-21

**按外部评审意见修复三项安全问题**（评审基于 0.7.26）。这一轮只做安全，没碰协议/场景/定时逻辑。

### 1. 静态目录逃逸（0.7.27 已修，本轮按评审意见加固）

评审要求比我的第一版更细，全部采纳：先 `unquote` 解码一次 → 统一 `\` 为 `/` → **在删前导斜杠之前**
拒绝 POSIX 绝对路径 / 盘符 / 盘符相对 / UNC / NUL → 最后 `realpath` + `commonpath` 确认在目录内
（跨盘符抛 `ValueError` 一律当拒绝）。用 `ntpath.splitdrive` 是为了在 POSIX 上跑测试也能识别 `C:` 与 `\\host\share`。

### 2. 回环服务的请求入口校验（新增）

`_request_allowed()` 在 `do_GET` / `do_POST` 第一行调用，先于路由、provider 与 body 读取：

* **Host**：必须唯一，必须是回环字面量（`127.0.0.1` / `::1` / `localhost`，用 `ipaddress.is_loopback` 判断，
  不做 DNS 解析），带端口时必须等于监听端口。挡 DNS rebinding。
* **Origin**：存在时只允许 `http`、hostname 与 Host 一致、端口与监听端口一致、无路径/查询/用户名；
  `null` 与多值一律 403。挡跨站写。
* **Sec-Fetch-Site**：只允许 `same-origin` / `none`。
* **POST body**：拒绝 `Transfer-Encoding`；`Content-Length` 必须是非负整数且 ≤ 1 MiB（超限在读 body 之前 413）；
  有 body 时必须 `application/json`（否则 415）；非法 JSON 或顶层不是对象 → 400（不再静默当空字典）。

### 3. 登录异常不再泄露凭据（新增）

* `auth.py` 里六处异常去掉 payload / URL / 描述：`QR response missing qr/lp`、`login response has no location`、
  `login response has no ssecurity`、`no serviceToken`、`QR request failed: code=…`、`QR poll failed: code=…`。
* 网络错误统一走 `_safe_reason()`：只输出 `connection refused` / `timeout` / `dns failure` / `connection reset`，
  **不带 URL**（URL 里可能有 ticket）。
* `LoginFlow._run()` 不再把 `str(exc)` 写进页面：只给异常类型 + 固定中文提示，日志也只记类型。
* `Credentials.__repr__` 去掉 serviceToken 前 8 位，只保留「是否已认证」。

### 测试（新增 `tests/test_security.py`，24 条）

全部走真实 socket（`http.client` 精确控制 Host / Origin / Content-Type）：

* 静态：盘符绝对路径、URL 编码盘符、`..`、`%2E%2E`、`..%5C`、UNC、目录外符号链接（无权限时 skip）、合法资源 200。
  每个攻击用例都断言「不是 200 且响应里没有 canary 内容」。
* 请求：外域 Host、错误端口、跨源 Origin、`Origin: null`、`Sec-Fetch-Site: cross-site`、`text/plain` 写、
  1 MiB+ 体（只发头不发体，验证在读之前就 413）、**攻击前后 provider 收藏状态未变**、
  同源写 200、无 Origin 的 CLI JSON POST 200。
* 登录：缺 location、缺 ssecurity、QR 缺字段、真实网络错误（连 127.0.0.1:9）不含 URL、
  `LoginFlow` 抛含全部哨兵的异常后，异常 / `status()` / `web.log` 里都找不到哨兵、`Credentials.__repr__` 无 token。

### 报告（评审要求的材料）

* 改的文件：`mihome_pc/web/server.py`、`mihome_pc/xiaomi/auth.py`、新增 `tests/test_security.py`。
* 测试：474 → **502**（新增 24 条安全 + 4 条静态），跳过 2 条（符号链接权限、既有的环境相关 skip）。
* 结果：`Ran 502 tests ... OK (skipped=2)`；`ruff check .` 全绿；`selftest` ok。
* **未做**：浏览器端到端 DNS rebinding 验证（需要可控 DNS + 真实浏览器，未做）；
  启动随机令牌（评审说可作为后续纵深防御，本轮未加）。

---
## 0.7.27 — 2026-11-21

**安全修复**：静态文件服务在 Windows 上可被读任意文件。

### 问题

`_static()` 只拒绝了 `..`，然后在 `os.path.join(STATIC_DIR, safe)` 拼路径。
但 Windows 上第二段带盘符会被当成绝对路径，`join` **直接丢掉**第一个参数：

```
>>> os.path.join(r'D:\\app\\static', 'C:/Windows/win.ini')
'C:/Windows/win.ini'
```

于是 `GET /static/C:/Windows/win.ini` 能读走本机任意文件。这个服务只绑 127.0.0.1、
没有鉴权，所以任何能起本机进程的东西（或浏览器里的 CSRF 式请求）都能利用。

### 修复

* 先算 `os.path.realpath`，再要求结果确实以 `STATIC_DIR` 的真实路径为前缀，否则 400。
  这一条同时挡住 `..`、盘符、符号链接和大小写花样，比逐个黑名单可靠。
* 额外拒绝含 `:` 与 `\x00` 的路径（Windows 备用数据流 / 截断）。

### 测试

`tests/test_web.py` 新增 `TestStaticFiles` 四条：正常资源 200、`..` → 400、
`C:/Windows/win.ini` → 400、反斜杠穿越 → 400。

> 这条是准备开源、自查「无鉴权 + 静态服务」时发现的，正好赶在外部评审意见回来之前修掉。

### 工程

* 测试 474 → **478**；`ruff` 全绿；产物 0.7.27。

---
## 0.7.26 — 2026-11-21

为开源发布做脱敏：把仓库里所有能指回作者账号/家庭/设备的信息清掉，并加了一个可复用的检查脚本。

### 新增：`tools/sanitize_check.py`

* 扫描 10 类可疑内容：家庭 id、uid、设备 did、BLE did、局域网 IP、MAC、长十六进制串、邮箱、
  Windows 用户路径，以及作者的家庭名。
* 对 `tests/` 与 `assets/` 放宽「数字像 did」「长十六进制」两类（测试夹具里是时间戳、错误码、AES 向量）；
  对不发布的本地脚本（如 `wol_timing_test.py`）直接跳过。
* 现在整仓 **0 suspicious line(s)**，可作为发版前的门禁。

### 清理清单

* `docs/reverse/01-网络与签名.md`：`home_id` / `owner_uid` → 占位符。
* `docs/03-现状与待办.md`：本机路径、电视 IP、NAS 地址 → 占位符。
* `CHANGELOG.md`：设备 did、BLE did、房间 id、序列号、IP、MAC、时间戳 → 占位符。
* `mihome_pc/web/server.py`：演示数据里误用了真实洗衣机 did 与一个内网 IP，改成 `demo-washer` / 空字符串。
* 源码注释里的真实 `roomid` → `<room_id>`。
* `tests/`：真实 uid / home id / 房间 id 换成假值。

### 仓库整理

* 新增 `.gitignore`：`data/`、`dist/`、`out/`、`reverse/`、`.toolchain/`、缓存，
  以及含真实设备信息的四个本地脚本和真机日志。
* README 截图全部换成**演示模式**重拍的（家庭名「演示家庭」，设备虚构），放在 `docs/images/`。
* 新增 `docs/发布到GitHub.md`：不发布什么、怎么脱敏、首次提交与发 Release 的命令。

### 工程

* 测试 474 条全绿；`ruff` 全绿；`sanitize_check` 0 命中；产物 0.7.26。

---
## 0.7.25 — 2026-11-21

最终门禁：把六条要求对应的检查全跑一遍，结果写进 `docs/阶段总结.md` 的「最终门禁」表。

### 门禁结果

* 单测 474 条 OK；`ruff check .` 全绿；`selftest` ok；`check_release.py` 六项全 ✓；`verify_deb.py` 通过。
* **绿色版解压实跑**（包内 python38、空数据目录、端口 18792）：`使用须知.txt` 在包里、版本正确、
  对这份打包产物跑只读巡检 **11/11 通过**——这是「产物本身可用」最直接的证据。
* deb 内含 `usr/share/doc/mihome-pc/{README.md,copyright}`。
* 安装版：已装 0.7.25；桌面 `米家 PC.lnk`、开始菜单 `米家 PC\{米家 PC,卸载 米家 PC}.lnk`。

### 门禁里唯一一次「失败」

我自己写的检查脚本去找 `Programs\MiHome PC.lnk`，结果 False——实际快捷方式在
`Programs\米家 PC\` 子目录下（应用 + 卸载两个）。产品没问题，是脚本找错位置，
已把这个细节写进阶段总结，避免后来人重复怀疑。

### 工程

* 测试 474 条仍全绿；产物 0.7.25。

---
## 0.7.24 — 2026-11-21

把「阶段总结」重写成逐条对照目标的证据清单——不是自我表扬，而是每条结论都能被复现。

### docs/阶段总结.md 重写

按用户的六条要求逐条给：**做到什么 / 证据在哪 / 还剩什么**。

* ①插件文案自动同步：当前缓存（洗衣机 339 条、电视 59 条）、启动日志行、语言选择规则。
* ②功能矩阵：21 行状态 + **做不到的三件事及其实测证据**（自动化新建 sc_id、房间排序、固件 OTA）。
* ③桌面化 + 兼容性：刚跑出来的证据——后端 25 个模块全是标准库；
  前端 `?.` 出现 0 次、箭头函数 0 次（`??` 只在注释里出现）。
* ④测试与真机：474 条单测、11 项只读巡检、四条可复现命令、产物清单与绿色版实跑结论。
* ⑤版本记录与托盘：40 个版本条目、托盘菜单三项（含退出）。
* ⑥分发包：zip 里 `使用须知.txt`/`mihome.vbs`/`install.ps1`、桌面快捷方式存在、deb 带 README。

### 这一轮的额外核查

* 实测确认「纯标准库」：列出 `mihome_pc/` 下所有 import 的顶层模块并核对，25 个全在标准库。
* 实测确认 zip 内容与托盘菜单，不再只凭记忆写文档。

### 工程

* 测试 474 条全绿；`ruff` 全绿；`check_release` 通过；产物 0.7.24。

---
## 0.7.23 — 2026-11-21

消息页多了类型筛选：全部 / 设备消息 / 场景通知，各带条数。

### 后端

* `message.normalise` 增加 `types` 汇总：`[{type, label, count}]`，按条数降序、同数按类型 id。
* 演示数据也改成走同一个 `normalise`——之前 DemoProvider 自己拼字典，
  所以这次加 `types` 时演示端直接漏了字段（测试当场抓到）。现在两边字段强制一致。
* 所有空返回统一成 `{messages: [], types: [], unread: 0}`。

### 界面

* 消息页顶部一排筛选标签：「全部 5」「设备消息 4」「场景通知 1」；只有一种类型时不显示这一行。
* 切到某个类型时，标题栏的「N 条最近消息」跟着变成该类型的条数；空态文案变成「这个类型下没有消息」。

### 真机验证

* 真账号：`types: 设备消息(4) / 场景通知(1)`，页面显示三个标签，点击可过滤；
  截图 `out/ui23-message-types.png`。

### 工程

* 测试 471 → **474**：`normalise` 的 types 汇总（字段与顺序、按条数排序）+ web 层字段存在性。
* 修了一处「两个地方各自拼同一个结构」的历史问题（DemoProvider.messages 改为复用 normalise）。
* `ruff` 全绿；`check_release` 通过；产物 0.7.23。

---
## 0.7.22 — 2026-11-21

「切回窗口时数据是旧的」这件事修掉了：聚焦/可见性变化会立刻同步一次，场景执行后也会回填首页状态。

### 改动

* `refreshOnce(did)`：把原来只给定时器用的刷新逻辑抽出来，定时轮询与聚焦共用；
  只有当前设备仍是被刷的那台才应用结果（避免切设备时把旧数据写回去）。
* 窗口 `focus` / `visibilitychange`（变为可见）时：
  在设备详情就刷当前设备，在首页就重新拉设备列表——不再等 5 秒轮询。
* 场景执行成功后 2.5 秒回填一次设备列表（在首页时顺带重绘），
  这样「执行观影模式 → 返回首页」看到的是新状态而不是执行前的。

### 验证

* 真账号：执行「正常模式」后返回首页，灯组状态与执行结果一致（之前要等一次轮询）。
* 新增 `/api/refresh` 契约测试两条：返回 `{state: {...}}`；未知设备 404。
  这个接口之前一直没测试覆盖，而它正是详情页轮询与聚焦刷新共用的入口。

### 工程

* 测试 469 → **471**：`/api/refresh` 两条契约测试。
* `ruff` 全绿；`check_release` 通过；产物 0.7.22。

---
## 0.7.21 — 2026-11-21

这一轮做的是「全量巡检 + 阶段总结」：写了一个只读巡检脚本，把主要接口挨个打一遍，
并把这一路做到什么、怎么验证的、明确不做什么，沉淀成一份阶段总结。

### 新增：`tools/regression.py`

* 11 项检查全部是 GET，**不改任何数据**，因此可以放心对着真实账号跑：
  status / meta / devices / scenes / messages / members / favorites / scene icons，
  再加上「挑一台能打开的设备的」detail / info / messages 三项。
* 设备挑选用「有控件且没 error」的那台——手环这类没 spec 的设备本来就会 404，不该算失败。
* 失败只报告不抛异常（服务器连不上也能出一张全 ✗ 的表）。

### 新增：`docs/阶段总结.md`

* 21 项矩阵的最终状态（15 ✅ / 3 ⚠️ / 3 ❌ + 房间排序判定不可行）、六步验证线、
  七个可重复命令、最近一次巡检输出、**明确的边界**（添加设备、自动化新建、房间排序、固件、推送、Win7），
  以及给 fork 用户的三条建议。README 目录里加了入口。

### 实测（真实账号）

```
$ python tools/regression.py --port 18780
  ✓ devices          15 台设备 / 6 个房间
  ✓ scenes           4 场景 / 2 自动化
  ✓ messages         5 条消息 / 1 未读
  ✓ device detail    13 个控件
  ✓ device info      xiaomi.health_pot.p1v2 / 1.0.9.0014
regression: 11 checks, 0 failed
```

顺带修掉一个工具自身的问题：最初取「设备列表第一台」做详情检查，
而列表第一台恰好是没 spec 的手环 → 404 被算作失败。现在挑真正可用的那台。

### 工程

* 测试 466 → **469**：巡检工具三条（检查项都有路径且唯一、连不上时报告而不是抛异常）。
* `ruff` 全绿；`check_release` 通过；产物 0.7.21。

---
## 0.7.20 — 2026-11-21

两张卡片的小改进：离线设备说清「上次什么时候有数据」，长名字能悬停看全。

### 离线卡片：最后上报时间

* 设备列表每项新增 `last_seen`（该设备所有属性里最新的上报时间戳，来自已有的 `state_seen` 缓存，
  不额外请求云端）；抽成 `_newest_seen()` 便于单测。
* 卡片副标题：离线时显示「房间 · 最后上报 3 小时前」；从没有过数据的（比如没连过的手环）显示「未上报过」。
* spec 拿不到的设备走的是降级分支，也补了 `last_seen: 0`，避免前端出现 `undefined`。

### 长名字

* 设备卡片、场景卡片、自动化行的名字都加了 `title`——CSS 省略号截断后，悬停能看到完整名字。

### 真机验证

* 真账号首页：小米手环7 Pro（离线且无历史）副标题是「未上报过」；
  电视/电压力锅（离线但有历史）走的是「最后上报 X 前」。
* 截图 `out/ui22-offline.png`。

### 工程

* 测试 463 → **466**：`_newest_seen` 三条（取最新、空/脏数据、列表里一定有这个字段）。
* `ruff` 全绿；产物 0.7.20。

---
## 0.7.19 — 2026-11-21

这一轮专门打磨「空手感和键盘」——fork 用户最容易在没设备/没场景时觉得这是个半成品。

### 空态说清楚原因

* 设备页的空态分三种：搜索无结果（提示换关键词）、这个房间没设备（提示可从设备详情搬过来）、
  账号里就没有设备（提示去手机 App 添加后会自动出现）。之前只有一句「没有设备」。
* 场景页：「还没有可执行场景，点右上角「新建场景」建一个」。
* 消息页：「还没有消息；设备完成任务、场景执行失败时会出现在这里」。
* 新增 `MIHOME_PC_DEMO_EMPTY=1`：演示数据全部清空，专门用来查看/验证空态。

### 键盘可用

* `/` 聚焦搜索框（不在输入框里时才生效）；
* `Esc` 依次关闭弹窗（登录/定时场景/状态/账号），没有弹窗时从设备详情返回列表，
  再按一次退出房间筛选；
* 搜索框里按 `Enter`：如果只剩一台匹配设备，直接打开它。

### 验证

* 用空模式起了个独立实例（18791）+ 全新数据目录，截了三张空态图：
  设备「还没有设备；在手机 App 里添加后，这里会自动出现」、场景「点右上角「新建场景」建一个」、
  消息空态。三张都在 `out/ui21-empty-*.png`。
* 键盘行为没有自动化（无头浏览器不方便模拟组合键），实现方式是绑定在 `document` 上的一处
  集中处理，`node --check` 通过；键盘逻辑刻意写成「先关弹窗，再退一层」，避免一次退太多。

### 工程

* 测试 460 → **463**：空模式三条（开/关/`0` 视为关），验证四类列表同时变空。
* `ruff` 全绿；产物 0.7.19。

---
## 0.7.18 — 2026-11-21

发版这件事本身自动化了：`python packaging/check_release.py` 一条命令检查
「版本号到处一致 + 三件产物 + 发布说明哈希对得上」，另外补了一份人工检查清单。

### 新增：check_release.py

* 检查六项：`mihome_pc.VERSION` 可读、README「当前版本」一致、CHANGELOG 顶部一致、
  `docs/04` 提到当前版本、三件产物齐全、发布说明里的 sha256 与产物实际哈希一致。
* 任何一项 ✗ 就退出码 1——CI 或本地都能直接拦。
* 哈希不一致这项专门防「打包重建了但忘了重新生成发布说明」这种低级错误。

### 新增：docs/发布检查清单.md

* 分三段：自动项（脚本覆盖的）、其他脚本（单测/ruff/selftest/verify_deb）、
  手动项（产物能独立跑、界面看一眼、真机验证并复原、托盘与快捷方式、使用须知）。
* 最后写了「什么情况不能发」，比如「只在演示数据上验过」不算、
  「改了云端数据没复原」不能发。

### 实测

```
$ python packaging/check_release.py
  ✓ 版本号：0.7.18
  ✓ README 版本 / CHANGELOG 顶部 / 功能矩阵 一致
  ✓ 三件产物：齐全
  ✓ sha256 一致：全部对得上
check_release: ok
```

### 工程

* 测试 452 → **460**：`tests/test_check_release.py` 8 条（四个解析器 + 干净树通过 +
  版本不一致/缺产物/哈希过期 三种失败）。
* `ruff` 全绿；产物 0.7.18。

---
## 0.7.17 — 2026-11-21

定时现在会告诉你「下次什么时候跑」；固件那行也标清楚了只读。

### 新增：下次执行时间

* `domain/timer.next_run(cron, after=None, weekdays_only=False)`：纯函数，
  按 7 段 cron 算出下一次触发时间（支持固定时分 + 星期列表 / 每天）。
* `scene._trigger` 顺带输出 `next_run`（ISO）与 `next_run_approx`；
  有「法定工作日」过滤时按周一到周五近似，并**在界面上直接写明**「未计节假日」——
  宁可标注不准的范围，也不假装精确。
* 自动化那一行右侧显示 `下次 11-23 08:45`（同一年不显示年份）；
  非定时自动化（比如传感器触发）不显示这一项。

### 固件那行

* 设备信息卡的「固件」加了悬停说明与一行小字：**版本只读、是否最新未知、升级请用手机 App**。
  避免用户以为这里能升级、或以为显示的就是最新版。

### 真机验证

* 账号里那条「八点四十五提醒」（08:45 法定工作日）：`next_run=2026-11-23T08:45:00`、
  `next_run_approx=true`——当天是周六，下一次确实是周一，界面显示「下次 11-23 08:45（按周一至周五，未计节假日）」。

### 工程

* 测试 444 → **452**：`test_timer` 新增 6 条（跨天、当天未过点、星期列表、工作日跳过周末、周日=0、非法输入）+
  `test_scene` 2 条（触发器带 next_run、普通每天任务不算近似）。
* `ruff` 全绿；产物 0.7.17。

---
## 0.7.16 — 2026-11-21

这一轮去确认「房间排序到底能不能做」，结论是**做不了**，顺手把结论与证据固化进文档。

### 排查过程（都是实测）

* `ctrroom`（改房间）与 `ctrhome`（改家庭）的请求体里都没有顺序/位置字段——
  它们只接受 `name/icon/background/did/add/delete`。
* 找到唯一像排序的接口：`GET /v2/home/get_union_order`（`get_room_order:true`）与
  `POST /v2/home/set_union_order {home_id, platform, type:"room"|"room_v2", order_data:[…], owner_uid}`。
* 两个硬障碍：
  1. 写接口要求把内层 JSON 包在 `data` 字符串里（不包 → `-8 data not valid`）；
     包了之后仍然 `-8 invalid homeid`——`home_id` 试过字符串、整数、数组三种写法都不行。
  2. `get_union_order` 返回的 `room_order` 里 id 是 `22100119xxxx`，
     而真实房间 id 是 `22100168xxxx–22100178xxxx`，**根本对不上**。
* 结论：房间顺序由服务端按创建时间决定，客户端没有可靠入口；这条不再尝试。

### 文档

* `docs/04` 第 11 行改成「房间排序做不了」，并在「已知不能做」里写清上面两条证据。
* 「仍然缺的」列表相应缩短：现在剩自动化新建、设备共享写操作、固件升级、真推送、
  家庭图标背景、按日期间隔。

### 工程

* 测试 444 条仍全绿；`ruff` 全绿；产物 0.7.16。

---
## 0.7.15 — 2026-11-21

收尾体检：`selftest --env` 现在会把「东西放在哪、能不能用」一条条列出来。

### 新增：环境自检

* `python -m mihome_pc selftest --env` 额外打印四行：
  数据目录（能否写入）、凭据（是否已登录 + 时间）、spec 缓存文件数、插件文案已缓存型号数。
  状态用 `✓ / ! / -` 三档区分 ok / 有问题 / 还没产生。
* 数据目录的检查是真的去写一个探针文件再删掉——不做「看起来存在就算好」的判断。

### 实际用途

* 用户报「看不到插件文案」「登录后一片空白」时，先让他跑这条命令，
  就能区分是没登录、没缓存、还是目录不可写。
* 顺带发现：本机跑自检时 `~/.mihome-pc` 被沙箱拦了写入（PowerShell 能写、python 不能），
  自检如实报了 `!`，没有假装 ok ——这就是它该干的事。

### 工程

* 测试 441 → **444**：`tests/test_selftest_env.py` 3 条（空目录全 off、有凭据/spec/插件时全 ok、一行一主题）。
* `ruff` 全绿；产物 0.7.15。

---
## 0.7.14 — 2026-11-21

场景卡片可以直接**拖动**排序了（原来的 ↑↓ 保留，键盘/小屏也能用）。

### 新增

* `POST /api/scene/reorder {scene_ids: [...]}`：整份顺序一次写回
  （`SetLightingManualSceneOrder` 的 second-page 列表，和 0.7.8 同一机制）。
  不认识的 id 直接拒绝并说明是哪几个；顺序没变就跳过请求。
* 卡片 `draggable`：拖动时原卡片半透明、目标卡片描边高亮，松手后提交并刷新。
  页面副标题加了一句「拖动卡片可调整顺序」，免得只有鼠标划过才发现。

### 真机验证

* 把 4 个场景整份倒序提交 → 列表顺序真的反过来（`正常模式 / 出门 / 睡觉 / 观影模式`）；
  再提交原顺序 → 复原。传一个不存在的 id → `不认识这些场景：x1`。
* 说明一句：**拖拽手势本身没有自动化测试**（无头浏览器做不了真实拖放），
  验证到的是「落点计算逻辑 + 提交接口 + 视觉状态」三件事；接口层有 4 条单测。

### 工程

* 测试 437 → **441**：provider 3 条（写回整份顺序、拒绝未知 id、顺序未变不发请求）+ web 1 条。
* `ruff` 全绿；产物 0.7.14。

---
## 0.7.13 — 2026-11-21

设备可以收藏置顶了：卡片右上角一颗星，点一下钉到第一排。偏好存在本机，不动云端。

### 为什么做本地的

* 米家的「收藏」是账号级数据，但它的写接口没在 APK 里一眼看出来；
  而「我想把常看的几台钉在最上面」纯粹是桌面端的浏览习惯，
  存本地 `~/.mihome-pc/favorites.json` 既够用，又不会污染账号。

### 新增

* `GET /api/favorites`、`POST /api/favorites/toggle {did}`；数据落在数据目录的
  `favorites.json`（`{"dids": ["…"]}`），换机器重装自然消失。
* `/api/devices` 现在会：① 把置顶的排在最前（组内保持原顺序）；② 每台设备带 `pinned` 标记。
* 卡片右上角星标：悬停变淡入，已置顶是实心星星；点它即时生效（列表重新排序，页面不跳）。

### 真机验证

* 把洗衣机置顶：`/api/devices` 第一条变成「洗衣机」，`pinned=true`，其余仍在原顺序；
  文件里写着 `{"dids": ["<did>"]}`。取消后列表与文件都回到原样。

### 工程

* 测试 428 → **437**：`tests/test_favorites.py` 8 条（toggle/prune/order、持久化往返、列表排序与标记）+ web 1 条。
* 顺手修了两个老问题：
  1. `DemoProvider.devices()` 直接读 `_room_of`，但那个映射只有 `rooms()` 调用后才有 → 独立构造时 AttributeError；
  2. web 测试原来会往**真实的** `~/.mihome-pc` 写文件（这次因为写 favorites 才暴露），
     现在 `WebTestCase` 统一把 `_data_dir` 指向临时目录。
* `ruff` 全绿；产物 0.7.13。

---
## 0.7.12 — 2026-11-21

场景可以挑图标了（31 个官方图标），新建和编辑时都能选。

### 逆向

* `POST /appgateway/miot/appsceneservice/AppSceneService/GetIconInfoList`（空 body，
  旧版的 `data={}` 也直接转成 JSON body）→ `[{icon_id, icon_url}]`，31 条，
  图片在 `cnbj1.fds.api.xiaomi.com/scenetemplate/user_scene/…`。
* 场景记录里的字段就是 `icon_url`，改它 = 普通 `Edit`（带 `scene_id`）。
* 一个小坑：**把 `icon_url` 设为空字符串并不会清空图标**，服务端会保留原来的——
  所以界面上只提供「换图标」，不提供「去掉图标」。

### 新增

* `GET /api/scene/icons`（目录，缓存 10 分钟）与 `POST /api/scene/icon {scene_id, icon_url}`。
* 新建/编辑场景的弹窗里多了一行「图标」：31 个缩略图可点，选中有高亮；
  `icon_url` 跟着 `create` / `update` 一起提交，不用额外请求。

### 真机验证

* 建一个临时场景（默认无图标）→ 设成 `c_run.png` → 列表里 `icon` 立刻变成该 URL；
* 再设成空串 → 仍然是 `c_run.png`（确认「清空」这条路走不通，已写进文档）；删除临时场景收尾。

### 工程

* 测试 423 → **428**：provider 4 条（目录过滤与缓存、`set_scene_icon`、`update_scene` 带图标、`create_scene` 带图标）+ web 1 条。
* `ruff` 全绿；产物 0.7.12。

---
## 0.7.11 — 2026-11-21

家庭共享从「完全没碰」变成「能看见谁有权限」：账号页多了一块只读的「家庭共享」。

### 逆向

* `POST /v2/homeroom/member_list {home_id}`（`home_id` 传字符串），返回两条平行数组：
  `list`（权限：`uid/permit_level/inviter/invite_nick/invite_time/status/note`）与
  `userdetail`（昵称与头像 `uid/nick_name/icon`）。
* 角色判定：`uid == 家庭 owner` → 拥有者；`permit_level ≥ 10` → 管理员；否则成员。

### 新增

* `GET /api/members` → 合并两条数组后的成员列表（按权限倒序）；`domain/member.py` 纯函数可离线测。
* 账号与同步弹窗底部「家庭共享」：昵称（悬停显示全名）+ 角色 + 加入日期，并注明
  「只读；邀请与移除请用手机 App」。
* 顺带补了 `?view=account` 深链接（截图与文档引用方便）。

### 界面细节

* 加入时间用**绝对日期**（2019-05-29）而不是相对时间——否则会显示成「2733 天前」。
* 成员名字不再被 64px 的键列截断（`member-row` 单独放宽）。

### 真机验证

* 账号（只有自己）：`members: 1`，`布利啾啾迪不理多 | 拥有者 | permit=10`，
  账号页显示 `拥有者 · 2019-05-29 加入`。

### 工程

* 测试 415 → **423**：`tests/test_member.py` 7 条（合并昵称、角色判定、缺 detail 回退、空数据、provider 2 条）+ web 1 条。
* `ruff` 全绿；产物 0.7.11。

---
## 0.7.10 — 2026-11-21

发布物料这一块补齐：功能矩阵按代码重新核了一遍，并新增自动生成的发布说明（带 sha256）。

### 功能矩阵重写（docs/04）

* 之前那张表停在 0.6.0，光定时/场景两行就和实际不符（定时早就支持多动作、场景早就能排序）。
  这次逐行对着代码与实测证据重写，21 行、顺序也理顺了。
* 每行都写清「还差什么」，并把**查过的死路**写进去：
  `sc_id` 不在 miot-spec、不在插件包、不在 `GetSceneList`（三处都验证过），
  这样下一个接手的人不用重复踩。
* 「按收益排序的路线」那段也是旧的（还写着定时/消息接口未定位），改成「仍然缺的（按价值排序）」。

### 新增：发布说明生成器

* `packaging/release_notes.py` 扫 `dist/` 里本版本的三个产物，输出
  `dist/发布说明-<版本>.md`：文件表（大小 + sha256）、Windows 绿色版/安装版与 Linux 的安装步骤、
  以及「本版本没做的事」。
* 放在 `dist/` 而不是打进包里：sha256 要等产物生成后才能算，
  顺序是 build → release_notes.py。
* 顺手修了自己写的一个 bug：Linux 那两行的 `%s` 没套参数，生成出来是字面量（测试里加了断言防复发）。

### 工程

* 测试 408 → **415**：`tests/test_release_notes.py` 7 条（文件筛选、哈希长度、占位符、限制清单、空产物）。
* `ruff` 全绿；产物 0.7.10。

---
## 0.7.9 — 2026-11-21

两件事：**验证了打包出来的绿色版真的能独立跑**，以及给未读数加了轮询。

### 打包产物端到端验证（第一次做）

* 把 `mihome-pc-0.7.9-portable-win64.zip` 解压到临时目录，用包里自带的
  `python38\python.exe` 起 `serve --port 18790`，数据目录也指到临时目录（等于全新用户）：
  `status: demo=True logged_in=False`、演示设备 6 台、首页 200（7954 字节）、包内版本号 0.7.9。
* 也就是说：**不依赖开发机、不依赖系统 Python**，解压即用这条路径是通的。

### 新增：未读数轮询

* `GET /api/messages/unread` → `{unread}`，走 `messages()` 的 20 秒缓存，
  所以轮询不会每次都打云端（有测试断言只请求一次）。
* 前端每 45 秒问一次，页面不可见时跳过，窗口重新获得焦点时立刻问一次；
  如果正停在消息页，顺带刷新列表。
* README 里把「消息推送」那条限制改成「轮询，不是实时推送」，不夸张。

### 工程

* 测试 406 → **408**：provider 1 条（轮询复用缓存）+ web 1 条。
* `ruff` 全绿；产物 0.7.9。

---
## 0.7.8 — 2026-11-21

场景可以调顺序了（↑↓，顺序存在服务端，手机上也一致）。顺带把插件包里有没有 spec-scene 表这件事查完。

### 逆向：场景顺序有两个接口

* 读：`POST /AppSceneService/GetLightingManualSceneOrder {home_id, owner_uid}`，
  返回**按房间分组**的顺序：`{"-1": {home_page_display_ids:[...],
  second_page_display_ids:[...]}, "<roomId>": {...}}`，`-1` 是「全部房间」。
* 写：`POST /AppSceneService/SetLightingManualSceneOrder`，
  `{home_id, room_id, owner_uid, home_page_display_ids|second_page_display_ids}`。
  **`home_page_display_ids` 有配额**：塞满 4 条以上会回 `-6 save lighting manual scene order err`；
  `second_page_display_ids` 不限长度。所以整份顺序写 second page。
* 关键发现：**云端 `manual_scene_info_list` 的顺序不会跟着变**，它是插入顺序。
  所以顺序由客户端读取 display order 后自行应用——我们也是这么做的（`_apply_scene_order`）。
* 证据：`_m_j/bw6.java:26-60`（两个请求体）。

### 新增

* `GET /api/scenes` 按保存的顺序排序（读 `GetLightingManualSceneOrder`，失败就退回云端顺序）；
  `POST /api/scene/move {scene_id, direction: up|down}` 写回整份顺序。
* 场景卡片底部加 `↑ ↓`（还有原来的「编辑」），点一下就把这条往前/后挪一位。

### 插件包排查（sc_id 线索，仍无收获）

* 洗衣机插件包 `package.zip`：593 个条目，`scene|trigger|automation` 相关为 0——
  里面是 dex + 资源，没有标准场景条目表。`sc_id` 这条线到此为止，
  自动化新建继续标为「受限于服务端校验」。

### 真机验证

* `观影模式` ↓ → 顺序变成 `睡觉 / 观影模式 / 出门 / 正常模式`；再 ↑ → 复原。
  （浏览器刷新后仍是调整后的顺序，因为读的是服务端保存的 display order。）

### 工程

* 测试 402 → **406**：provider 3 条（按保存顺序排序、下移写回整份顺序且走 second page、到边界不发请求）+ web 1 条。
* 顺手修了场景卡片排版：四个按钮挤在标题行会把名字压成「观…」，改成标题行只放「执行」，
  排序与编辑挪到卡片底部一行。
* `ruff` 全绿；产物 0.7.8。

---
## 0.7.7 — 2026-11-21

这一轮去挖「新建自动化」需要的 spec-scene 表，没挖到；于是把 README 重写了一遍，
把现在能做什么、不能做什么、怎么验证的都写清楚——fork 用户拿到仓库应该能直接上手。

### 逆向结论：sc_id 不在 GetSceneList 里

* 找到 `RemoteSpecSceneApi.getSpecSceneList` 对应的接口：
  `POST /appgateway/miot/appsceneservice/AppSceneService/GetSceneList`，
  参数 `{home_id, did, owner_uid, get_type, app_version:19}`（`_m_j/mrc.java:58-75`）。
* 实测：它返回的是**这台设备已经建过的场景**，不是可选的触发目录——
  16 台设备里只有小爱音箱返回了 1 条（就是我们那条定时提醒）。
* 所以触发用的 `sc_id`（定时是固定的 12，传感器有人是 41899）来自服务端的
  标准场景条目表，miot-spec JSON 里也没有。要做自动化新建，得先找到这张表
  （可能在插件资源或另一个未发现的接口）。矩阵里保持「受限于服务端校验」。

### 新增：README 重写

* 顶部就是能力表（✅/⚠️/—），四张最新截图，下载安装（绿色版 / 安装版 / Linux），
  命令行、深链接、**已知限制**（自动化新建、不做添加设备、无推送、Win7 未实测）、
  架构分层与开发流程。
* 明确写了「真机验证时对真实设备的改动都会复原」——这是这些版本的实测纪律。

### 工程

* 测试 402 条仍然全绿；`ruff check .` 全绿；产物 0.7.7（README 会打进 deb 与 tar.gz）。

---
## 0.7.6 — 2026-11-21

两处改名：**设备**和**家庭**。名字是 App 里最常动的字段，之前只能看不能改。

### 逆向

* 设备改名：`POST /device/rename`，body `{app_version:1, newName, did, is_group:false}`。
  证据：`device/impl/OooO0O0.java:744-773`。
* 家庭改名：`POST /homeroom/ctrhome`，body `{id, type:"set", home_owner, name, icon,
  smart_room_background, smart_scene_background, app_ver:11, ...}`。
  证据：`_m_j/ju6.java:599-627`。
* **关键**：ctrhome 是整条家庭记录覆盖，`icon`/背景必须原样带回，否则会被重置成默认值。

### 新增

* `POST /api/device/rename {did, name}`（≤30 字）、`POST /api/home/rename {name}`。
* 设备信息卡第一行「名称 + 改名」；侧栏家庭名点一下就改（悬停有提示）。

### 修掉的缓存 bug

* 改完设备名后 `/api/devices` 一直回旧名字：`DeviceView` 是在 `_views` 里缓存的对象，
  构造时把 name 拷了进去，只失效设备列表缓存不够——必须把该设备的 view 也丢掉（`_views.pop`）。
  验证：改名前 `灯泡` → 改名后 `/api/devices` 立刻返回 `测试灯泡`。

### 真机验证

* 灯泡：`灯泡` → `测试灯泡` → 复原 `灯泡`（每一步 raw 列表与 `/api/devices` 都是新名字）；
* 家庭：`布利啾啾迪不理多的家` → `测试家庭` → 复原，**icon 与背景字段全程没变**（`style_1_favorites`）。

### 工程

* 测试 396 → **402**：provider 4 条（改名透传/空名拒绝/失败上报/家庭改名带回 icon）+ web 2 条。
* 记一笔 lint 上的坑：`ruff check mihome_pc tests ...` 这种显式路径会**绕过 pyproject 的 exclude**，
  把 `tools/` 里的旧脚本一起算进来（报了几百条）；项目级校验请用 `ruff check .`。
* 产物 0.7.6。

---
## 0.7.5 — 2026-11-21

设备详情页加了「消息记录」，同时把「新建自动化」卡在哪里查清楚了（结论：卡在服务端的触发校验）。

### 新增：每台设备的历史消息

* `GET /api/messages/device?did=&limit=`：走 `/v2/message/v2/list`，body `{did, type:6,
  timestamp, limit, force_read:false}`。
* **坑**：`timestamp` 传「现在」会返回空数组，传 **0** 才给最新的那批（实测 2026-11-21）。
  文档里写清楚，免得以后又踩。
* 设备详情页右侧多一张「消息记录」卡：最近 5 条标题 + 相对时间；没有历史消息就不显示这张卡。

### 自动化：查清了但没做成

* 现有自动化的触发记录长这样：`src:"device", key:"prop.2.1078", value_json:1, sc_id:41899,
  extra_json:{device_name, did, model}`。
* 试过两种做法，**都被服务端挡下**（`-998 CheckTriggerConditionSrcKeyExtraValueValueTypeDuration`）：
  1. 拿灯泡的属性手拼一个 `prop.2.1` 触发；
  2. 把账号里那条真实自动化**原样复制**（触发逐字段照抄）再改名字——连这个也被拒。
* 原因判断：校验要求触发里的 `sc_id` 仍然对应当前设备的 spec-scene 表，而那条自动化引用的是
  BLE 传感器 `<ble-did>`（已不在设备列表里），所以复制出来也无法通过校验。
* 因此 `POST /api/automation/clone` 只保证「结构正确」（用桩测试覆盖），真机上要等有在线设备的设备触发示例才能验证；
  矩阵里如实标为「受限于服务端触发校验」，没有吹成已完成。

### 真机验证

* 洗衣机详情页：出现 5 条「洗衣完成」「洗衣液缺少」记录（1 天前 / 18 小时前…）；
  灯泡详情页：`messages: []`，卡片不显示。

### 工程

* 测试 389 → **396**：设备消息 provider 2 条、web 1 条、clone 的 domain 3 条 + provider 1 条。
* `ruff` 全绿；产物 0.7.5。

---
## 0.7.4 — 2026-11-21

场景能改了：卡片上多「编辑」，改名、增删动作都在原地保存（scene_id 不变）。

### 做法

* `POST /api/scene/update {scene_id, name?, actions?}`：`GetSceneInfo` 拿原记录 →
  只替换 name / scene_action.actions，其余字段（触发、条件、timespan 等）原样带回 →「带 scene_id 的 Edit」。
* 这正好是 0.6.4「改定时」的同一套机制，只是这次改的是动作而不是时间。
* `/api/scenes` 里每条动作补了 `kind/siid/piid/aiid/value/inputs`，弹窗可以直接回填成草稿列表。

### 界面

* 场景卡片：「执行」旁边多了「编辑」（胶囊按钮）。点开同一个弹窗，切到编辑模式：
  标题变「编辑场景」、名字预填、动作列表按原场景回填、按钮变「保存」，时间/重复行隐藏。
* 编辑模式里仍可「＋ 再加一个动作」或点 × 删动作。

### 真机验证

* 建「测试编辑」（灯泡开）→ 编辑成「测试编辑后」+ 动作改成灯泡关 → **scene_id 不变**、列表里名字与动作都变了；
* 先把灯打开（`2.1=true`）→ 执行该场景 → 灯变 **`2.1=false`**，说明改后的动作真的生效；
* 删除 → 场景回到原来的 4 个。

### 工程

* 测试 383 → **389**：`build_update_payload` 4 条（保留 id/只替换给定字段/拒绝空名/不改原记录）+ provider 1 条 + web 1 条。
* `ruff` 全绿；产物 0.7.4。

---
## 0.7.3 — 2026-11-21

场景和定时都能一次带多个动作了：「观影模式」那种连做 4 台设备的玩法现在能自己搭。

### 改动

* `POST /api/scene/create`、`POST /api/timer/create` 都接受 `actions: [{did, kind, siid,
  piid|aiid, value|inputs}, ...]`；单设备的旧字段写法仍然有效（内部会包成一条）。
* 动作记录按顺序写 `order` 与 `id`（1、2、3…），和 App 产出的记录一致。
* 弹窗里多了「＋ 再加一个动作」：把当前「设备 + 动作 + 取值」存进草稿列表（带序号和 ×），
  最后一起提交；草稿为空时就用当前选择当单动作（老流程不变）。

### 真机验证

* 建一个两动作场景（灯泡先关、再开）→ 列表显示 `actions = 2`，两条都是 `set_properties`；
* 先把灯关掉（`2.1=false`）→ 执行场景 → 灯变成 **`2.1=true`**，证明第二条动作确实在第一条之后执行；
* 删除 → 场景回到原来的 4 个。

### 工程

* 把「找设备 + 找动作模板 + 组装」抽成 `_build_actions(specs)`，场景/定时共用；
  请求体解析抽成 `_parse_actions(body)`（纯函数，可单测）。
* 测试 377 → **383**：provider 2 条（两动作的 order/id/取值、含未知设备时整体失败）+ 解析器 4 条。
* `ruff` 全绿；产物 0.7.3。

---
## 0.7.2 — 2026-11-21

场景能自己建了：选设备 → 选动作 → 命名，就多一个「点一下执行」的手动场景。

### 逆向：手动场景与自动化的差别只在触发源

* 手动场景的记录形状和定时几乎一样，**区别是 `scene_trigger.triggers[0]`**：
  `{src:"user", key:"user.click", sc_id:1, value_json:null}`（点击执行）。
* 所以创建逻辑复用 0.7.x 的模板机制：拿一条现有场景做骨架（优先手动场景），
  把 trigger 换成 user.click，action 换成本次要执行的那个动作，去掉 `scene_id` 就是新建。
* 证据：「观影模式」的 `GetSceneInfo`（src=user / key=user.click / sc_id=1）。

### 新增

* `POST /api/scene/create {name, did, kind, siid, piid|aiid, value|inputs}`。
* 场景页工具栏多「新建场景」：同一个弹窗切到场景模式（隐藏时间/重复，提示「点一下就能执行」）。
* `?newscene=1` 可直接打开该弹窗（截图/书签用）。

### 真机验证

* 建「测试一键关灯」（灯泡 2.1=false）→ 出现在场景页（设备=灯泡）→
  执行前 `2.1=true`，执行后 `2.1=false`（真关灯）→ 恢复 `2.1=true` → 删除 → 场景回到原来的 4 个。

### 工程

* 测试 369 → **377**：`test_automation` 5 条（user.click 触发、替换定时触发、剥只读字段、触发是副本、空动作报错）+
  provider 2 条（建场景、设备不存在）+ web 2 条。
* 顺手把弹窗重构成 `openActionDialog(mode)`，定时/场景共用同一套「设备→动作→取值」选择器。
* `ruff` 全绿；产物 0.7.2。

---
## 0.7.1 — 2026-11-21

消息页补上两个动作：**全部标为已读**、**单条删除**。矩阵里「消息/告警」这一行收口。

### 逆向：已读与删除的请求体

* 已读：`POST /v2/message/v2/batch_read_msg`，`data = {homes: [{dids:[did...], home_id, home_owner}]}` ——
  **按设备分组**，不是按消息 id；没有 did 的消息（比如纯场景通知）没法这样标记，会保持未读。
  之前的猜测 `[{home_id, home_owner}]` 少了 `dids`，服务端直接回 `false`。
* 删除：`POST /v2/message/v2/delete`，`data = {msg_ids:[<long>...], type:<int>, did:"<did>"}`，返回 `true`。
* 证据：`MessageCenterV2Activity.java:235-260`（homes 元素形状）、`_m_j/bc1.java:73-94`（delete）。

### 新增

* `POST /api/messages/read`：把当前列表里所有带 did 的消息标为已读（返回 `marked` 台设备数）。
* `POST /api/messages/delete {msg_id}`：单条删除，会自动带上这条消息的 `type` 与 `did`。
* 消息页右上角「全部标为已读」；每行悬停出现「删除」（带确认框）。

### 真机验证

* 删除掉那条**我们自己测场景时产生的**「正常模式 03:04 执行失败」通知（5 → 4 条），顺手清了自己造的垃圾。
* 全部标为已读：`{ok, marked: 4}` → 未读数 4 → 0，四条都变 `new=False`，侧栏「消息 0」。

### 工程

* 测试 364 → **369**：provider 4 条（按设备标记、无设备时拒绝、删除带 type/did、未知 id）+ web 2 条。
* 修了一个顺序 bug：`mark_messages_read` 里先算 owner 后调 `_home_id()`，导致 owner 是 0——
  `_home_uid` 是 `_home_id()` 顺手填的，必须把它放前面。
* `ruff` 全绿；产物 0.7.1。

---
## 0.7.0 — 2026-11-21

房间这一行补齐：能新建、重命名、删除，也能把设备搬进别的房间。

### 逆向：房间三件套的请求体

* 新建：`POST /homeroom/addroom`，`data = {type:"addroom", name, home_owner, parent_id:<家庭 id>, background, did:[...]}`，
  返回 `{roomid: "<room_id>", did: []}` —— **键名是 `roomid` 不是 `id`**（先猜 `id` 导致拿到空 id，白建了一个房间）。
* 删除：`POST /homeroom/batchdelroom`，`data = {ids:[roomId...], home_owner}`，返回 `{affect: 1}`。
* 搬设备：`POST /homeroom/batch_device_to_room`，`data = {id:<房间 id>, home_owner, dids:[...]}`，返回字面量 `ok`。
* 证据：`com/xiaomi/smarthome/homeroom/OooOo00.java:624-653`（addroom）、`:750-780`（move）、
  `PluginService.java:1089-1105`（batchdelroom）。

### 新增

* `POST /api/room/add {name}`、`/api/room/delete {room_id}`、`/api/room/move {did, room_id}`。
* 房间行末尾三个入口：**＋ 新建房间**、**重命名**（选中房间时可用）、**删除房间**（选中房间时出现，
  会提示「里面还有 N 台设备，删掉后它们会变成未分配」）。
* 设备信息卡里多了「移到」下拉，选一个房间就把这台设备搬过去。

### 真机验证

* 新建「测试房间PC」→ 得到 roomid `<room_id>` → 把灯泡搬进去（客厅 6→5、测试房间PC 0→1，
  设备详情里房间变成「测试房间PC」）→ 搬回客厅（6）→ 删除房间（`affect: 1`）→ 房间列表回到原来的 6 个。
* 中途一次失败的尝试留下了一个空房间，已用 `batchdelroom` 删掉，账号没有残留。

### 工程

* 测试 359 → **364**：provider 4 条（addroom 取 roomid、空名不发请求、删除、搬家）+ web 3 条。
* `ruff` 全绿；产物 0.7.0。

---
## 0.6.9 — 2026-11-21

房间可以重命名了（`/homeroom/ctrroom`），顺带把房间相关的写接口摸清了。

### 逆向：房间写接口

* `POST /homeroom/ctrroom`，body `data = {type:"set", home_owner, id, name, icon, background,
  app_ver:11, add:[did...], delete:[did...]}`：**带 `id` 是改**（改名/换图标/搬设备），
  返回 `{affect, add, delete}`；`id` 不合法时回 `-8 id illegal`。
* 同一族还有 `/homeroom/addroom`、`/homeroom/batchdelroom`、`/homeroom/batch_device_to_room`、
  `/homeroom/bind_device_to_room`——新建/删除房间和搬设备都在这里，下一轮可直接用。
* 证据：`_m_j/ju6.java:663-711`（ctrroom 请求体）、`_m_j/pg1.java:113`（addroom）。

### 新增：房间重命名

* `POST /api/room/rename {room_id, name}`：名字去空白、限 20 字，调 `ctrroom` 后让房间缓存失效。
* 首页房间行末尾多了「重命名」按钮：**先点一个具体房间**再点它（选「全部」时是灰的），
  弹输入框，成功后筛选自动切到新名字并刷新列表。
* 深链接 `/?room=客厅` 可以直接筛某个房间（截图/书签友好）。

### 真机验证

* 办公室 →「测试房间临」→ 列表里立刻变成新名字 → 再改回「办公室」，房间顺序与名字都复原。
* 同名重命名（无操作）也返回成功，说明「先重命名后比对」这类交互不会误报失败。

### 工程

* 测试 352 → **359**：provider 4 条（owner/名字透传、空名不发请求、超长拒绝、失败上报）+ web 3 条。
* 修了一个自己写错的位置：路由第一次插进了 `do_GET`（POST 直接 404），已移到 `do_POST` 并验证。
* `ruff` 全绿；产物 0.6.9。

---
## 0.6.8 — 2026-11-21

设备信息卡上线：详情页右侧直接看到型号/固件/序列号/网络/插件文案/最后上报。

### 新增：`GET /api/device/info?did=`

* 从设备记录取 name/model/urn/localip/mac/token，从 `1.1-1.4` 属性取厂商/型号/序列号/固件，
  房间来自 `gethome_merged`，插件文案条数来自 `data/plugins/<型号>/texts.json`，
  最后上报时间用已经轮询到的 `state_seen`（刻意不在信息卡里再打一轮请求）。
* token 只显示前 6 位 + 长度，避免把设备密钥整段印在界面上。
* 属性读取失败时降级：其余字段照常显示（信息卡不该因为一台设备不回话就整块消失）。

### 界面

* 详情页右侧新增「设备信息」卡（在故障码对照下面）：房间/型号/厂商/固件/序列号/IP/MAC/
  token/插件文案/最后上报，最后一列 did 带「复制」按钮。
* 侧栏的场景数与消息未读数改为**启动时预取**，不点进去也准确（0.6.7 只在本页渲染时更新）。

### 真机验证

* 洗衣机：房间=阳台、厂商=minij、固件=2.3.0.0451、序列号=<sn>、IP=<ip>、
  MAC=<mac>、token 打码、插件文案 339 条、最后上报 2 小时前。

### 工程

* 测试 344 → **352**：`tests/test_device_info.py` 5 条（字段映射/未知属性忽略/token 打码/属性失败降级/未知设备）+ web 3 条。
* 顺手修了两个自己引入的问题：详情页 `detailSubtitle` 被误替换成 `relativeTime`（会 ReferenceError）、
  演示 provider 没有 `_room_of` 导致信息卡 500。
* `ruff` 全绿；产物 0.6.8。

---
## 0.6.7 — 2026-11-21

消息中心上线：侧栏「消息 N」+ 消息页，显示设备消息与场景通知，未读有标记。

### 逆向：消息接口

* 列表用 `POST /v2/message/v2/typelist`，body `{timestamp(ms), limit}`，返回 `{"messages": [...]}`；
  **它给的是「每个类型的最新一条」**，不是完整历史——完整历史走 `/v2/message/v2/list`，那个必须带 `did`。
* `POST /v2/message/v2/check_new_msg`，body `{begin_at: unix 秒}`，返回布尔（是否有新消息）。
* 实测类型：`6` = 设备消息（烹饪完成/洗衣完成/滤芯过期）、`13` = 场景通知（执行失败）；
  未知类型保留数字，不瞎猜标签。
* 已读用 `POST /v2/message/v2/batch_read_msg`，body `{homes: [...]}` —— 元素形状未知，
  试了 `[{home_id, home_owner}]` 返回 `false`，本轮先不做已读/删除，如实记在矩阵里。

### 新增：消息页

* `GET /api/messages`（`?refresh=1` 绕缓存，20 秒 TTL）：标题、类型标签、设备名、相对时间、未读点。
* 侧栏「消息」显示未读数；启动时预取场景数与未读数，不用点进去也准。
* 正文与设备名重复时不再重复显示（这两类消息的 `content` 常常就是设备名）。

### 真机验证

* 账号 5 条消息全部正确解析：场景通知 1 条（「正常模式 03:04 执行失败」——正是我们之前测场景时的失败记录）、
  设备消息 4 条（压力锅/洗衣机/洗碗机/饮水机滤芯），设备名与时间都正确，未读数 5。

### 工程

* 测试 333 → **344**：`tests/test_message.py` 8 条（排序/类型标签/设备解析/未读/脏数据）+ provider 3 条（缓存/强制刷新/失败降级）+ web 2 条。
* `ruff` 全绿；产物 0.6.7。

---
## 0.6.6 — 2026-11-21

定时支持重复方式：每天 / 法定工作日 / 法定节假日 / 每周（选周几）。并修掉一个会让「每周」偷偷变成「每天」的隐患。

### 逆向：cron 的星期位与过滤字段

* 7 段 cron：`秒 分 时 日 月 周 年`，星期用**逗号列表**，`0=周日 … 6=周六`。
  证据：App 的位掩码快捷值 `62`(周一..周五)、`65`(周日+周六)、`127`(每天)，
  `CorntabUtils$CorntabParam.java:88-93`；生成逻辑在 `_m_j/iue.java:32-73`。
* 「法定工作日/节假日」不是 cron 表达，而是 `extra_json.filter`：`cn_workday` / `cn_freeday`
  （`TimeWindowSetting.java:259`）。
* 服务端**不做规范化**：原样存回我们给的 cron 与 name，所以拼错不会被纠正——只能自己保证对。

### 修复：改时间会把「每周」变成「每天」

* 原来的 `build_payload` 用 `cron_for(hour, minute)` 重写整个 cron，星期位被抹成 `*`：
  给一条「周一、周三」的定时改时间，它会悄悄变成每天。
* 新增 `timer.retime(cron, hour, minute)`：只替换第 1、2 段（分、时），保留第 4-7 段（日/月/周/年）。
  已验证：`0 30 7 * * 1,3 *` + 08:15 → `0 15 8 * * 1,3 *`。

### 新增：重复方式

* `POST /api/timer/create` 增加 `repeat`（`daily`/`workday`/`holiday`/`weekly`）与 `days`（0=周日）；
  `weekly` 不给 `days` 直接 400，未知 `repeat` 也 400。
* 文案由 `timer.label_for` 生成：`07:30 周一、周三`、`07:30 法定工作日`、`07:30 每天`。
* 界面上多了「重复」下拉与「周几」一排可点的芯片（只在「每周」时出现）。

### 真机验证

* 建「周一周三 07:30」→ 服务端 cron `0 30 7 * * 1,3 *`、文案 `07:30 周一、周三`；
* 改成 08:15 → cron `0 15 8 * * 1,3 *`（**周几保留**）、文案 `08:15 周一、周三`；
* 删除 → 账号回到原来的 2 个自动化。

### 工程

* 测试 325 → **333**：`test_timer` 加 7 条（周几 cron/标签/过滤/retime 保留周几/边界），provider 2 条（weekly、workday 过滤），web 2 条（weekly 缺 days、未知 repeat 都要 400）。
* 顺手让 provider 在 name 为空时用标签当名字（API 层之前只有路由兜底，直接调 provider 会报「名字不能为空」）。
* `ruff` 全绿；产物 0.6.6。

---
## 0.6.5 — 2026-11-21

新建定时打通：界面上选设备 → 选动作 → 选时间，就能建一条每天执行的定时。至此「定时/预约」这一行完成。

### 逆向：新建场景必须复用真实记录做模板

* 手工拼 `scene_action` 会被服务端拒：`-998 CheckTriggerConditionAction → CheckActionTypePayload`，
  即便 payload 字段看起来一样。**必须拿一条真实动作当模板**，只改 `payload_json` 里的
  `did`/`device_name`/`model`/`uid`/`value`，保留 `sa_id`/`type`/`name` 这些服务器自己产的簿记字段。
* 场景外壳同理：以一条现有场景（自动化的或手动场景的 `GetSceneInfo`）为骨架，替换 `name`、
  把 `scene_trigger` 换成 timer 触发、替换 `scene_action.actions`、清空 `scene_condition`/`scene_else_action`。
  **去掉 `scene_id` 即新建**。
* 实测：账号里没有 `action` 类动作可作模板时，创建会被我们自己拦下并说明原因（不硬凑）。

### 新增：新建定时

* `POST /api/timer/create {did, hour, minute, kind: prop|action, siid, piid|aiid, value|inputs, name}`；
  组装逻辑在 `domain/automation.py`（动作重定向、外壳组装、只读字段剥离），全部可离线测试。
* 界面上「新建定时」按钮只在场景页出现；弹窗里设备下拉**只列有可用动作的设备**，
  动作下拉列出「开关：开/关」「枚举控件 + 取值」「无参动作」，时间默认 08:00，名称留空自动生成 `HH:MM定时`。
  枚举值按原始类型发送（下拉框的字符串会被设备按类型拒绝，这个坑 0.5.0 就踩过）。

### 真机验证

* 通过界面接口给灯泡建了一条 10:30 的定时：`ok=true`，列表里出现 `mihome-pc 测试新建`（10:30、设备=灯泡），
  随后删除，账号回到原来的 2 个自动化，无残留。

### 工程

* 测试 312 → **325**：新增 `tests/test_automation.py` 10 条（动作重定向/保留簿记/不改进模板/外壳组装/拒绝空输入），
  provider 2 条（模板选择、设备不存在），web 2 条（demo 新建、坏参数 400）。
* `ruff` 全绿；产物 0.6.5。

---
## 0.6.4 — 2026-11-21

定时从「只能看和启停」变成「能改时间、能删」。顺带把新建定时的全部零件（`Edit`/`Delete`）验证通了。

### 逆向：改定时 = 把整条场景记录重新提交

* `GetSceneInfo` 拿到的记录就是编辑用的载体：去掉只读字段（`uid`/`create_time`/`last_edit_time`/`icon_url`）后，
  补 `edit_from:1`、`value_format:1`、`app_version:19`、`owner_uid`、`home_id`，POST `AppSceneService/Edit`。
  **带 `scene_id` 是在原记录上改，去掉 `scene_id` 就是新建一条**（拿返回里的新 id）。
* 时间的载体是 `scene_trigger.triggers[0].value_json`（7 段 cron `0 45 8 * * * *`），
  显示文案在同一个 trigger 的 `name`（`08:45 法定工作日(中国大陆)`）——只替换开头的 HH:MM，保留「法定工作日」这类修饰。
* 删除：POST `AppSceneService/Delete`，body `{del_from:1, scene_id, home_id, owner_uid}`，直接回 `true`。
* 证据：`_m_j/o29.java:403-433`（Edit）、`_m_j/o29.java:366-401`（Delete）。

### 新增：定时改时间 / 删除

* `POST /api/timer/update`、`POST /api/timer/delete`；组装逻辑在 `domain/timer.py`（cron 生成/解析、文案重写、只读字段剥离），可离线测试。
* 场景页的定时行多了「时间选择 + 保存 + 删除」；保存后自动刷新（`/api/scenes?refresh=1` 绕过 15 秒缓存），删除前有确认。
* 非定时自动化不显示这两个按钮（改时间对它们没意义）。

### 真机验证

* 复制账号里的定时新建一条测试定时（08:47）→ 通过 `/api/timer/update` 改成 09:05 →
  列表里 `clock` 与文案都变成 `09:05 测试` → 通过 `/api/timer/delete` 删除 → 列表回到原来的 2 条，账号没留垃圾。

### 工程

* 测试 294 → **312**：新增 `tests/test_timer.py` 13 条（cron 生成/解析/越界、文案保留修饰、payload 组装/不改入参/拒绝空数据），
  provider 3 条（改时间、失败上报、删除），web 3 条（demo 改与删、坏参数 400）。
* `ruff` 全绿；产物 0.6.4。

---
## 0.6.3 — 2026-11-21

把「定时/自动化」这半块补上：定时其实就是**触发源是 timer 的自动化**，现在能看、能启停。

### 逆向：米家的「定时」不是设备 miIO 定时

* APK 里根本没有 `get_timer`/`add_timer` 这类 miIO 方法串；定时走的是场景服务：
  `GetSimpleSceneList` 的 `auto_scene_info_list` 里，`first_trigger.src == "timer"` 的那些就是定时。
* 列表接口只给 `src`/`key`，**没有具体时间**；时间要再调一次 `GetSceneInfo`
  （body `{scene_id, value_format:1, owner_uid, home_id, app_version:12}`），返回里
  `scene_trigger.triggers[0]` 带 `name`（`08:45 法定工作日(中国大陆)`）、`value_json`（cron `0 45 8 * * * *`）、
  `extra_json.time_zone`。
* 启停：`SetSceneStatus` body `{scene_id, home_id, owner_uid(字符串), enable(bool)}`，成功回布尔 `true`。
* 证据：`scene/timer/*Activity`、`_m_j/ltf.java:92-104`（列表请求体）、`_m_j/vrc.java:44-50`（启停请求体）、
  `scene/api/OooO00o.java:107-127`（详情请求体）。

### 新增：场景页的「定时与自动化」

* `/api/scenes` 的 `automations` 现在带 `trigger{src,label,detail,cron,timezone}`、涉及设备、动作数；
  列表 15 秒缓存，读取时对每个自动化补一次 `GetSceneInfo`（拿不到就退回只有名字/启停，不报错）。
* 新增 `POST /api/scene/status`（启停），UI 一行一个开关，切换后本地立即反映并 toast。
* 场景卡片上方补一句「下面是自动化：定时会自己跑，不需要手动执行」，避免误以为要手点。

### 真机验证

* 账号里 2 个自动化：`八点四十五提醒`（定时、`08:45 法定工作日(中国大陆)`、播报小爱音箱）
  与 `小米人在传感器有人-批量打开设备`（设备状态触发、已停用）—— 都能正确显示触发描述与设备。
* 对定时做「停用 → 重读 → 启用 → 重读」：`enabled` 从 True→False→True，两次都 `ok / code 0`。

### 工程

* 测试 288 → **296**：自动化触发解析、`GetSceneInfo` 合并、启停成功/失败映射、web 层启停与 400。
* `ruff` 全绿；产物 0.6.3。

---
## 0.6.2 — 2026-11-21

新增**场景页**：一键执行家里的手动场景（接口是真机验证过的，含新挖出来的 `scene_type` 必填项）。

### 新增：场景

* 后端：`GET /api/scenes`（手动场景 + 自动化数量）、`POST /api/scene/run`；
  归一化放在 `domain/scene.py`（名称/分类中文/设备去重/动作延时，未知分类原样透出）。
* 前端：侧栏新增「场景 N」；卡片显示图标、名称、分类、涉及设备，右侧「执行」按钮，
  执行中禁用并在完成后 1.2 秒恢复，成功/失败都有 toast；深链接 `/?view=scenes`。
* 演示模式自带两个场景（观影模式 / 睡眠模式），未登录也能看页面、能点执行。

### 逆向：`NewRunScene` 的真实请求体

* 只发 `scene_id` 会被拒：`-8 invaild scene_type`。实测 `scene_type=2`（`CommonUsedScene.SPEC_SCENE_TYPE`）
  才能执行 `GetSimpleSceneList` 里的场景；`scene_type=1` 是旧的类型，回 `sceneInfo is empty`。
* 还需要 `phone_id`（非空即可），共享家庭再加 `home_id` + `owner_uid`。
* **成功返回布尔 `true`**，不是 `{code:0}` —— 按 code 解析会把成功判成失败（初版就踩了）。
* 细节与证据代码行号见 `docs/reverse/01-网络与签名.md` §10。

### 真机验证

* 真实账号 4 个场景（观影模式 / 睡觉 / 出门 / 正常模式），`/api/scenes` 全部带设备名返回；
* 通过界面接口执行「观影模式」→ `ok=True code=0`，随后执行「正常模式」复原 → 同样 `ok=True`。

### 工程

* 测试 271 → **287**：`tests/test_scene.py` 13 条（归一化、空/脏数据、provider 缓存、scene_type、布尔成功）+ web 层 4 条。
* 侧栏高亮与搜索框按页面显隐；`ruff` 全绿；产物 0.6.2。

---
## 0.6.1 — 2026-11-21

桌面形态补齐：右下角托盘图标（右键退出）、单实例、Windows 安装版带桌面快捷方式，绿色版带中文使用须知。

### 新增：通知区域图标

* `python -m mihome_pc app`（打包启动器默认走这个）：起本地服务 + 托盘图标 + 单实例。
* 右键菜单：打开米家 PC / 服务地址（不可点，用来看端口）/ 打开数据目录 / 退出；双击图标等于打开界面。
* **退出链路已验证**：关掉图标 → 应用关服务器、删 `app.lock`；应用被杀 → 图标自己退出（2 秒轮询）。
* 两条实现并存：`mihome_pc/tray.py`（ctypes 直调 `Shell_NotifyIcon`，菜单更完整）与
  `mihome_pc/tray_ps.py` + `assets/tray.ps1`（.NET `NotifyIcon`，PS 2.0 兼容 Win7）。
  选后者兜底是因为**本机实测 ctypes 那条会返回 ERROR_ACCESS_DENIED（5）**，而同一台机器上 .NET 正常；
  启动时先 `tray.probe()` 探测，失败自动落到 PowerShell 实现（图标挂上后会写 `data/logs/tray.ready`，便于排障）。
* 单实例：第二次双击不会起第二个服务器，而是直接把已有界面打开（`data/app.lock` 里记 pid + URL）。

### 新增：Windows 安装版 + 绿色版

* 绿色版新增 `使用须知.txt`（中文，讲清怎么启动、托盘怎么用、数据在哪、怎么卸载）。
* 安装版：双击 `安装到桌面.cmd` → `install.ps1` 把程序复制到 `%LOCALAPPDATA%\Programs\MiHomePC`，
  在**桌面**和开始菜单建带图标的快捷方式（`mihome.ico`），生成 `uninstall.ps1` 与「卸载 米家 PC」快捷方式；
  不写注册表、不要管理员，安装时去掉 `portable.flag`（数据改放 `%USERPROFILE%\.mihome-pc`）。
* 图标：`mihome_pc/assets/mihome.ico` 由 `tools/make_icon.py` 从 `static/logo.png` 生成（PNG-in-ICO，不需要 Pillow）。

### 工程

* 新增 `tests/test_app.py` 13 条：托盘菜单模型、ICO 头、单实例锁、PowerShell 命令拼装、平台可用性。
* `ruff` 排除 `out/`、`dist/`（生成物）；全绿。测试 258 → **271**。

---
## 0.6.0 — 2026-11-21

这一版换掉了「手机版式塞进桌面窗口」的做法，并把插件文案变成**默认就有**，不再需要手动同步。

### 界面：桌面化重做

* **左侧导航 + 内容区**的外壳：全部设备 / 运行状态 / 账号与同步，底部是登录态与明暗切换；
  窄于 1000px 时侧栏自动折成顶部一行，内容改单列。
* **首页**：页头（标题 / 「15 台设备 · 11 台在线 · 6 个房间」/ 搜索框 / 运行中徽章 / 刷新）+ 房间胶囊 +
  三列自适应卡片网格（`minmax(232px, 1fr)`）。
* **详情页双栏**：左边控制（操作 + 分组控件），右边状态（设备状态卡 + 遥控面板 + 故障码对照）；
  只有遥控面板的设备（电视）面板放主栏居中。
* 详情页不再重复显示顶部标题（详情条本身已有返回 / 型号 / 在线态），并修掉 `.page` 放进 grid 后
  `margin: auto` 导致内容按内容宽度收缩、1180px 窗口只剩两列的问题。
* 新增「账号与同步」面板：登录态、版本、网关、数据目录、插件文案覆盖情况。

### 插件文案：默认自动同步

* `serve` 启动时在后台补全缺失的插件文案（`sync_plugins_in_background`）：缓存包**本地解析**，
  只有真正缺的才联网；日志写 `data/logs/web.log`。
* `plugin.ensure()` 遇到「有 `package.zip` 但没有 `texts.json`」的旧缓存会就地回填，不需要重下。
* **扫码登录后免重启**：provider 会跟随 `credentials.json` 的 mtime 切换，登录成功即从演示模式切到云端，
  并顺带触发插件同步。
* 修掉一个静默 bug：`create_server` 里残留的 `provider = provider or DemoProvider()` 会把刚建好的
  云 provider 换回演示 provider —— 登录后仍显示演示数据、自动同步也没跑。已加回归测试。
* CLI `plugin-sync`：服务端不给包的型号（暖风机）显示「服务端不给包」，不再假装「已下载」。

### 版本号单一来源

* 新增 `mihome_pc/__init__.py::VERSION`；`packaging/build_win.py` 与 `build_deb.py` 改成读它，
  界面 `/api/meta` 也返回同一个值，不会再出现「UI 说 0.5.3、产物叫 0.5.2」。

### 文档

* 新增 `docs/04-功能对照.md`：17 项功能逐条对照米家 App、标注现状与优先级（场景页是 P0，接口已验证可用）。

### 工程

* 测试 256 → **258**（provider 选择回归 2 条）；`ruff` 全绿；产物重建为 0.6.0。

---
## 0.5.3 — 2026-11-21

把 0.5.2 拿到的插件文案真正接到界面上：枚举文案用官方的，故障码对照直接看得到。

### 新增：插件文案覆盖枚举标签

* `mihome_pc/domain/plugin_text.py`：从 `data/plugins/<型号>/texts.json` 取文案，按**值名**匹配
  （spec 的 `Drum Clean` → `mode_DrumClean`），不做模糊文本相似——错标签比回落更糟。
* 匹配规则：键的**尾巴**要等于值名，尾巴**前面**的词必须都属于属性名（`head ⊆ prop`）。
  这条规则拦住了两个真实误配：水位 `middle` 不会命中 `dry_level_middle`（即穿），
  水位 `low` 不会命中 `water_low`（「到达补水次数，水位仍过低（LF）」这条故障说明）。
* 实测洗衣机 22 个程序里有 **11 个**和内置词典不同，且插件说法更准确：
  漂洗→单漂洗、衬衫→衬衣洗、煮洗→高温洗、外套→冲锋衣、内衣→内衣洗、烘干→单烘干、化纤→化纤洗…
* 取不到就回落到 spec i18n / 内置词典，所以没有插件的型号（炸锅、灯泡）行为不变。

### 新增：故障码对照

* 插件自带成对的故障说明：`temperature_sensor_problem` =「温度传感器异常（tF）」+ `_method` = 处理办法，
  洗衣机一个型号就有 **16 条**（CF/bF/CE/dE/HE/FE/3H/IE/dF/rF/OE/UF/tF/LU/HF/~F）。
  MIoT spec 里完全没有这些信息。
* `plugin_text.fault_notes()` 把它们提取成 `{code, title, method}`，随 `/api/device` 下发；
  详情页底部渲染成可折叠的「故障码对照 N 项」，**设备正在报故障时自动展开**（看当前枚举标签里有没有「故障/异常/报警」）。
* 非故障的 `_failed` 条目（例如「加载失败，点击重试」）按「必须带 1–4 字符故障码」过滤掉。

### 工程

* 测试 241 → **256**：新增 `tests/test_plugin_text.py` 14 条（匹配、head 规则、语言别名、故障表、
  DeviceView 级「插件说法优先 / 无插件回落」）。
* `ruff --target-version=py38` 全绿；产物重建为 0.5.3。

---
## 0.5.2 — 2026-11-21

这一版的收获一半是功能、一半是**否定结论**：插件文案表彻底打通，「规则硬编码在插件代码里」被证伪。

### 新增：插件文案表（具名 + 简体中文）

* 卡点真相：不是 complex entry、不是偏移解析，而是 `string` 类型有 **94 个语言分片**，
  原型按文件顺序遍历、后者覆盖前者，最后落在 `zh-rTW` —— 339 条中文**全在，只是全是繁体**，
  按简体关键词 grep 一条都中不了，于是被误判成「中文没取到」。
* `mihome_pc/xiaomi/arsc.py` 新增 `table_texts` / `package_texts` / `config_locale` / `has_cjk`
  与 `LOCALE_PREFERENCE`（zh-rCN → zh → default → zh-rSG → zh-rHK → zh-rTW → en）。
* `plugin-sync` 现在同时写 `strings.json`（扁平去重）和 `texts.json`（`资源名 → 文案`）；
  实测洗衣机 542 条扁平 / 339 条具名，电视 77 / 59。CLI 输出加「具名 N 条」。
* 故障码解释现在可直接拿：`通讯故障（CE）`、`门锁故障（dF）`、`电脑板与变频板通讯异常（CF）`、`洗涤电机故障（rF）`。

### 结论修正：规则不在插件 Java 代码里（有证据）

* `筒自洁模式下不能预约，请重新选择模式`：584 个 java **0 处**引用、数字 id **0 处**、
  dex 里 `const-string` **0 个方法**引用、407 个资源 XML **0 处** `@string/`。
* 360 条 string 资源中 212 条被 `R.string.xxx` 引用，148 条没有；而那 148 条**全部**以字面量存在于 dex 中
  —— 插件把资源名留给宿主（主 App 的通用插件框架）按名字解析，而不是自己显示。
* 因此「grep 插件代码拿可用性规则」这条路作废；现实路径改为真机矩阵探测 / 宿主 RN 框架 / 云端拒绝码。
  推演与数据见 `docs/reverse/04-插件包下载.md` §10。

### 工具

* `tools/arsc_map.py` 重写：产品模块的命令行外壳，新增 `--stats`（结构 + 分片统计）、`--locale`、`--all-configs`。
* 新增 `tools/plugin_texts.py`：`名字 → 文案` + 「哪些名字被代码引用 / 哪些只以字面量留在 dex」。
* 新增 `tools/dex_literal_refs.py`：给定 dex 与字面量，列出引用它的方法（扫 class_data → code_item 的 const-string）。
* `tools/plugin_strings.py` 改为薄壳，删掉与产品模块重复的 100 行解析。

### 工程

* 测试 235 → **241**：新增 `tests/test_arsc.py`，用手工构造的 ARSC（含 `default` 与 `zh-rTW` 两个分片）
  钉住语言偏好、缺失回落、`keep_all` 与 `config_locale`；不依赖任何外部插件包。
* `ruff --target-version=py38` 全绿。

---
## 0.5.1 — 2026-11-21

这一版修掉了一个「看着像协议不通、其实是本地过滤」的缺陷：**电视的状态永远是空的**。

### 修复：电视（以及任何「属性都归遥控面板」的设备）没有任何状态

* 现象：`/api/device?did=mitv...` 返回 `controls: []`、`state: {}`，遥控面板上的音量/静音/输入源看不到当前值。
* 根因：`DeviceView.__init__` 为了「别把遥控器的属性重复成普通行」，把这些属性从 `controls` 里**删掉**了；
  而 `refresh_state()` 只轮询 `poll_items()`（源自 `controls`）。电视的三个可读属性（2.1 输入源、4.1 音量、4.2 静音）
  全在遥控面板名下 → 控件列表为空 → `poll_items()` 为空 → `/miotspec/prop/get` **一次都没发过** → `state` 自然为空。
  云端其实是好的：直接读得到 `2.1=1`、`4.1=12`、`4.2=false`（电视离线时返回的是最后一次上报值，带 `updateTime`）。
* 修法：遥控面板负责的**属性**保留在 `controls` 里（面板要用它的控件对象读写值），只在 `groups()` / `status_controls()` /
  `primary` 三处排除；动作仍按 `REMOTE_ROLES` 从通用「操作」列表里拿掉。新增 `DeviceView.remote_keys` 表达这层归属。

### 修复：遥控面板的音量 / 静音 / 输入源

* 面板按键原来按 `siid/piid` 在 `controls` 里找控件，控件被删过 → 静音键与输入源一直**根本没渲染**。
* 新增摇杆下方的「音量 12%」读数；静音键未静音时显示喇叭图标、静音后显示叉号并高亮。
* 静音键改成点击时读**当下**状态再取反（原来闭包里的是上一轮轮询的旧值，连点会发同一个值）。

### 新增：离线时把「缓存值」说成缓存

* 电视关机时云端的 `prop/get` 仍返回最后一次上报值（带 `updateTime`）。原来 UI 只显示「离线」+ 音量 12%，
  看上去像实时值。现在 `refresh_state()` 顺手记下 `updateTime`，`/api/device` 多返回一个 `state_seen`
  （形如 `{"4.1": <时间戳>}`），遥控面板底部写成「设备离线，按键不会响应（下面显示的是 12 小时前上报的值）」。

### 修复：`message-router` 不再是用户可见的按钮

* 电视的 siid 5 是固件内部的 `post(request) -> response` RPC，通用渲染把它变成了「消息传递 / 执行」，点了会发空串。
  新增 `_PROTOCOL_SERVICES`，控件和动作都不再暴露它。

### 修复：只有遥控面板的设备不再报「规格缺失」

* 详情页底部那句「该设备没有可显示的控件（规格缺失？）」原来只要没有通用控件就出现，现在把遥控面板也算作控件。

### 演示与工具

* 演示模式新增电视（`demo-tv`，`xiaomi.tv.fsv1`）：未登录也能看到遥控面板，音量预置 12%、输入源 TV。
* 新增 `tools/probe_tv_state.py`：打印设备记录、可读属性、`poll_items()` 与网关对 `prop/get` 的实际回答——
  排查「有没有状态」先跑它，能一眼看出是没发请求还是设备不答。

### 工程

* 测试 222 → **234**（新增 11 条：domain 层 6 条钉住归属规则，web 层 5 条钉住 `state` 非空与 `prop/get` 真的发出）。
* `ruff --target-version=py38` 全绿。

---
## 0.5.0 — 2026-11-21

这一次会话的主体工作是**把"能控"推进到"控得对、看得懂"**，并打通了第四条逆向主线（插件包）。

### 新增：插件包（第四条逆向主线）

* `mihome_pc/xiaomi/plugin.py`：`POST /v2/plugin/fetch_plugin` 的完整实现（**嵌套请求体**才有效，扁平体会得到全 null），
  含 `fetch_plugin_info` / `download_package` / `extract` / `bundle_text` / `search_rules` / `ensure` / `sync_all`。
* `mihome_pc/xiaomi/arsc.py`：纯标准库解析 `resources.arsc` 字符串池，取出插件里全部官方文案。
* CLI：`python -m mihome_pc plugin-sync [型号...] [--force]`，实测洗衣机 542 条中文文案、电视 77 条。
* 结论：`plugin_id == 0` 表示该型号**没有插件**（炸锅、灯泡即如此），走通用 spec 渲染。
* 插件有两种形态：混合（原生 + `assets/index.android.bundle` 明文 JS）与纯原生（无任何 bundle）。
  文案统一在 `resources.arsc`，不在 JS 里。

### 新增：设备专属能力

* **电视唤醒（经小爱音箱）**：音箱 `intelligent-speaker` 服务 action 5.4 `execute-text-directive`，
  发「打开电视」→ `code 0`，约 2 分钟（冷启动）/ 1 秒（息屏）后电视上线。`POST /api/wake-tv`。
* **电视网络唤醒兜底**：`POST /api/wol` 发标准魔术包（无音箱时用），本机型实测**不生效**，接口保留。
* **局域网在线探测**：`GET /api/presence?did=`，TCP 0.7s 探测，比云端 `isOnline` 快得多。
* **动态范围（跨属性约束）**：`domain/limits.py` + `assets/dynamic_ranges.json`。
  空气炸锅「温度越高、可设时长越短」由用户面板实测编码：≤85℃→1440 分钟，86-105→480，106-155→120，156-200→60。
  滑块上限随温度自动收缩，越界写入本地拦截并给中文原因。

### 新增：界面

* **设备状态弹窗**：`POST /smh/get_act_data`（服务端已算好分组/计数/倒计时），顶栏徽章显示「运行中 N」。
* **电视遥控面板**：圆形方向盘 + chevron 图标键 + 音量摇杆 + 播放控制；BLE-only 的「开机」如实禁用并标「蓝牙」。
* **房间系统**：来自 `gethome_merged` 的 `roomlist[].dids`，首页胶囊筛选。
* **明暗切换**：改编自 `Xiumuzaidiao/Day-night-toggle-button`（云/星/陨石坑/回弹）。
* **设备状态区**独立视觉（色底 + 网格 + 计数），可设置项与只读项分栏。
* 详情页头部**通栏吸顶**；下拉自绘皮肤；「本轮约 N 分钟」提示；只读长文本可断行。

### 修复

* `code: 1` 也是成功（灯泡族），此前误判为失败导致"每次操作都报错"。
* 只读布尔/数值不再渲染成开关/滑块（改为状态胶囊、进度条）。
* 单位与文案：`percentage` 不再回退成英文；补全洗衣机 22 个程序（含**空气洗**，原误译"风干"）。
* 语义化翻译改为**按设备类型**区分：洗衣机 `Dry`→烘干，空调 `Dry`→除湿。
* 设备/房间列表加入 20 秒 TTL（原来永久缓存，新设备、新房间永远看不到）。
* 下拉框会被轮询重建导致自动收起 → 交互期间暂停轮询、`<select>` 就地改选中项。
* `<option>` 永远是字符串，直接下发 `"4"` 会被设备按类型错误拒绝（`-704220043`）→ 改为按选项原始类型发送。
* `applyState` 被误删导致「失败：applyState is not defined」→ 补回。
* `overflow-x: hidden` 会破坏 `position: sticky` → 改用 `overflow-x: clip`。

### 逆向文档（`docs/reverse/`）

* `01-网络与签名.md`：补 §9 首页状态弹窗接口。
* `03-真机行为记录.md`：补电视唤醒三条路径实测、两种关机态（息屏 1s / 关机 2min）、
  `wake_up_rc` 三个遥控器 MAC、WoL 失败记录、**环境声明（该电视为已改机环境）**。
* `04-插件包下载.md`：新增，接口/结构/两种形态/文案与规则的边界。

### 工程

* 222 个单元测试（真 Python 3.8.10）全部通过；`ruff --target-version=py38` 全绿。
* 打包重建：Win 便携 zip、deb、linux tar.gz。
* 新增工具：`tools/plugin_strings.py`、`tools/arsc_map.py`、`tools/probe_fryer_limits.py`、`tools/wol_timing_test.py`、`tools/ble_scan.ps1`。

---

## 0.4.0 — 2026-11-19

* 首个可用版本：云端协议（签名/RC4/AES-GCM）、spec 层、扫码登录、控件生成、
  可用性矩阵探测、零依赖网页 UI、Win/Linux 三份产物。

## 0.3.0 及更早

见 `E:\DSH-Archive-2026-11-19\mihome-pc-v2-session\`（v1/v2 已归档，不再维护）。
