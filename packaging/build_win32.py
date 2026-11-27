"""Assemble the Windows distribution (Windows 7 SP1 and later, x86).

Layout produced::

    dist/mihome-pc-win32/
      python38/                 embedded CPython 3.8.10 (no install needed)
      mihome_pc/                our package (code + static + assets)
      _boot.pyw                 entry point, stdout/stderr redirected to a log
      mihome.vbs                silent launcher (double-click target)
      start-console.cmd         same thing but keeps the console for debugging
      README.txt                short usage notes
      DIST-MANIFEST.txt         sha256 of every shipped file

Two details that bite on this platform and are handled explicitly:

* ``python38._pth`` **must be written without a BOM**.  PowerShell's
  ``Set-Content -Encoding UTF8`` adds one, which turns the first line into
  "\\ufeffpython38.zip" and the interpreter dies with
  ``init_fs_encoding: failed to get the Python codec``.
* ``pythonw.exe`` has ``sys.stdout is None``; any bare ``print()`` raises and
  kills the process.  ``_boot.pyw`` re-binds stdout/stderr to a log file first.

Usage:  python packaging/build_win32.py [--clean]
"""
import hashlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from distribution_policy import (assert_tree_clean, copy_legal_documents,
                                 copy_project_package)

EMBED = os.path.join(ROOT, ".toolchain", "py38-x86")
DIST = os.path.join(ROOT, "dist", "mihome-pc-win32")

# Never ship these.
SKIP_DIRS = frozenset([
    "__pycache__", ".git", ".ruff_cache", "reverse", "tests", ".toolchain",
    "dist", "out", "docs", "packaging", "tools",
])
SKIP_SUFFIXES = frozenset([".pyc", ".pyo", ".md"])

def _read_version():
    """Version from ``mihome_pc/__init__.py`` (single source of truth)."""
    path = os.path.join(ROOT, "mihome_pc", "__init__.py")
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith("VERSION") and "=" in line:
                return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("cannot read VERSION from %s" % path)


# Version comes from the package itself, so the UI and the build agree.
VERSION = _read_version()

PTH_CONTENT = "python38.zip\r\n.\r\nLib\\site-packages\r\n..\r\n"

BOOT_PYW = '''# -*- coding: utf-8 -*-
"""Entry point for pythonw.exe (no console).

``pythonw`` gives us ``sys.stdout is None``; anything that prints would raise
AttributeError and end the process, so everything is redirected to a log file
first.  The log lives next to the app, not in %TEMP%, because a Store-packaged
interpreter gets a redirected %TEMP% (a known trap on this platform).
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
LOG_PATH = os.path.join(LOG_DIR, "mihome-pc.log")


def _redirect():
    if not os.path.isdir(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except OSError:
            return
    try:
        handle = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    handle.write("\\n=== %s ===\\n" % __import__("datetime").datetime.now().isoformat())
    sys.stdout = handle
    sys.stderr = handle


def _print_safe_exc():
    exc_type = sys.exc_info()[0]
    try:
        from mihome_pc.logs import redact_text
        text = redact_text(traceback.format_exc())
    except Exception:
        # 脱敏器本身没加载成功时宁可少记，也不能把原始异常写进日志。
        text = "startup error: %s (traceback withheld: redactor unavailable)\n" % (
            getattr(exc_type, "__name__", "Error"),)
    if sys.stderr:
        sys.stderr.write(text)


def main():
    _redirect()
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        from mihome_pc.cli import main as cli_main
    except Exception:
        _print_safe_exc()
        return 1
    try:
        # "app" = server + notification-area icon (right-click to quit).
        return cli_main(["app"])
    except SystemExit as exc:
        return exc.code or 0
    except Exception:
        _print_safe_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
'''

