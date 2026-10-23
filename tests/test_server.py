"""The local server: who may ask (session key, Host, Origin) and that the page and a route answer."""
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request

import _path  # noqa: F401
from monkap import server


def fetch(url, headers=None, data=None):
    req = urllib.request.Request(url, headers=headers or {}, data=data)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    op = urllib.request.build_opener(NoRedirect)
    try:
        r = op.open(req, timeout=5)
        return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["MONKAP_HOME"] = cls.home.name
        cls.srv, cls.url = server.start(18770, token="k" * 20)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.cookie = {"Cookie": f"{server.COOKIE}={'k' * 20}"}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        os.environ.pop("MONKAP_HOME", None)
        cls.home.cleanup()

    def test_the_address_carries_the_key_and_sets_a_cookie_once(self):
        code, _, h = fetch(self.url)
        self.assertEqual(code, 302)
        self.assertIn("HttpOnly", h["Set-Cookie"])
        self.assertIn("SameSite=Strict", h["Set-Cookie"])

    def test_no_key_no_page_and_no_api(self):
        self.assertEqual(fetch(self.base + "/")[0], 401)
        self.assertEqual(fetch(self.base + "/api/state")[0], 401)
        self.assertEqual(fetch(self.base + "/?t=wrong")[0], 403)

    def test_page_and_api_with_the_key(self):
        code, body, h = fetch(self.base + "/", self.cookie)
        self.assertEqual(code, 200)
        self.assertIn(b"MonKap", body)
        self.assertEqual(h["Cache-Control"], "no-store")
        code, body, _ = fetch(self.base + "/api/state", self.cookie)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["settings"]["delete_mode"], "soft")

    def test_another_host_or_origin_is_refused(self):
        self.assertEqual(fetch(self.base + "/api/state", {**self.cookie, "Host": "evil.example"})[0], 403)
        self.assertEqual(fetch(self.base + "/api/state", {**self.cookie, "Origin": "http://evil.example"})[0], 403)

    def test_static_files_cannot_climb_out(self):
        self.assertEqual(fetch(self.base + "/..%2f..%2fpyproject.toml", self.cookie)[0], 404)
        self.assertEqual(fetch(self.base + "/nope.js", self.cookie)[0], 404)

    def test_post_needs_json(self):
        code, _, _ = fetch(self.base + "/api/settings", {**self.cookie, "Content-Type": "application/json"}, b"{nope")
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
