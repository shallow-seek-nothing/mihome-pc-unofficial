"""统一的日志与诊断层（零第三方依赖）。

为什么要自己写一层而不是各处 ``print``：

* **一条日志要能独立看明白**。格式固定成

      2026-11-26T19:20:01.123+0800 INFO  web      8f2c1a  GET /api/devices -> 200 in 42ms

  时间（ISO8601、带毫秒和时区）/ 级别 / 模块 / 请求 id / 正文。
  用户把一行贴进 issue，开发就能直接定位，不用再问「哪台机器、什么时候、哪个请求」。

* **密钥绝不能进日志**。``serviceToken`` / ``passToken`` / ``ssecurity`` /
  ``Cookie`` / 设备 ``token`` 一律在写盘前打码（见 :func:`redact_text`）。
  日志是给人贴出去的，这一条没有例外。

* **一个请求一条线**。HTTP 层每个请求生成 ``req=xxxxxxxx``，同一线程内的
  云端调用、异常、耗时都自动带同一个 id；响应的 ``X-Request-Id`` 头和
  JSON 里的 ``request_id`` 是同一个值，排障时能对上。

* **日志要能滚动、能打包**。``RotatingFileHandler``（默认 2 MiB × 5），
  :func:`report` 把日志与**不含密钥**的环境信息打成 zip，方便附到 issue。

用法::

    from . import logs
    logs.setup()                       # 进程启动时调用一次（幂等）
    log = logs.get("web")              # 之后各模块用自己的 logger
    with logs.request_scope():
        log.info("GET %s -> %d in %dms", path, status, ms)
"""
import contextlib
import datetime
import io
import logging
import logging.handlers
import os
import platform
import re
import sys
import threading
import traceback
import uuid
import zipfile

__all__ = [
    "LEVELS", "setup", "get", "log_path", "log_dir", "current_level",
    "new_request_id", "request_scope", "request_id", "redact_text",
    "mask_value", "environment_lines", "report", "install_excepthook",
]

LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

DEFAULT_LEVEL = "info"
MAX_BYTES = 2 * 1024 * 1024          # 2 MiB / 个文件
BACKUP_COUNT = 5                     # web.log.1 .. .5
LOG_NAME = "mihome-pc.log"
FORMAT = "%(asctime)s %(levelname)-5s %(module_tag)-8s %(request_id)-8s %(message)s"
ROOT_NAME = "mihome_pc"

_local = threading.local()
_lock = threading.Lock()
_state = {"level": None, "dir": None, "handler": None, "signature": None}

# import 这个模块本身不该产生任何副作用（尤其不该建文件）
logging.getLogger(ROOT_NAME).addHandler(logging.NullHandler())


# --------------------------------------------------------------------------
# request id
# --------------------------------------------------------------------------

def new_request_id():
    """短、可读、够区分：8 位十六进制（同一秒里也几乎不会撞）。"""
    return uuid.uuid4().hex[:8]


def request_id():
    """当前线程正在处理的请求 id，没有就是 ``""``。"""
    return getattr(_local, "request_id", "") or ""


@contextlib.contextmanager
def request_scope(rid=None):
    """在这个上下文里打的日志都带 ``rid``；退出时恢复上一层（支持嵌套）。"""
    previous = getattr(_local, "request_id", "")
    _local.request_id = rid or new_request_id()
    try:
        yield _local.request_id
    finally:
        _local.request_id = previous


# --------------------------------------------------------------------------
# 脱敏
# --------------------------------------------------------------------------

# 形如 "serviceToken":"xxxx" / serviceToken=xxxx 的给打码；Bearer 也顺手处理。
_SECRET_KEYS = (
    "serviceToken", "passToken", "ssecurity", "signature",
    "cookie", "password", "passwd", "token", "uid_token",
)
_KEYS_RE = "|".join(_SECRET_KEYS)
# JSON："serviceToken": "xxxxx"
_JSON_RE = re.compile(r'(?i)("(?:%s)"\s*:\s*")([^"]{6,})(")' % _KEYS_RE)
# form / 查询串：serviceToken=xxxxx（HttpOnly cookie 里的 userId=42 这类不受影响）
_KV_RE = re.compile(r'(?i)\b((?:%s)=)([^\s&",;\)\]}]{6,})' % _KEYS_RE)
# Authorization: Bearer xxxxx
_BEARER_RE = re.compile(r"(?i)\b(Bearer\s+)([A-Za-z0-9._\-]{16,})")
# 32 位以上的十六进制串（米家 token 是 32 位）——只有在上面那些键附近才打码，
# 否则会误伤 md5 / sha 这类无辜的十六进制。
_HEXISH = re.compile(r"^[0-9a-fA-F]{16,}$")


