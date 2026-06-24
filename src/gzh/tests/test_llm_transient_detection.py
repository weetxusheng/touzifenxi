import urllib.error

import pytest

from gzh_pipeline.parse import llm_compat as lc


@pytest.mark.parametrize(
    ("exc", "expect"),
    [
        (urllib.error.URLError("<urlopen error [WinError 10054] 远程主机强迫关闭了一个现有的连接。>"), True),
        (urllib.error.URLError("Remote end closed connection without response"), True),
        (urllib.error.HTTPError("https://example.com/x", 503, "gw", hdrs={}, fp=None), True),
        (urllib.error.HTTPError("https://example.com/x", 401, "auth", hdrs={}, fp=None), False),
    ],
)
def test_is_transient_http_failure(exc: BaseException, expect: bool):
    assert lc._is_transient_http_failure(exc) == expect


def test_retry_env_defaults_readable():
    assert lc.llm_http_extra_retries() >= 0
    assert lc.llm_retry_base_seconds() >= 0