LAUNCH_VBS = '''\' Silently start the Mi Home PC client with no console window.
\' Kept ASCII-only on purpose: cscript reads .vbs as the ANSI code page and
\' non-ASCII bytes would be mangled on a GBK Windows install.
Option Explicit
Dim shell, fso, here, target, python
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
python = here & "\\python38\\pythonw.exe"
If Not fso.FileExists(python) Then
    MsgBox "python38\\pythonw.exe is missing from " & here, 16, "Mi Home PC"
    WScript.Quit 1
End If
shell.CurrentDirectory = here
shell.Run """" & python & """ """ & here & "\\_boot.pyw""", 0, False
'''

START_CMD = '''@echo off
rem Console build -- useful when something goes wrong and you want the output.
chcp 65001 >nul
cd /d "%~dp0"
".\\python38\\python.exe" -m mihome_pc serve
if errorlevel 1 pause
'''

README_TXT = '''Mi Home PC client %s
=====================================

How to run
----------
Double-click  mihome.vbs   (no console window)

If nothing happens, run  start-console.cmd  and read the output; the log is at
logs\\mihome-pc.log

What it needs
-------------
* Windows 7 SP1 or later, 32-bit.
* On Windows 7, install the Universal CRT (KB2999226) and restart when the
  installer requires it. The bundled Python interpreter still needs this CRT.
* Firefox 115 ESR is verified on Windows 7. IE8 is not supported;
  Chrome/Chromium 109+ has not been separately verified.
* No Python installation is required -- the interpreter is bundled in python38\\

First run
---------
The UI starts in demo mode.  Click "scan to log in" and scan the QR code with the
Mi Home app; credentials are stored in %%USERPROFILE%%\\.mihome-pc\\credentials.json

Data locations
--------------
%%USERPROFILE%%\\.mihome-pc\\credentials.json   account tokens (chmod 600)
%%USERPROFILE%%\\.mihome-pc\\specs\\             cached device specs
%%USERPROFILE%%\\.mihome-pc\\availability\\      availability rules from probing
logs\\mihome-pc.log                            runtime log
''' % VERSION



USAGE_TXT = r"""PC 控制端 %s —— 使用须知（绿色版）
================================================

怎么启动
--------
双击  mihome.vbs  （无控制台窗口；图标出现在右下角通知区域）

启动后在右下角能看到 PC 控制端图标：
  * 双击图标 / 右键 →「打开 PC 控制端」  打开界面
  * 右键 →「打开数据目录」           看缓存、日志、截图
  * 右键 →「退出」                   关闭程序（菜单里也能看到服务地址）

需要什么
--------
* Windows 7 SP1 或更新的 32 位系统。
* Win7 需先安装 UCRT（KB2999226），并按安装程序要求重启；内置 Python 仍依赖此运行库。
* Win7 已验证 Firefox 115 ESR；IE8 不支持，Chrome / Chromium 109+ 尚未单独实测。
* 不需要安装 Python：python38\\ 里自带 32 位解释器。

第一次使用
----------
界面开始是演示模式。点右上角「扫码登录」，用米家 App 扫码；
扫码确认后界面会自动切到账号数据，**不用重启程序、也不用手动刷新**；
凭据保存在数据目录里，下次启动自动登录。

换账号 / 退出登录
-----------------
界面左侧「账号与同步」→「退出登录」（点两下确认）。
只删除本机的 credentials.json，不会把手机上的米家 App 退掉；
退出后立刻回到演示模式，再点「扫码登录」换账号即可。

数据放在哪
----------
%%USERPROFILE%%\\.mihome-pc\\credentials.json   账号令牌（建议自己备份）
%%USERPROFILE%%\\.mihome-pc\\specs\\            设备规格缓存
%%USERPROFILE%%\\.mihome-pc\\plugins\\          插件包与官方文案
logs\\mihome-pc.log                                运行日志
（如果目录里有 portable.flag，数据会放在程序目录下的 data\\ 里）

出问题怎么办
------------
1) 双击  start-console.cmd  能看到报错信息，异常会留在 logs\\mihome-pc.log
2) 常见原因：端口被占用（换一台机器试）、浏览器太旧（< 109）、
   代理软件拦截 127.0.0.1（把它加进例外）
3) 扫码后一直是演示模式：0.7.36 及以前需要重启程序（新版已修）；
   凭据文件坏了的话，界面会直接提示「凭据文件无法读取」，重新扫码即可
4) 想换账号：「账号与同步」→「退出登录」，或退出程序后删除 credentials.json，重新扫码

从旧版升级
----------
* 安装版：退出旧程序，双击新包的「安装到桌面.cmd」即可覆盖升级，不用先卸载；
  用户数据和登录凭据会保留。
* 绿色版：退出旧程序，把新版解压到新目录，将旧目录的整个 data 文件夹复制过来；
  确认新版数据正常后再删除旧目录。升级前建议额外备份 data。

卸载（绿色版）
--------------
右键托盘图标 →「退出」，然后直接删掉整个文件夹即可，不写注册表、不装服务。
""" % VERSION