def mask_value(value):
    """``abcdef...`` -> ``abcd…(24)``：够判断是哪一把，又不可复用。"""
    text = "" if value is None else str(value)
    if len(text) <= 8:
        return "****"
    return "%s…(%d)" % (text[:4], len(text))


def _already_masked(value):
    return value.startswith("*") or "…(" in value


def redact_text(text):
    """把一段文本里能识别出的密钥换成 ``mask_value`` 的形式。

    顺序固定为 JSON → 键值对 → Bearer，三条互不重叠，不会二次包裹。
    """
    if not text:
        return text
    result = str(text)

    def _json_sub(match):
        if _already_masked(match.group(2)):
            return match.group(0)
        return "%s%s%s" % (match.group(1), mask_value(match.group(2)), match.group(3))

    def _kv_sub(match):
        value = match.group(2)
        if _already_masked(value):
            return match.group(0)
        if not _HEXISH.match(value) and len(value) < 16:
            return match.group(0)          # 短值大概率不是密钥，别动
        return "%s%s" % (match.group(1), mask_value(value))

    result = _JSON_RE.sub(_json_sub, result)
    result = _KV_RE.sub(_kv_sub, result)
    result = _BEARER_RE.sub(
        lambda match: "%s%s" % (match.group(1), mask_value(match.group(2))), result)
    return result


# --------------------------------------------------------------------------
# logging 装配
# --------------------------------------------------------------------------

class _ContextFilter(logging.Filter):
    """补齐 ``module_tag`` / ``request_id`` 两个字段。

    脱敏不在这里做：此刻 ``record.args`` 还是惰性格式化的参数，动了就会丢消息
    （踩过：``info("...serviceToken=%s", tok)`` 直接把 token 参数吞掉）。
    统一放到 :class:`_Formatter` 里对最终字符串做。
    """

    def filter(self, record):
        try:
            record.module_tag = record.name.rsplit(".", 1)[-1][:8]
        except Exception:                                    # pragma: no cover
            record.module_tag = "?"
        record.request_id = request_id() or "-"
        return True


class _Formatter(logging.Formatter):
    """ISO8601 + 毫秒 + 时区偏移；整行输出前统一脱敏。"""

    def formatTime(self, record, datefmt=None):        # noqa: N802 - stdlib 名
        stamp = datetime.datetime.fromtimestamp(record.created).astimezone()
        return stamp.isoformat(timespec="milliseconds")

    def format(self, record):
        return redact_text(super().format(record))


def log_dir(data_dir=None):
    if data_dir is None:
        from .cli import data_dir as _data_dir
        data_dir = _data_dir()
    return os.path.join(data_dir, "logs")


def log_path(data_dir=None):
    return os.path.join(log_dir(data_dir), LOG_NAME)


def _resolve_level(level=None):
    if level is None:
        level = os.environ.get("MIHOME_PC_LOG_LEVEL") or DEFAULT_LEVEL
    if isinstance(level, int):
        return level
    return LEVELS.get(str(level).strip().lower(), logging.INFO)


def setup(level=None, data_dir=None, console=None, max_bytes=MAX_BYTES,
          backup_count=BACKUP_COUNT):
    """装配一次（重复调用只改级别，不会重复挂 handler）。

    ``console``：``None`` 表示自动判断（stdout 是真终端就顺带打到 stderr；
    GUI / pythonw 下不打开，避免 ``sys.stdout is None`` 带来的噪声）。
    """
    resolved = _resolve_level(level)
    root = logging.getLogger(ROOT_NAME)
    root.setLevel(resolved)
    root.propagate = False
    _state["level"] = resolved

    with _lock:
        path = log_path(data_dir)
        _state["dir"] = os.path.dirname(path)
        if console is None:
            console = _console_available()
        # 参数变了也要重建，否则测试里 max_bytes 永远沿用进程第一次的值
        signature = (path, int(max_bytes), int(backup_count))
        need_file = _state.get("signature") != signature
        if need_file:
            directory = os.path.dirname(path)
            with contextlib.suppress(OSError):
                if not os.path.isdir(directory):
                    os.makedirs(directory)
            for handler in list(root.handlers):
                if isinstance(handler, logging.handlers.RotatingFileHandler):
                    root.removeHandler(handler)
                    with contextlib.suppress(Exception):
                        handler.close()
            try:
                handler = logging.handlers.RotatingFileHandler(
                    path, maxBytes=max_bytes, backupCount=backup_count,
                    encoding="utf-8", delay=True)
                handler.setFormatter(_Formatter(FORMAT, datefmt=None))
                handler.addFilter(_ContextFilter())
                handler.setLevel(logging.DEBUG)
                root.addHandler(handler)
                _state["handler"] = handler
                _state["signature"] = signature
            except OSError:                 # 数据目录只读时不要炸掉整个程序
                _state["handler"] = None
        if console:
            if not any(isinstance(h, logging.StreamHandler)
                       and not isinstance(h, logging.handlers.RotatingFileHandler)
                       for h in root.handlers):
                stream = logging.StreamHandler(stream=sys.stderr)
                stream.setFormatter(_Formatter(FORMAT, datefmt=None))
                stream.addFilter(_ContextFilter())
                root.addHandler(stream)
        else:
            for handler in list(root.handlers):
                if not isinstance(handler, logging.handlers.RotatingFileHandler):
                    root.removeHandler(handler)
    return path


