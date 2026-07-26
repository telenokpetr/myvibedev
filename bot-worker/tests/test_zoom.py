"""Юнит-тесты разбора ссылок Zoom и построения zoommtg://-URL для автовхода."""

import urllib.parse

import pytest

from app import zoom
from app.config import config


# ------------------------------------------------------------ parse_meeting ---

class TestParseMeeting:
    def test_standard_join_url(self):
        conf, pwd = zoom.parse_meeting("https://us02web.zoom.us/j/1234567890")
        assert conf == "1234567890"
        assert pwd is None

    def test_join_url_with_pwd(self):
        conf, pwd = zoom.parse_meeting(
            "https://us02web.zoom.us/j/1234567890?pwd=aBcDeF123"
        )
        assert conf == "1234567890"
        assert pwd == "aBcDeF123"

    def test_confno_query_form(self):
        conf, pwd = zoom.parse_meeting(
            "https://zoom.us/wc/join?confno=9876543210"
        )
        assert conf == "9876543210"
        assert pwd is None

    def test_unparseable_url(self):
        conf, pwd = zoom.parse_meeting("https://example.com/not-a-meeting")
        assert conf is None
        assert pwd is None


# ----------------------------------------------------------- build_zoommtg ---

class TestBuildZoommtg:
    def test_builds_scheme_url(self):
        url = zoom.build_zoommtg("https://us02web.zoom.us/j/1234567890", None)
        assert url.startswith("zoommtg://zoom.us/join?")
        assert "confno=1234567890" in url
        assert "action=join" in url
        assert f"uname={urllib.parse.quote(config.bot_name)}" in url

    def test_passcode_argument_overrides_url_pwd(self):
        url = zoom.build_zoommtg(
            "https://us02web.zoom.us/j/1234567890?pwd=fromurl", "override"
        )
        assert "pwd=override" in url
        assert "fromurl" not in url

    def test_uses_url_pwd_when_no_argument(self):
        url = zoom.build_zoommtg(
            "https://us02web.zoom.us/j/1234567890?pwd=fromurl", None
        )
        assert "pwd=fromurl" in url

    def test_pwd_is_url_encoded(self):
        url = zoom.build_zoommtg("https://us02web.zoom.us/j/1/", "a b/c")
        # пробел и слэш должны быть экранированы
        assert "a b/c" not in url
        assert "pwd=" in url

    def test_fallback_returns_original_when_unparseable(self):
        original = "https://example.com/custom-invite"
        assert zoom.build_zoommtg(original, None) == original

    @pytest.mark.parametrize("passcode", [None, "", "secret"])
    def test_never_crashes_on_various_inputs(self, passcode):
        # Не должно бросать исключений на любых комбинациях.
        zoom.build_zoommtg("https://us02web.zoom.us/j/1234567890", passcode)