INSTALL_PS1 = r"""param([switch]$Quiet)
# 安装/覆盖升级到当前用户（不写注册表、不装服务、不需要管理员）
# 目标：%LOCALAPPDATA%\Programs\MiHomePC + 桌面/开始菜单快捷方式 + 卸载脚本
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest = Join-Path $env:LOCALAPPDATA 'Programs\MiHomePC'
$staging = $dest + '.new-' + $PID
$backup = $dest + '.old-' + $PID

Write-Host ''
Write-Host 'PC 控制端 —— 安装 / 升级到当前用户' -ForegroundColor Cyan
Write-Host ('  源目录: ' + $here)
Write-Host ('  目标:   ' + $dest)

# 只结束本程序自己的进程，不影响用户其他 Python 程序。
Get-Process -Name pythonw, python -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -like ($dest + '*') } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

# Windows 可能在进程退出后短暂保留目录锁；有限重试，仍失败则走回滚。
function Rename-WithRetry($path, $newName) {
  for ($attempt = 0; $attempt -lt 10; $attempt++) {
    try {
      Rename-Item -Path $path -NewName $newName -ErrorAction Stop
      return
    } catch {
      if ($attempt -eq 9) { throw }
      Start-Sleep -Milliseconds 500
    }
  }
}

# 先完整复制到同级暂存目录。复制失败时旧版仍可继续使用，不会只剩半个程序。
try {
  if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
  if (Test-Path $backup) { Remove-Item -Recurse -Force $backup }
  New-Item -ItemType Directory -Force -Path $staging | Out-Null
  Copy-Item -Path (Join-Path $here '*') -Destination $staging -Recurse -Force
  # 安装版不使用便携模式：标准数据目录是 %USERPROFILE%\.mihome-pc。
  $flag = Join-Path $staging 'portable.flag'
  if (Test-Path $flag) { Remove-Item -Force $flag }

  if (Test-Path $dest) {
    Write-Host '  检测到旧版本，备份后升级…'
    Rename-WithRetry $dest (Split-Path -Leaf $backup)
  }
  Rename-WithRetry $staging (Split-Path -Leaf $dest)

  # 极少数用户目录不可写的旧安装会把数据回退到 <安装目录>\data，升级必须带回来。
  $oldData = Join-Path $backup 'data'
  if (Test-Path $oldData) {
    Copy-Item -Path $oldData -Destination $dest -Recurse -Force
  }
} catch {
  # 切换任一步失败都尽量恢复旧目录；标准用户数据从未离开原位置。
  if ((Test-Path $backup) -and (Test-Path $dest)) {
    Remove-Item -Recurse -Force $dest -ErrorAction SilentlyContinue
  }
  if ((Test-Path $backup) -and (-not (Test-Path $dest))) {
    Rename-WithRetry $backup (Split-Path -Leaf $dest)
  }
  throw
} finally {
  if (Test-Path $staging) { Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue }
}

$pythonw = Join-Path $dest 'python38\\pythonw.exe'
$icon = Join-Path $dest 'mihome_pc\\assets\\mihome.ico'
if (-not (Test-Path $icon)) { $icon = Join-Path $dest 'mihome_pc\\static\\favicon.png' }

function New-Shortcut($path, $target, $arguments, $workdir, $iconPath) {
  $shell = New-Object -ComObject WScript.Shell
  $lnk = $shell.CreateShortcut($path)
  $lnk.TargetPath = $target
  $lnk.Arguments = $arguments
  $lnk.WorkingDirectory = $workdir
  if (Test-Path $iconPath) { $lnk.IconLocation = $iconPath }
  $lnk.Description = 'PC 控制端'
  $lnk.Save()
}

$desktop = [Environment]::GetFolderPath('Desktop')
$startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'PC 控制端'
New-Item -ItemType Directory -Force -Path $startMenu | Out-Null

# 同版本品牌更新也要清掉旧名称，避免桌面和开始菜单出现两个入口。
$legacyDesktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) '米家 PC.lnk'
$legacyStartFolder = Join-Path ([Environment]::GetFolderPath('Programs')) '米家 PC'
if (Test-Path $legacyDesktopLnk) { Remove-Item -Force $legacyDesktopLnk }
if (Test-Path $legacyStartFolder) { Remove-Item -Recurse -Force $legacyStartFolder }

New-Shortcut (Join-Path $desktop 'PC 控制端.lnk') $pythonw '-m mihome_pc app' $dest $icon
New-Shortcut (Join-Path $startMenu 'PC 控制端.lnk') $pythonw '-m mihome_pc app' $dest $icon
$unArgs = '-NoProfile -ExecutionPolicy Bypass -File ' + (Join-Path $dest 'uninstall.ps1')
New-Shortcut (Join-Path $startMenu '卸载 PC 控制端.lnk') 'powershell.exe' $unArgs $dest $icon

$uninstall = @'
# 卸载：删程序目录、桌面与开始菜单快捷方式（标准用户数据目录保留）
$dest = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'PC 控制端.lnk'
$startFolder = Join-Path ([Environment]::GetFolderPath('Programs')) 'PC 控制端'
$legacyDesktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) '米家 PC.lnk'
$legacyStartFolder = Join-Path ([Environment]::GetFolderPath('Programs')) '米家 PC'
Get-Process -Name pythonw, python -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -like ($dest + '*') } | Stop-Process -Force -ErrorAction SilentlyContinue
if (Test-Path $desktopLnk) { Remove-Item -Force $desktopLnk }
if (Test-Path $startFolder) { Remove-Item -Recurse -Force $startFolder }
if (Test-Path $legacyDesktopLnk) { Remove-Item -Force $legacyDesktopLnk }
if (Test-Path $legacyStartFolder) { Remove-Item -Recurse -Force $legacyStartFolder }
Write-Host '标准数据目录没有删除：%USERPROFILE%\.mihome-pc'
Start-Sleep -Seconds 2
Remove-Item -Recurse -Force $dest -ErrorAction SilentlyContinue
'@
Set-Content -Path (Join-Path $dest 'uninstall.ps1') -Value $uninstall -Encoding UTF8

if (Test-Path $backup) { Remove-Item -Recurse -Force $backup -ErrorAction SilentlyContinue }

Write-Host ''
Write-Host '安装 / 升级完成，用户数据已保留：' -ForegroundColor Green
Write-Host ('  桌面快捷方式: ' + (Join-Path $desktop 'PC 控制端.lnk'))
Write-Host ('  开始菜单:     ' + $startMenu)
Write-Host '  双击桌面图标即可启动（右下角出现托盘图标）'
Write-Host ''
if (-not $Quiet) { Read-Host '按回车键继续' }
"""

