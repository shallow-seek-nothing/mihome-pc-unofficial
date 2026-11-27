"""Adversarial regression tests for log/crash/report secret redaction."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
import zipfile

from mihome_pc import logs


PT = "PT_CANARY_1234567890"
ST = "ST_CANARY_1234567890"
SSEC = "SSEC_CANARY_1234567890"
TICKET = "URL_CANARY_999"
SHORT = "abc123"


class TestRedactionFormats(unittest.TestCase):
    def assert_clean(self, text, *secrets):
        result = logs.redact_text(text)
        for secret in secrets:
            self.assertNotIn(secret, result, "leaked in %r" % result)
        return result

    def test_python_dict_repr_and_short_values(self):
        self.assert_clean(
            "{'passToken': '%s', 'password': '%s'}" % (PT, SHORT), PT, SHORT)
        self.assert_clean("serviceToken=%s" % SHORT, SHORT)

    def test_real_cookie_alias_and_session_security(self):
        self.assert_clean(
            "Cookie: yetAnotherServiceToken=%s; userId=42" % ST, ST)
        self.assert_clean('{"sessionSecurity":"%s"}' % SSEC, SSEC)

    def test_login_ticket_and_common_token_aliases(self):
        self.assert_clean("https://example.invalid/cb?ticket=" + TICKET, TICKET)
        for key in ("accessToken", "refreshToken", "authToken", "sessionToken",
                    "access_token", "refresh_token"):
            with self.subTest(key=key):
                self.assert_clean("%s=%s" % (key, PT), PT)

    def test_percent_encoded_and_escaped_json_secrets(self):
        encoded = "PT%5FCANARY%5F1234567890"
        self.assert_clean("passToken=" + encoded, encoded)
        self.assert_clean(r'{\"passToken\":\"%s\"}' % PT, PT)

    def test_bearer_base64_padding_is_fully_masked(self):
        token = "abcdefghijklmnop+////=="
        result = self.assert_clean("Authorization: Bearer " + token, token)
        self.assertNotIn("+////==", result)

    def test_non_secret_diagnostics_stay_visible(self):
        result = logs.redact_text(
            "Cookie: serviceToken=%s; userId=42; code=-704010000; did=123" % ST)
        self.assertNotIn(ST, result)
        self.assertIn("userId=42", result)
        self.assertIn("code=-704010000", result)
        self.assertIn("did=123", result)


class TestCrashAndReportRedaction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(logs.shutdown)
        self.old_home = os.environ.get("MIHOME_PC_HOME")
        os.environ["MIHOME_PC_HOME"] = self.tmp.name
        self.addCleanup(self._restore_home)
        self.old_hook = sys.excepthook
        self.addCleanup(self._restore_hook)

    def _restore_home(self):
        if self.old_home is None:
            os.environ.pop("MIHOME_PC_HOME", None)
        else:
            os.environ["MIHOME_PC_HOME"] = self.old_home

    def _restore_hook(self):
        sys.excepthook = self.old_hook

    def test_crash_file_and_console_traceback_are_redacted(self):
        logs.install_excepthook()
        try:
            raise RuntimeError("{'passToken': '%s', 'ticket': '%s'}" % (PT, TICKET))
        except RuntimeError as exc:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                sys.excepthook(type(exc), exc, exc.__traceback__)
        crash_dir = os.path.join(self.tmp.name, "logs")
        names = [name for name in os.listdir(crash_dir) if name.startswith("crash-")]
        self.assertEqual(len(names), 1)
        with open(os.path.join(crash_dir, names[0]), encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn(PT, text)
        self.assertNotIn(TICKET, text)
        self.assertNotIn(PT, stderr.getvalue())
        self.assertNotIn(TICKET, stderr.getvalue())

    def test_report_redacts_preexisting_raw_and_rotated_logs(self):
        directory = os.path.join(self.tmp.name, "logs")
        os.makedirs(directory)
        samples = {
            "crash-old.log": "{'passToken': '%s', 'ticket': '%s'}" % (PT, TICKET),
            "mihome-pc.log.1": "yetAnotherServiceToken=%s\npassword=%s" % (ST, SHORT),
        }
        for name, text in samples.items():
            with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
                handle.write(text)
        target = os.path.join(self.tmp.name, "report.zip")
        logs.report(data_dir=self.tmp.name, target=target)
        with zipfile.ZipFile(target) as archive:
            blob = b"".join(archive.read(name) for name in archive.namelist())
        for secret in (PT, TICKET, ST, SHORT):
            self.assertNotIn(secret.encode("utf-8"), blob)


class TestWindowsBootRedaction(unittest.TestCase):
    def test_embedded_boot_tracebacks_use_redactor(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for filename in ("build_win.py", "build_win32.py"):
            with self.subTest(filename=filename):
                path = os.path.join(root, "packaging", filename)
                with open(path, encoding="utf-8") as handle:
                    build_source = handle.read()
                source = build_source.split("BOOT_PYW = '''", 1)[1].split("'''", 1)[0]
                self.assertNotIn("traceback.print_exc", source)
                namespace = {"__name__": "embedded_boot_test", "__file__": path}
                exec(compile(source, path, "exec"), namespace)
                try:
                    raise RuntimeError("{'passToken': '%s', 'ticket': '%s'}" % (PT, TICKET))
                except RuntimeError:
                    stderr = io.StringIO()
                    with contextlib.redirect_stderr(stderr):
                        namespace["_print_safe_exc"]()
                self.assertNotIn(PT, stderr.getvalue())
                self.assertNotIn(TICKET, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
