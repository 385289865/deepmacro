"""HTTP 公共层 — 会话、WZWS 反爬检测、重试与代理。

代理约定：环境变量 ``DEEPMACRO_HTTP_PROXY``（如 ``socks5://127.0.0.1:1080``
或 ``http://127.0.0.1:7890``）对全部请求生效。BLS / e-Stat / 台湾主计总处
等海外源在中国大陆通常需要代理；国家统计局接口直连即可。socks 代理需额外
安装 ``requests[socks]``。
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

_log = logging.getLogger(__name__)

PROXY_ENV = 'DEEPMACRO_HTTP_PROXY'

WZWS_CHALLENGE_MARKERS = (
    'Please enable JavaScript',
    'WZWS_CHARSET',
    'wzws',
)

_STATS_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}


def get_proxies() -> dict[str, str] | None:
    """读取 ``DEEPMACRO_HTTP_PROXY`` 环境变量 → requests proxies；未设置返回 None。"""
    import os
    url = os.environ.get(PROXY_ENV, '').strip()
    if not url:
        return None
    return {'http': url, 'https': url}


def is_wzws_challenge(html: str) -> bool:
    """检测 stats.gov.cn WZWS 反爬挑战页面（JS 挑战页无真实内容）。"""
    if not html:
        return False
    head = html[:2048]
    return any(marker in html for marker in WZWS_CHALLENGE_MARKERS[:2]) or 'wzws' in head.lower()


def new_stats_session() -> requests.Session:
    """stats.gov.cn HTML 会话（复用 cookie 应对 WZWS 挑战）。"""
    s = requests.Session()
    s.headers.update(_STATS_HEADERS)
    return s


def fetch_html_robust(
    url: str,
    session: requests.Session | None = None,
    max_retries: int = 2,
    timeout: int = 30,
) -> str:
    """带 WZWS 检测 + session cookie 重试的 HTML 抓取（直连重试耗尽后代理兜底）。

    WZWS 挑战持续时返回空字符串；网络异常耗尽后抛出。
    """
    owns_session = session is None
    if owns_session:
        session = new_stats_session()

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or 'utf-8'
            html = resp.text
            if not is_wzws_challenge(html):
                return html
            _log.warning('WZWS challenge on %s (attempt %d/%d)',
                         url, attempt + 1, max_retries + 1)
        except Exception as e:
            last_exc = e
            _log.warning('fetch %s failed (attempt %d/%d): %s',
                         url, attempt + 1, max_retries + 1, e)
        if attempt < max_retries:
            time.sleep(random.uniform(5, 10))

    proxies = get_proxies()
    if proxies:
        try:
            resp = session.get(url, timeout=timeout, proxies=proxies)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or 'utf-8'
            if not is_wzws_challenge(resp.text):
                return resp.text
        except Exception as e:
            _log.warning('fetch %s via proxy failed: %s', url, e)

    if last_exc is not None:
        raise last_exc
    _log.error('WZWS challenge persisted after %d retries on %s', max_retries, url)
    return ''


def fetch_html(url: str, timeout: int = 30) -> str:
    """获取 HTML 页面内容；WZWS 挑战持续时抛 RuntimeError。"""
    html = fetch_html_robust(url, timeout=timeout)
    if not html:
        raise RuntimeError(f'WZWS challenge persisted on {url} after retries')
    return html


def download_file(url: str, timeout: int = 60,
                  headers: dict[str, str] | None = None) -> bytes:
    """下载二进制文件（zip/xlsx 等），直连优先、代理兜底。"""
    h = headers or _STATS_HEADERS
    try:
        resp = requests.get(url, timeout=timeout, headers=h)
        resp.raise_for_status()
        return resp.content
    except Exception as direct_err:
        proxies = get_proxies()
        if not proxies:
            raise
        _log.warning('direct download failed (%s), retry via proxy: %s', direct_err, url)
        resp = requests.get(url, timeout=timeout, headers=h, proxies=proxies)
        resp.raise_for_status()
        return resp.content


def request_direct_first(method: str, url: str, timeout: int = 30,
                         params: dict[str, Any] | None = None,
                         headers: dict[str, str] | None = None,
                         json_body: dict[str, Any] | None = None) -> requests.Response:
    """直连优先的 HTTP 请求；失败/403 时切换 ``DEEPMACRO_HTTP_PROXY`` 代理重试一次。"""
    try:
        resp = requests.request(method, url, timeout=timeout, params=params,
                                headers=headers, json=json_body)
        if resp.status_code != 403:
            return resp
        reason = f'HTTP {resp.status_code}'
    except requests.RequestException as e:
        reason = str(e)
    proxies = get_proxies()
    if not proxies:
        raise RuntimeError(
            f'直连失败（{reason}）且未设置 {PROXY_ENV} 环境变量——'
            f'该数据源在当前网络环境可能需要代理')
    _log.warning('direct request failed (%s), retry via proxy: %s', reason, url)
    return requests.request(method, url, timeout=timeout, params=params,
                            headers=headers, json=json_body, proxies=proxies)


def polite_sleep(low: float = 0.5, high: float = 1.5) -> None:
    """请求间随机小睡，降低触发反爬的概率。"""
    time.sleep(random.uniform(low, high))
