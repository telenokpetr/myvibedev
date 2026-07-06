"""Запуск официального Zoom-клиента и вход в конференцию по ссылке."""

import os
import re
import subprocess
import urllib.parse

from app.config import config

ENV = {**os.environ, "DISPLAY": config.display}


def parse_meeting(join_url: str) -> tuple[str | None, str | None]:
    """Из https-ссылки достаём номер конференции и pwd (если есть)."""
    conf = None
    pwd = None

    m = re.search(r"/j/(\d+)", join_url)
    if m:
        conf = m.group(1)
    else:
        m = re.search(r"[?&]confno=(\d+)", join_url)
        if m:
            conf = m.group(1)

    q = urllib.parse.urlparse(join_url).query
    params = urllib.parse.parse_qs(q)
    if "pwd" in params:
        pwd = params["pwd"][0]

    return conf, pwd


def build_zoommtg(join_url: str, passcode: str | None) -> str:
    """Строим zoommtg://-ссылку для авто-входа. Если не распарсили — отдаём исходную."""
    conf, pwd = parse_meeting(join_url)
    pwd = passcode or pwd
    if not conf:
        return join_url

    parts = [f"confno={conf}", "action=join"]
    if pwd:
        parts.append(f"pwd={urllib.parse.quote(pwd)}")
    parts.append(f"uname={urllib.parse.quote(config.bot_name)}")
    return "zoommtg://zoom.us/join?" + "&".join(parts)


def launch(join_url: str, passcode: str | None) -> subprocess.Popen:
    """Запускаем Zoom-клиент с входом в конференцию."""
    url = build_zoommtg(join_url, passcode)
    return subprocess.Popen(
        ["zoom", f"--url={url}"],
        env=ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def kill(proc: subprocess.Popen | None = None) -> None:
    """Завершаем Zoom и reap-аем наш Popen, чтобы не копить <defunct>-зомби."""
    subprocess.run(["pkill", "-TERM", "zoom"], env=ENV, check=False)
    if proc is None:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()          # не отреагировал на TERM — добиваем KILL
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
