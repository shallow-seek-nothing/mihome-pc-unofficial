"""日志与诊断层：格式、请求 id、脱敏、滚动、诊断包。

这些断言存在的理由很实际：用户报 bug 时贴出来的就是这些行。格式一乱、
密钥没打码、或者请求 id 跟响应头对不上，排障就变成猜。
"""
import os
import re
import tempfile
import unittest

from mihome_pc import logs

LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}[+-]\d\d:\d\d) "
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) +"
    r"(?P<module>\S+) +(?P<req>\S+) +(?P<message>.*)$")


class LogsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Windows 上日志文件被 handler 占着时临时目录删不掉：先摘 handler 再清理
        self.addCleanup(logs.shutdown)
        self.data = self.tmp.name
        # 每个用例自己装配一遍：handler 跟着 data_dir 走，不会污染真实数据目录
        self.path = logs.setup(data_dir=self.data, console=False, level="debug")

    def tearDown(self):
        logs.shutdown()

    def lines(self):
        with open(self.path, encoding="utf-8") as handle:
            return [line.rstrip("\n") for line in handle if line.strip()]


class TestFormat(LogsTestCase):
    def test_one_line_is_self_contained(self):
        logs.get("web").info("GET /api/devices -> 200 in 42ms")
        line = self.lines()[-1]
        match = LINE_RE.match(line)
        self.assertIsNotNone(match, "格式不对: %r" % line)
        self.assertEqual(match.group("level"), "INFO")
        self.assertEqual(match.group("module"), "web")
        self.assertEqual(match.group("req"), "-")
        self.assertIn("GET /api/devices -> 200 in 42ms", match.group("message"))

    def test_timestamp_has_milliseconds_and_offset(self):
        logs.get("app").info("hello")
        stamp = LINE_RE.match(self.lines()[-1]).group("ts")
        self.assertRegex(stamp, r"\.\d{3}[+-]\d\d:\d\d$")

    def test_level_name_is_recorded(self):
        log = logs.get("cloud")
        log.warning("call miotspec/prop/set code=-704010000")
        line = self.lines()[-1]
        self.assertIn("WARNING", line)
        self.assertIn("code=-704010000", line, "错误码必须原样保留")

    def test_module_tag_is_short_name(self):
        logs.get("web").info("x")
        self.assertEqual(LINE_RE.match(self.lines()[-1]).group("module"), "web")


class TestRequestScope(LogsTestCase):
    def test_request_id_appears_inside_scope_and_clears_after(self):
        with logs.request_scope() as rid:
            self.assertEqual(len(rid), 8)
            logs.get("web").info("inside")
        logs.get("web").info("outside")
        inside, outside = self.lines()[-2:]
        self.assertIn(rid, inside)
        self.assertEqual(LINE_RE.match(outside).group("req"), "-")

    def test_explicit_request_id_is_reused(self):
        with logs.request_scope("deadbeef"):
            logs.get("web").info("x")
        self.assertIn("deadbeef", self.lines()[-1])

    def test_nested_scope_restores_outer(self):
        with logs.request_scope("outer01"):
            with logs.request_scope("inner02"):
                logs.get("web").info("inner")
            logs.get("web").info("outer")
        inner, outer = self.lines()[-2:]
        self.assertIn("inner02", inner)
        self.assertIn("outer01", outer)


class TestRedaction(LogsTestCase):
    def test_service_token_is_masked(self):
        secret = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
        logs.get("cloud").info("call /app/v2/x serviceToken=%s", secret)
        line = self.lines()[-1]
        self.assertNotIn(secret, line)
        self.assertIn("a1b2…(32)", line)

    def test_json_pass_token_is_masked(self):
        secret = "0123456789abcdef0123456789abcdef"
        logs.get("web").info('payload {"passToken": "%s", "userId": 42}', secret)
        line = self.lines()[-1]
        self.assertNotIn(secret, line)
        self.assertIn('"userId": 42', line, "非密钥字段不能被连带打掉")

    def test_cookie_header_is_masked(self):
        logs.get("auth").info("Cookie: serviceToken=%s; userId=42",
                              "ffffffffffffffffffffffffffffffff")
        line = self.lines()[-1]
        self.assertNotIn("ffffffffffffffffffffffffffffffff", line)
        self.assertIn("userId=42", line)

    def test_error_codes_are_not_redacted(self):
        logs.get("web").info('{"code": -2001, "message": "invalid"}')
        line = self.lines()[-1]
        self.assertIn("-2001", line)

    def test_mask_value_is_stable(self):
        self.assertEqual(logs.mask_value("abcdef"), "****")
        self.assertEqual(logs.mask_value("abcdefghij"), "abcd…(10)")


class TestSetup(LogsTestCase):
    def test_setup_is_idempotent(self):
        import logging
        root = logging.getLogger(logs.ROOT_NAME)
        logs.setup(data_dir=self.data, console=False, level="debug")
        handlers = [h for h in root.handlers
                    if isinstance(h, logging.handlers.RotatingFileHandler)]
        self.assertEqual(len(handlers), 1)

    def test_log_files_rotate(self):
        path = logs.setup(data_dir=self.data, console=False, level="debug",
                          max_bytes=400, backup_count=2)
        log = logs.get("web")
        for index in range(60):
            log.info("line %03d %s", index, "x" * 40)
        names = os.listdir(os.path.dirname(path))
        self.assertIn(os.path.basename(path), names)
        self.assertTrue(any(name.endswith(".1") for name in names),
                        "超过 max_bytes 应该滚出 .1：%s" % names)

    def test_level_can_be_raised(self):
        logs.setup(data_dir=self.data, console=False, level="warning")
        logs.get("web").info("hidden")
        logs.get("web").error("shown")
        text = "\n".join(self.lines())
        self.assertNotIn("hidden", text)
        self.assertIn("shown", text)


class TestEnvironmentAndReport(LogsTestCase):
    def test_environment_lines_are_fact_only(self):
        rows = logs.environment_lines()
        text = "\n".join(rows)
        self.assertIn("version:", text)
        self.assertIn("python:", text)
        self.assertIn("platform:", text)
        # 只允许出现"有没有凭据"，不能出现凭据内容
        self.assertNotIn("serviceToken", text)
        self.assertNotIn("passToken", text)

    def test_report_zip_has_logs_and_environment(self):
        logs.get("web").info("something happened")
        target = os.path.join(self.data, "report.zip")
        path = logs.report(data_dir=self.data, target=target)
        import zipfile
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            self.assertIn("environment.txt", names)
            self.assertTrue(any(name.startswith("logs/") for name in names),
                            "诊断包必须带日志：%s" % names)
            text = archive.read("environment.txt").decode("utf-8")
            self.assertIn("version:", text)

    def test_report_never_contains_credentials(self):
        with open(os.path.join(self.data, "credentials.json"), "w", encoding="utf-8") as handle:
            handle.write('{"serviceToken": "0123456789abcdef0123456789abcdef"}')
        logs.get("web").info("x")
        target = os.path.join(self.data, "report.zip")
        path = logs.report(data_dir=self.data, target=target)
        import zipfile
        with zipfile.ZipFile(path) as archive:
            blob = b"".join(archive.read(name) for name in archive.namelist())
        self.assertNotIn(b"0123456789abcdef", blob,
                         "诊断包绝不能带上凭据文件的内容")


if __name__ == "__main__":
    unittest.main()