INSTALL_CMD = '''@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
'''


def log(message):
    print(message)


def hash_tree(root):
    lines = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if os.path.splitext(name)[1] in SKIP_SUFFIXES and name != "README.txt":
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root).replace("\\", "/")
            if rel.startswith("python38/"):
                continue                     # third-party stdlib: not ours to hash
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
            lines.append("%s  %s" % (digest.hexdigest(), rel))
    return lines


def copy_package():
    copy_project_package(ROOT, os.path.join(DIST, "mihome_pc"))


def copy_interpreter():
    target = os.path.join(DIST, "python38")
    if not os.path.isdir(EMBED):
        raise SystemExit("embedded interpreter missing: %s" % EMBED)
    if os.path.isdir(target):
        shutil.rmtree(target)
    shutil.copytree(EMBED, target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "Lib"))
    # rewrite the search path for the new layout, WITHOUT a BOM
    pth = os.path.join(target, "python38._pth")
    with open(pth, "w", encoding="ascii", newline="") as handle:
        handle.write(PTH_CONTENT)
    with open(pth, "rb") as handle:
        data = handle.read()
    if data.startswith(b"\xef\xbb\xbf"):
        raise SystemExit("._pth got a BOM -- the interpreter would not start")


def write_text(relative, content):
    path = os.path.join(DIST, relative)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(content)