def _console_available():
    try:
        stream = sys.stderr
        return bool(stream) and hasattr(stream, "isatty") and stream.isatty()
    except Exception:                                        # pragma: no cover
        return False


def shutdown():
    """关闭并摘下文件 handler。

    单测（Windows 上临时目录删不掉被占用的日志文件）和程序正常退出都用它。
    """
    root = logging.getLogger(ROOT_NAME)
    with _lock:
        for handler in list(root.handlers):
            if isinstance(handler, logging.handlers.RotatingFileHandler):
                root.removeHandler(handler)
                with contextlib.suppress(Exception):
                    handler.close()
        _state["handler"] = None
        _state["signature"] = None


def get(name):
    """取一个子 logger；``name`` 只写模块名（``web`` / ``cloud`` / ``app``）。

    注意这里**不会**自动 ``setup()``：装配只由入口（``cli.main`` / ``serve`` /
    ``app.run``）负责，否则单测里随便 import 一下就会往真实数据目录写日志。
    没装配时消息被 NullHandler 吞掉，不会报 "no handlers"。
    """
    return logging.getLogger("%s.%s" % (ROOT_NAME, name))


def current_level():
    level = _state.get("level")
    for name, value in LEVELS.items():
        if value == level and name != "warn":
            return name
    return DEFAULT_LEVEL


# --------------------------------------------------------------------------
# 环境 / 崩溃 / 打包
# --------------------------------------------------------------------------

def environment_lines():
    """排障要的第一屏信息；**只含事实，不含任何凭据内容**。"""
    from . import VERSION
    rows = [
        ("version", VERSION),
        ("python", "%s (%s)" % (platform.python_version(), platform.architecture()[0])),
        ("platform", "%s %s" % (platform.system(), platform.release())),
        ("machine", platform.machine()),
        ("timestamp", datetime.datetime.now().astimezone().isoformat(timespec="seconds")),
    ]
    with contextlib.suppress(Exception):
        from .cli import data_dir
        directory = data_dir()
        rows.append(("data_dir", directory))
        rows.append(("log_file", log_path(directory)))
        rows.append(("log_level", current_level()))
        rows.append(("credentials", "present" if _credentials_present(directory) else "absent"))
    return ["%s: %s" % pair for pair in rows]


def _credentials_present(directory):
    return os.path.isfile(os.path.join(directory, "credentials.json"))


def install_excepthook():
    """未捕获异常落到 ``logs/crash-<时间>.log``（pythonw 下否则什么都看不到）。"""
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        try:
            text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            directory = log_dir()
            if not os.path.isdir(directory):
                os.makedirs(directory)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            path = os.path.join(directory, "crash-%s.log" % stamp)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(environment_lines()))
                handle.write("\n\n")
                handle.write(text)
            get("crash").error("uncaught %s: %s (详情 %s)",
                               exc_type.__name__, exc_value, path)
        except Exception:                                    # pragma: no cover
            pass
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def report(data_dir=None, target=None):
    """把日志 + 环境信息打成 zip（**不含凭据、不含设备 token**）。

    用户报 bug 时只要发这一个文件：里面有版本、系统、日志滚动档、崩溃档。
    """
    directory = log_dir(data_dir)
    if target is None:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        base = data_dir if data_dir is not None else os.path.dirname(directory)
        target = os.path.join(base, "mihome-pc-report-%s.zip" % stamp)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("environment.txt", "\n".join(environment_lines()) + "\n")
        if os.path.isdir(directory):
            for name in sorted(os.listdir(directory)):
                full = os.path.join(directory, name)
                if os.path.isfile(full) and (name.endswith(".log") or ".log." in name):
                    with contextlib.suppress(OSError):
                        archive.write(full, "logs/" + name)
    return target


# 让 `python -m mihome_pc report` 这类调用在没 setup 时也能拿到日志路径
def _ensure():
    if _state.get("handler") is None:
        setup()


if __name__ == "__main__":                       # pragma: no cover - 手工冒烟
    setup(level="debug", console=True)
    with request_scope():
        get("demo").info("hello %s", "world")
    print(open(log_path(), encoding="utf-8").read().strip())
