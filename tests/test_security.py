"""Security regression tests (external review, 0.7.26 findings).

Every attack here goes over a real socket with real headers, because the whole
point is that the *HTTP surface* must refuse -- calling handler methods directly
would not prove anything.
"""
import http.client
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mihome_pc.web import server as web_server  # noqa: E402
from mihome_pc.xiaomi import auth  # noqa: E402
from test_web import ServerFixture  # noqa: E402


def request(fixture, method, path, headers=None, body=None):
    """Raw request so the test controls Host/Origin/Content-Type exactly."""
    conn = http.client.HTTPConnection(fixture.host, fixture.port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


class SecurityCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._original_data_dir = web_server._data_dir
        web_server._data_dir = lambda: cls.tmp.name
        cls.fixture = ServerFixture()
        cls.fixture.start()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.stop()
        web_server._data_dir = cls._original_data_dir
        cls.tmp.cleanup()


class TestStaticEscape(SecurityCase):
    def setUp(self):
        self.canary_dir = tempfile.TemporaryDirectory()
        self.canary = os.path.join(self.canary_dir.name, 'canary.txt')
        with open(self.canary, 'w', encoding='utf-8') as handle:
            handle.write('CANARY_STATIC')

    def tearDown(self):
        self.canary_dir.cleanup()

    def assert_refused(self, path):
        status, body = request(self.fixture, 'GET', path)
        self.assertNotEqual(status, 200, '%s must not be served' % path)
        self.assertNotIn(b'CANARY_STATIC', body)

    def drive_path(self):
        return os.path.abspath(self.canary).replace('\\', '/')

    def test_windows_absolute_path(self):
        self.assert_refused('/static/' + self.drive_path())

    def test_url_encoded_absolute_path(self):
        encoded = self.drive_path().replace(':', '%3A').replace('/', '%2F')
        self.assert_refused('/static/' + encoded)

    def test_dotdot(self):
        self.assert_refused('/static/../mihome_pc/__init__.py')

    def test_encoded_dotdot(self):
        self.assert_refused('/static/%2E%2E/mihome_pc/__init__.py')

    def test_encoded_backslash(self):
        self.assert_refused('/static/..%5Cmihome_pc%5C__init__.py')

    def test_unc(self):
        self.assert_refused('/static//server/share/canary.txt')

    def test_symlink_out_of_tree(self):
        link = os.path.join(web_server.STATIC_DIR, 'evil-link.txt')
        try:
            if os.path.exists(link):
                os.remove(link)
            os.symlink(self.canary, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest('creating symlinks needs privileges on this platform')
        try:
            self.assert_refused('/static/evil-link.txt')
        finally:
            os.remove(link)

    def test_legit_assets_still_work(self):
        status, body = request(self.fixture, 'GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'<!DOCTYPE html>', body)
        status, _body = request(self.fixture, 'GET', '/static/app.js')
        self.assertEqual(status, 200)


class TestRequestOrigin(SecurityCase):
    def test_foreign_host_is_rejected(self):
        status, _body = request(self.fixture, 'GET', '/api/status',
                                {'Host': 'attacker.invalid'})
        self.assertEqual(status, 403)

    def test_wrong_port_is_rejected(self):
        status, _body = request(self.fixture, 'GET', '/api/status',
                                {'Host': '127.0.0.1:1'})
        self.assertEqual(status, 403)

    def test_cross_origin_write_is_rejected(self):
        status, _body = request(
            self.fixture, 'POST', '/api/favorites/toggle',
            {'Origin': 'http://evil.example',
             'Content-Type': 'application/json'},
            json.dumps({'did': 'demo-washer'}))
        self.assertEqual(status, 403)

    def test_null_origin_is_rejected(self):
        status, _body = request(self.fixture, 'GET', '/api/status',
                                {'Origin': 'null'})
        self.assertEqual(status, 403)

    def test_cross_site_fetch_metadata_is_rejected(self):
        status, _body = request(self.fixture, 'GET', '/api/status',
                                {'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(status, 403)

    def test_text_plain_write_without_origin_is_rejected(self):
        status, _body = request(
            self.fixture, 'POST', '/api/favorites/toggle',
            {'Content-Type': 'text/plain'}, '{"did": "demo-washer"}')
        self.assertEqual(status, 415)

    def test_oversized_body_is_rejected_before_reading(self):
        # 只发头不发体：服务器应该在读 body 之前就拒绝，
        # 不然客户端写 1 MiB 时连接会被中断（那反而是正确行为，但断言不上）。
        conn = http.client.HTTPConnection(self.fixture.host, self.fixture.port,
                                          timeout=10)
        try:
            conn.putrequest('POST', '/api/favorites/toggle')
            conn.putheader('Content-Type', 'application/json')
            conn.putheader('Content-Length',
                           str(web_server.MAX_BODY_BYTES + 10))
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            conn.close()

    def test_attack_does_not_change_state(self):
        before = list(self.fixture.provider.favorites()['dids'])
        request(self.fixture, 'POST', '/api/favorites/toggle',
                {'Origin': 'http://evil.example',
                 'Content-Type': 'application/json'},
                json.dumps({'did': 'demo-washer'}))
        after = list(self.fixture.provider.favorites()['dids'])
        self.assertEqual(before, after)

    def test_same_origin_write_is_allowed(self):
        origin = 'http://127.0.0.1:%d' % self.fixture.port
        status, body = request(
            self.fixture, 'POST', '/api/favorites/toggle',
            {'Origin': origin, 'Content-Type': 'application/json',
             'Sec-Fetch-Site': 'same-origin'},
            json.dumps({'did': 'demo-dishwasher'}))
        self.assertEqual(status, 200, body)
        # put it back
        request(self.fixture, 'POST', '/api/favorites/toggle',
                {'Origin': origin, 'Content-Type': 'application/json'},
                json.dumps({'did': 'demo-dishwasher'}))

    def test_malformed_origin_is_rejected_not_dropped(self):
        # 这三种输入会让 urlsplit/.port 抛 ValueError；必须是 403，
        # 而不是把连接直接弄断（那样客户端只看到 RemoteDisconnected）。
        for origin in ('http://127.0.0.1:bad', 'http://127.0.0.1:99999',
                       'http://[::1'):
            status, body = request(self.fixture, 'GET', '/api/status',
                                    {'Origin': origin})
            self.assertEqual(status, 403, '%s -> %r' % (origin, body))

    def test_empty_text_plain_post_is_rejected(self):
        # 即使 Content-Length 是 0，类型也必须是 application/json
        status, _body = request(self.fixture, 'POST', '/api/messages/read',
                                {'Content-Type': 'text/plain',
                                 'Content-Length': '0'}, '')
        self.assertEqual(status, 415)

    def test_text_plain_post_without_content_length_is_rejected(self):
        # 这是上一轮漏掉的分支：没有 Content-Length 也必须是 JSON。
        conn = http.client.HTTPConnection(self.fixture.host, self.fixture.port,
                                          timeout=10)
        try:
            conn.putrequest('POST', '/api/messages/read')
            conn.putheader('Content-Type', 'text/plain')
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 415)
            response.read()
        finally:
            conn.close()

    def test_json_post_without_content_length_still_works(self):
        # 没有 body、没有 Content-Length，但类型对 → 按空 body 处理
        conn = http.client.HTTPConnection(self.fixture.host, self.fixture.port,
                                          timeout=10)
        try:
            conn.putrequest('POST', '/api/messages/read')
            conn.putheader('Content-Type', 'application/json')
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
        finally:
            conn.close()

    def test_duplicate_host_header_is_rejected(self):
        conn = http.client.HTTPConnection(self.fixture.host, self.fixture.port,
                                          timeout=10)
        try:
            conn.putrequest('GET', '/api/status', skip_host=True)
            conn.putheader('Host', '127.0.0.1')
            conn.putheader('Host', 'attacker.invalid')
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            conn.close()

    def test_cli_json_post_without_origin_is_allowed(self):
        status, _body = request(self.fixture, 'POST', '/api/messages/read',
                                {'Content-Type': 'application/json'}, '{}')
        self.assertEqual(status, 200)


class TestLoginSecrets(unittest.TestCase):
    """登录脱敏：这里是单元级（注入异常），不经过 HTTP。

    弱点与保护对应关系在 `docs/安全修复报告` 里写清楚：不过 HTTP 也不代表不重要。
    """

    PT = 'PT_CANARY_1234567890'
    ST = 'ST_CANARY_1234567890'
    SSEC = 'SSEC_CANARY_1234567890'
    URL = 'https://account.example/login?ticket=URL_CANARY_999'

    def setUp(self):
        # 异常：日志断言会读 logs/web.log，不隔离就会写进真实用户目录、
        # 且结果受已有日志影响（不 hermetic）。
        self.tmp = tempfile.TemporaryDirectory()
        self._original_data_dir = web_server._data_dir
        web_server._data_dir = lambda: self.tmp.name

    def tearDown(self):
        web_server._data_dir = self._original_data_dir
        self.tmp.cleanup()

    def test_missing_location_does_not_echo_payload(self):
        response = auth.Response('u', json.dumps({'passToken': self.PT}), {})
        login = auth.QrLogin(transport=_FakeTransport())
        login.service = None
        with self.assertRaises(auth.LoginError) as caught:
            login._credentials_from_login({'passToken': self.PT,
                                           'ssecurity': self.SSEC}, response)
        self.assertNotIn(self.PT, str(caught.exception))
        self.assertNotIn(self.SSEC, str(caught.exception))

    def test_missing_ssecurity_does_not_echo_payload(self):
        response = auth.Response('u', '{}', {})
        login = auth.QrLogin(transport=_FakeTransport())
        with self.assertRaises(auth.LoginError) as caught:
            login._credentials_from_login({'location': self.URL}, response)
        self.assertNotIn('URL_CANARY', str(caught.exception))

    def test_qr_payload_never_appears_in_error(self):
        transport = _FakeTransport(payload={'code': 0, 'passToken': self.PT,
                                             'ticket': 'URL_CANARY'})
        login = auth.QrLogin(transport=transport)
        with self.assertRaises(auth.LoginError) as caught:
            login.request_qr()
        self.assertNotIn(self.PT, str(caught.exception))

    def test_real_transport_error_hides_url(self):
        # 真实请求一个关着的本机端口，错误里不能带 URL（它可能带 ticket）
        transport = auth.Transport()
        with self.assertRaises(auth.LoginError) as caught:
            transport.get('http://127.0.0.1:9/login?ticket=URL_CANARY_999')
        self.assertNotIn('URL_CANARY', str(caught.exception))
        self.assertNotIn('127.0.0.1', str(caught.exception))

    def test_login_flow_never_surfaces_exception_text(self):
        secrets = (self.PT, self.ST, self.SSEC, 'URL_CANARY_999')

        class Boom(auth.QrLogin):
            def __init__(self, *args, **kwargs):
                auth.QrLogin.__init__(self, transport=_FakeTransport())

            def request_qr(self):
                raise auth.LoginError('boom %s %s %s %s' % secrets)

        original = auth.QrLogin
        auth.QrLogin = Boom
        tmp = tempfile.TemporaryDirectory()
        try:
            flow = web_server.LoginFlow(os.path.join(tmp.name, 'credentials.json'))
            flow._run(flow._generation)
            self.assertEqual(flow.state, 'error')
            status = flow.status()
            log_path = os.path.join(web_server._data_dir(), 'logs', 'web.log')
            log_text = ''
            if os.path.isfile(log_path):
                with open(log_path, encoding='utf-8') as handle:
                    log_text = handle.read()
            for secret in secrets:
                self.assertNotIn(secret, flow.message)
                self.assertNotIn(secret, json.dumps(status, ensure_ascii=False))
                self.assertNotIn(secret, log_text)
        finally:
            auth.QrLogin = original
            tmp.cleanup()

    def test_credentials_repr_has_no_token(self):
        creds = auth.Credentials(user_id='1', service_token=self.ST,
                                 ssecurity=self.SSEC, pass_token=self.PT)
        text = repr(creds)
        for secret in (self.ST, self.ST[:8], self.SSEC, self.PT):
            self.assertNotIn(secret, text)


class _FakeTransport:
    def __init__(self, payload=None, raise_reason=None):
        self.payload = payload if payload is not None else {}
        self.raise_reason = raise_reason

    def get(self, url, data=None, headers=None, timeout=None):
        if self.raise_reason:
            raise auth.LoginError('network error: %s' % self.raise_reason)
        return auth.Response(url, json.dumps(self.payload), {})


if __name__ == '__main__':
    unittest.main()
