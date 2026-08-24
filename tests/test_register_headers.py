import unittest
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


if __name__ == "__main__":
    unittest.main()
