# 发布到 GitHub（Windows 首发）

这份文档写清楚：**发布什么、不发布什么、怎么脱敏、怎么发**。
照做即可，不需要再回头翻聊天记录。

## 0. 一句话现状

- 版本：`0.7.25`，Windows 绿色版 + 安装版已经过门禁（见 `docs/阶段总结.md` 的「最终门禁」表）。
- git 尚未初始化（`git repo: no`），所以这次是**首次提交**。
- 许可证：**MIT**，版权人 `Copyright (c) 2026 shallow-seek-nothing`（`LICENSE` 已就位）。
  第三方资源边界见 `THIRD_PARTY_NOTICES.md`。

## 1. 不进仓库的东西（`.gitignore` 已覆盖）

| 路径 | 为什么 |
|---|---|
| `data/` | 凭据、插件缓存、spec 缓存、日志、收藏 —— 含账号信息 |
| `reverse/` | 米家 APK 的 jadx 反编译源码，916 MB、5.5 万个文件；也不适合公开再分发 |
| `dist/` / `out/` | 打包产物与工作截图，走 GitHub Releases 附件 |
| `.toolchain/` | 嵌入式 Python 3.8（629 MB） |
| `tools/wol_timing_test.py`、`probe_fryer_limits.py`、`probe_tv_state.py`、`do_login.py` | 一次性实验脚本，里面写死了真实 IP / did / token |
| `docs/reverse/03-真机行为记录.md` | 真机实验日志，含 MAC / BLE 地址 / token 前缀 / ADB 端口 |

> 这几个「本地实验」文件我保留在你机器上，只是不提交。要彻底删也可以，先确认你不再需要它们。

## 2. 脱敏（已做，可复查）

已经处理过的地方：

* `docs/reverse/01-网络与签名.md`：`home_id` / `owner_uid` → `<home_id>` / `<owner_uid>`
* `docs/03-现状与待办.md`：本机路径、电视 IP、NAS 地址 → 占位符
* `CHANGELOG.md`：设备 did、BLE did、房间 id、序列号、IP、MAC → `<did>` / `<room_id>` / `<sn>` / `<ip>` / `<mac>`
* `tests/test_timer.py`、`tests/test_device_info.py`：测试夹具里的真实 uid / home id / 房间 id 换成假值
* `README.md` 的截图全部换成**演示模式**新拍的（家庭名「演示家庭」，设备是虚构的）

复查命令（发之前务必跑，必须输出 `0 suspicious line(s)`）：

```powershell
python tools/sanitize_check.py
```

## 3. 提交前再跑一遍门禁

```powershell
python -m unittest discover -s tests     # 474 条
python -m ruff check .
python packaging\check_release.py        # 版本 / 产物 / 哈希一致
python tools\sanitize_check.py           # 脱敏复查
```

## 3.5 仓库名与展示名

（最初想用 `Mijia_Win7/8.1/10/11`，但斜杠在仓库名里非法。）**评审后定为 `mihome-pc-unofficial`**：
页面展示名「米家 PC 客户端（非官方）」，英文简介 `Unofficial community desktop client for Mi Home devices`。
Python 包名 `mihome_pc`、命令 `mihome-pc` 与构建脚本**保持不变**。

> 标题可以照旧写「米家 PC 客户端（Win7/8.1/10/11）」——标题没有命名限制。

## 4. 首次提交

```powershell
cd <仓库根>
git init -b main
git add .
git status --short        # 看一眼有没有 data/ dist/ reverse/ 混进来
git commit -m "米家 PC 客户端 v3：0.7.25（Windows 首发）"
git remote add origin git@github.com:<你的账号>/<仓库名>.git
git push -u origin main
```

首次 `git add` 之后**一定要**用 `git status --short` 或 `git ls-files | Select-String 'data/|dist/|reverse/'` 确认大件没进去。

## 5. 发 Windows Release

附件只放两样（不放进 git）：

* `dist\mihome-pc-0.7.25-portable-win64.zip`
* `dist\发布说明-0.7.25.md`（含 sha256，可直接贴进 Release 正文）

网页操作：Releases → Draft a new release → tag `v0.7.25` → 标题「米家 PC 客户端 0.7.25（Windows）」→
正文粘贴 `dist\发布说明-0.7.25.md` → 上传上面那个 zip → Publish。

命令行等价：

```powershell
gh release create v0.7.25 dist\mihome-pc-0.7.25-portable-win64.zip `
  --title "米家 PC 客户端 0.7.25（Windows）" `
  --notes-file dist\发布说明-0.7.25.md
```

## 6. 发完之后

* README 的「下载与安装」里把 Release 链接补上（现在写的是 `dist/` 相对路径）。
* Linux 的 deb/tar.gz 这次不发，等 Windows 反馈稳定后再作为第二个 Release。
* 有人提 issue 说 Win7 跑不起来时，先让他跑 `python -m mihome_pc selftest --env` 并把输出贴上来。

## 7. 来源不明的派生数据（重要）

`mihome_pc/assets/catalog.json`、`catalog_categories.json`、`card_layouts.json` 是从**米家 APK 内置配置**提取的，
再分发依据未确认（见 `THIRD_PARTY_NOTICES.md` 风险 1）。处置：

* 已加入 `.gitignore`，**源码仓库不收录**；
* 代码对缺失优雅降级：无中文型号名、卡片用通用布局，`selftest` 与 535 条测试仍通过（多跳过 1 条依赖 catalog 的用例）；
* 需要完整型号目录的用户用**自己拥有的安装包**本地生成：

```powershell
python tools\make_catalog.py
python tools\make_card_layouts.py
```

* Windows Release 是否附带这三个文件，由所有者决定；附带即承担相应风险。
