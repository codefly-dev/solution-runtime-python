import unittest
import urllib.request
from unittest import mock

from solution_runtime import _register_headers


class RegisterHeadersTest(unittest.TestCase):
    def test_includes_token_when_set(self):
        with mock.patch.dict("os.environ", {"CODEFLY_INTERNAL_TOKEN": "secret"}):
            headers = _register_headers()
        self.assertEqual(headers["x-codefly-internal-token"], "secret")
        self.assertEqual(headers["content-type"], "application/json")

    def test_omits_token_when_unset(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            headers = _register_headers()
        self.assertNotIn("x-codefly-internal-token", headers)
        self.assertEqual(headers["content-type"], "application/json")

    def test_omits_token_when_empty(self):
        with mock.patch.dict("os.environ", {"CODEFLY_INTERNAL_TOKEN": ""}):
            headers = _register_headers()
        self.assertNotIn("x-codefly-internal-token", headers)

    def test_omits_token_when_whitespace_only(self):
        with mock.patch.dict("os.environ", {"CODEFLY_INTERNAL_TOKEN": "  \n"}):
            headers = _register_headers()
        self.assertNotIn("x-codefly-internal-token", headers)

    def test_strips_surrounding_whitespace(self):
        with mock.patch.dict("os.environ", {"CODEFLY_INTERNAL_TOKEN": "secret\n"}):
            headers = _register_headers()
        self.assertEqual(headers["x-codefly-internal-token"], "secret")

    def test_stripped_token_yields_sendable_header(self):
        # A trailing newline in the header value raises ValueError at send
        # time. Stripping keeps the header sendable so the request never
        # fails on that account; building it must not raise on urlopen.
        with mock.patch.dict("os.environ", {"CODEFLY_INTERNAL_TOKEN": "secret\n"}):
            headers = _register_headers()
        request = urllib.request.Request(
            "http://127.0.0.1:1/x", data=b"{}", headers=headers, method="POST"
        )
        opener = urllib.request.build_opener()
        with self.assertRaises(urllib.error.URLError):
            opener.open(request, timeout=0.1)


if __name__ == "__main__":
    unittest.main()
