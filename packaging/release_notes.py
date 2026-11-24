"""Write ``dist/\u53d1\u5e03\u8bf4\u660e-<version>.md`` for a release.

The file is what a fork user sees next to the artifacts: which file to take,
how to install it, the sha256 to check, and what is *not* implemented.
Everything except the hashing is a pure function so it can be tested offline.
"""
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KNOWN_LIMITS = (
    "自动化：可以看/启停/改名/改动作，但**新建任意触发**还不行（服务端要 spec-scene 的 sc_id）。",
    "消息：没有常驻长连接，未读数每 45 秒轮询一次，不是实时推送。",
    "固件升级：只显示当前版本，不提供刷机。",
    "添加设备：按需求不做（扫码配网留给手机 App）。",
    "Win7：0.7.36 已在 Win7 SP1 x64／x86 虚拟机完成 Demo 实测（裸系统需先装 UCRT）；"
    "真实账号与设备、Chrome 109 未验证，详见 docs/win7-sp1-compatibility.md。",
)

# 每个版本在说明顶部写「为什么升级」；没有条目的版本就跳过这段。
HIGHLIGHTS = {
    "0.7.37": (
        "修复 issue #1：新装用户扫码登录成功后仍停在演示模式。"
        "0.7.36 及以前的进程只在启动时读一次凭据，必须重启程序才生效；现在扫码确认后自动切换，"
        "也不用再手动刷新。",
        "新增「账号与同步」→「退出登录」：只删本机凭据，不影响手机上的米家 App，"
        "退出后立刻回到演示模式，可直接换账号。",
    ),
}


def _human(size):
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return ("%d %s" % (size, unit)) if unit == "B" else ("%.1f %s" % (size, unit))
        size /= 1024.0
    return "%d B" % size


def digest(path):
    """sha256 of one file, chunked so a 9 MB zip is fine."""
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def collect(dist_dir, version):
    """``[(name, size, sha256)]`` for the artifacts of this version."""
    if not os.path.isdir(dist_dir):
        return []
    found = []
    for name in sorted(os.listdir(dist_dir)):
        if not name.startswith("mihome-pc") or version not in name:
            continue
        path = os.path.join(dist_dir, name)
        if not os.path.isfile(path):
            continue
        found.append((name, os.path.getsize(path), digest(path)))
    return found


def render(version, entries, limits=KNOWN_LIMITS, highlights=None):
    """The release note as markdown."""
    if highlights is None:
        highlights = HIGHLIGHTS.get(version, ())
    lines = [
        "# \u7c73\u5bb6 PC \u5ba2\u6237\u7aef %s \u53d1\u5e03\u8bf4\u660e" % version,
        "",
    ]
    if highlights:
        lines += ["## \u672c\u6b21\u91cd\u70b9", ""]
        lines += ["* " + item for item in highlights]
        lines.append("")
    lines += [
        ("\u672c\u6b21\u53d1\u5e03\u5305\u542b\u4e0b\u9762\u8fd9\u4e9b\u6587\u4ef6"
         "\uff08sha256 \u53ef\u7528\u4e8e\u6821\u9a8c\u4e0b\u8f7d"
         "\u662f\u5426\u5b8c\u6574\uff09\uff1a"),
        "",
        "| \u6587\u4ef6 | \u5927\u5c0f | sha256 |",
        "|---|---|---|",
    ]
    for name, size, sha in entries:
        lines.append("| `%s` | %s | `%s` |" % (name, _human(size), sha))
    if not entries:
        lines.append("| \uff08\u6ca1\u627e\u5230\u4ea7\u7269\uff0c"
                     "\u5148\u8dd1 build_win.py / build_deb.py\uff09 | \u2014 | \u2014 |")
    lines += [
        "",
        "## Windows\uff08\u7eff\u8272\u7248\uff09",
        "",
        "1. \u89e3\u538b `mihome-pc-%s-portable-win64.zip`\uff1b" % version,
        ("2. \u53cc\u51fb `mihome.vbs`\uff08\u6216 "
         "`\u5b89\u88c5\u5230\u684c\u9762.cmd` \u5efa\u5feb\u6377\u65b9\u5f0f\uff09\uff1b"),
        "3. \u7ec6\u8282\u770b\u76ee\u5f55\u91cc\u7684 `\u4f7f\u7528\u987b\u77e5.txt`\u3002",
        "",
        "## Windows\uff08\u5b89\u88c5\u7248\uff09",
        "",
        "`powershell -ExecutionPolicy Bypass -File install.ps1`\uff1b\u5b89\u5230",
        "`%LOCALAPPDATA%\\Programs\\MiHomePC`\uff0c\u684c\u9762\u4e0e\u5f00\u59cb\u83dc\u5355\u4f1a\u6709\u5feb\u6377\u65b9\u5f0f\uff0c\u5e26\u5378\u8f7d\u811a\u672c\u3002",
        "",
        "## Linux",
        "",
        ("`sudo dpkg -i mihome-pc_%s_all.deb`\uff0c\u6216\u76f4\u63a5\u89e3\u538b"
         % version),
        ("`mihome-pc-%s-linux.tar.gz` \u8fd0\u884c\uff08\u9700\u7cfb\u7edf Python 3.8+\uff09\u3002"
         % version),
        "",
        "## \u672c\u7248\u672c\u6ca1\u505a\u7684\u4e8b\uff08\u5148\u8bf4\u6e05\u695a\uff09",
        "",
    ]
    lines += ["* " + item for item in limits]
    lines += [
        "",
        ("\u5b8c\u6574\u53d8\u66f4\u89c1 CHANGELOG.md\uff0c"
         "\u529f\u80fd\u77e9\u9635\u89c1 docs/04-\u529f\u80fd\u5bf9\u7167.md\u3002"),
        "",
    ]
    return "\n".join(lines)


def main():
    sys.path.insert(0, ROOT)
    from mihome_pc import VERSION

    dist = os.path.join(ROOT, "dist")
    entries = collect(dist, VERSION)
    text = render(VERSION, entries)
    out = os.path.join(dist, "\u53d1\u5e03\u8bf4\u660e-%s.md" % VERSION)
    os.makedirs(dist, exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(text)
    print("wrote %s (%d artifacts)" % (out, len(entries)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