def write_bytes(relative, data):
    path = os.path.join(DIST, relative)
    with open(path, "wb") as handle:
        handle.write(data)


def main(argv):
    if "--clean" in argv and os.path.isdir(DIST):
        shutil.rmtree(DIST)
    if not os.path.isdir(DIST):
        os.makedirs(DIST)

    log("copying package ...")
    copy_package()
    log("copying embedded interpreter ...")
    copy_interpreter()

    # Marker that flips the app into "portable" mode: all state lives beside
    # the executable instead of in %USERPROFILE%.\.mihome-pc.
    write_bytes("portable.flag", b"portable\n")

    log("writing launchers ...")
    write_bytes("_boot.pyw", BOOT_PYW.encode("utf-8"))
    write_bytes("mihome.vbs", LAUNCH_VBS.encode("ascii"))
    write_bytes("start-console.cmd", START_CMD.encode("utf-8"))
    write_bytes("README.txt", README_TXT.encode("utf-8"))
    # 中文使用须知给最终用户；README.txt 是英文的，给愿意读细节的人
    write_bytes("\u4f7f\u7528\u987b\u77e5.txt",
                USAGE_TXT.encode("utf-8-sig"))
    # 安装到当前用户：桌面快捷方式 + 开始菜单 + 卸载脚本（不需要管理员）
    write_bytes("install.ps1", INSTALL_PS1.encode("utf-8-sig"))
    write_bytes("\u5b89\u88c5\u5230\u684c\u9762.cmd", INSTALL_CMD.encode("ascii"))
    copy_legal_documents(ROOT, DIST)
    assert_tree_clean(DIST)

    log("hashing ...")
    manifest = hash_tree(DIST)
    header = ["# Mi Home PC %s distribution manifest" % VERSION,
              "# sha256  relative-path", ""]
    write_bytes("DIST-MANIFEST.txt", ("\n".join(header + manifest) + "\n").encode("utf-8"))

    total = 0
    count = 0
    for base, _dirs, files in os.walk(DIST):
        for name in files:
            total += os.path.getsize(os.path.join(base, name))
            count += 1
    log("")
    log("dist: %s" % DIST)
    log("files: %d, total %.1f MB (%d hashed)" % (count, total / 1048576.0, len(manifest)))

    log("zipping ...")
    import zipfile
    zip_path = os.path.join(ROOT, "dist", "pc-controller-%s-portable-win32.zip" % VERSION)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for base, dirs, files in os.walk(DIST):
            dirs[:] = sorted(d for d in dirs if d not in ("data", "logs", "__pycache__"))
            for name in sorted(files):
                if name.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(base, name)
                archive.write(full, os.path.join("mihome-pc", os.path.relpath(full, DIST)))
    log("zip: %s (%.1f MB)" % (zip_path, os.path.getsize(zip_path) / 1048576.0))
    log("")
    log("")
    log("smoke test:")
    log('  "%s" -m mihome_pc selftest' % os.path.join(DIST, "python38", "python.exe"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
