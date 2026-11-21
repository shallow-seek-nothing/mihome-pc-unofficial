# 打包

所有产物都落在 `dist/`，构建过程**不需要** PyInstaller、dpkg-deb、node 或任何
交叉编译工具链 —— 目标平台太多，构建机必须是"零特殊依赖"。

## 构建

```powershell
python packaging\build_win.py --clean     # Windows（Win7 SP1+，x64）
python packaging\build_deb.py             # Linux（.deb + 便携 tar.gz）
python packaging\verify_deb.py            # 校验 .deb / tarball 结构
```

## 产物

| 文件 | 大小 | 说明 |
|---|---|---|
| `dist/mihome-pc-win64/` | ~18 MB | 嵌入式 Python 3.8.10 + 代码 + 资源，双击 `mihome.vbs` 即用 |
| `dist/mihome-pc_0.4.0_all.deb` | ~513 KB | Debian/Ubuntu 系；`Depends: python3 (>= 3.8)` |
| `dist/mihome-pc-0.4.0-linux.tar.gz` | ~508 KB | 通用；`sh install.sh [--prefix DIR]` |

## 三个必须守住的细节

1. **`python38._pth` 不能有 BOM。** PowerShell 的 `Set-Content -Encoding UTF8`
   会加 BOM，第一行变成 `\ufeffpython38.zip`，解释器直接
   `init_fs_encoding: failed to get the Python codec` 起不来。
   `build_win.py` 用 `io.open(..., encoding="ascii", newline="")` 写，并在打包后
   断言前三字节不是 `EF BB BF`。

2. **`pythonw.exe` 的 `sys.stdout is None`。** 裸 `print()` 会抛异常并让进程退出，
   所以 `_boot.pyw` 先把 stdout/stderr 重定向到 `logs/mihome-pc.log`。
   日志写在程序目录而不是 `%TEMP%`：Store 版 Python 的 `%TEMP%` 会被 MSIX 重定向。

3. **嵌入式 Python 在受限环境里写不了 `%TEMP%`**，`tempfile.gettempdir()` 会回退到
   当前目录。因此代码里不依赖 `tempfile` 存放持久数据，测试也统一用
   `TemporaryDirectory()`（否则会在项目根留下一堆 `tmp*` 目录）。

## Linux 侧的兼容性取舍

- 只依赖 `python3 >= 3.8` 标准库，`Architecture: all` —— 一个包覆盖
  Debian/Ubuntu/Fedora/RHEL/Arch/openSUSE；Alpine 用便携 tarball。
- **不编译任何扩展**，所以不受 glibc/musl、ABI 版本影响。
- 服务集成给了 systemd user unit（`/usr/lib/systemd/user/`）；OpenRC 用户用
  `install.sh` 后自行在 `/etc/local.d/` 添加一行，或用便携 tarball 里的说明。
- 桌面图标：`/usr/share/applications/mihome-pc.desktop`，`Exec=mihome-pc serve`。

## 校验做了什么（`verify_deb.py`）

不用 `dpkg-deb`，自己解析 ar 容器：

- ar 成员顺序必须是 `debian-binary` / `control.tar.gz` / `data.tar.gz`
- `debian-binary` 内容 `2.0\n`
- control 里 `Package/Version/Architecture/Depends/Maintainer/Description` 齐全
- data 里启动器存在且**可执行位 0755**
- 代码、`assets/catalog.json`、`assets/demo/`、`static/`、desktop、systemd、copyright 都在
- **不含** `__pycache__`/`.pyc`、不含 `reverse/` 与 `tests/`
- 每个随包 `.py` 都能被 `ast.parse` 解析
