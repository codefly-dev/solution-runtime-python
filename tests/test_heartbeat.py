import contextlib
import http.client
import io
import unittest
import urllib.error
from unittest import mock

from solution_runtime import _heartbeat, _post_registration, _register_interval


class _Response:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class PostRegistrationTest(unittest.TestCase):
    def test_success_returns_none(self):
        with mock.patch("urllib.request.urlopen", return_value=_Response(200)):
            self.assertIsNone(_post_registration("http://x", {}, {}))

    def test_non_2xx_returns_reason(self):
        with mock.patch("urllib.request.urlopen", return_value=_Response(503)):
            self.assertEqual(_post_registration("http://x", {}, {}), "HTTP 503")

    def test_connection_error_returns_reason(self):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            self.assertIsInstance(_post_registration("http://x", {}, {}), str)

    def test_http_error_returns_reason(self):
        error = urllib.error.HTTPError("http://x", 500, "boom", {}, io.BytesIO(b""))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            self.assertIsInstance(_post_registration("http://x", {}, {}), str)

    def test_non_oserror_exception_does_not_escape(self):
        # A peer redeploying can make urlopen raise http.client.HTTPException,
        # which is neither URLError nor OSError. If it escaped, the heartbeat
        # thread would die and the solution would never re-register.
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=http.client.BadStatusLine("garbage"),
        ):
            self.assertIsInstance(_post_registration("http://x", {}, {}), str)

    def test_header_value_error_does_not_escape(self):
        with mock.patch(
            "urllib.request.urlopen", side_effect=ValueError("Invalid header value")
        ):
            self.assertIsInstance(_post_registration("http://x", {}, {}), str)


class _StopLoop(Exception):
    pass


def _run_heartbeat(reasons):
    """Drive _heartbeat over a fixed sequence of _post_registration results,
    then break out of its infinite loop. Return the printed lines."""
    out = io.StringIO()
    sleeps = {"n": 0}

    def sleep(_):
        sleeps["n"] += 1
        if sleeps["n"] >= len(reasons):
            raise _StopLoop

    with mock.patch(
        "solution_runtime._post_registration", side_effect=list(reasons)
    ), mock.patch("time.sleep", side_effect=sleep), contextlib.redirect_stdout(out):
        with contextlib.suppress(_StopLoop):
            _heartbeat("http://x", {}, "host as s", {}, 0)
    return out.getvalue().splitlines()


class HeartbeatLoggingTest(unittest.TestCase):
    def test_logs_once_on_first_success(self):
        lines = _run_heartbeat([None, None])
        self.assertEqual(lines, ["registered with host as s"])

    def test_logs_recovery_after_loss(self):
        # startup ok, peer restarts (lost), re-registers within an interval.
        lines = _run_heartbeat([None, "HTTP 503", None])
        self.assertEqual(
            lines,
            [
                "registered with host as s",
                "lost registration with host as s: HTTP 503",
                "registered with host as s",
            ],
        )

    def test_survives_failure_and_keeps_looping(self):
        # A failing first attempt must not stop the loop; it recovers later.
        lines = _run_heartbeat(["boom", "boom", None])
        self.assertEqual(lines, ["registered with host as s"])


class RegisterIntervalTest(unittest.TestCase):
    def test_default(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_register_interval(), 15.0)

    def test_override(self):
        with mock.patch.dict("os.environ", {"REGISTER_INTERVAL_SECONDS": "30"}):
            self.assertEqual(_register_interval(), 30.0)

    def test_invalid_falls_back(self):
        with mock.patch.dict("os.environ", {"REGISTER_INTERVAL_SECONDS": "nope"}):
            self.assertEqual(_register_interval(), 15.0)

    def test_non_positive_falls_back(self):
        with mock.patch.dict("os.environ", {"REGISTER_INTERVAL_SECONDS": "0"}):
            self.assertEqual(_register_interval(), 15.0)


if __name__ == "__main__":
    unittest.main()
