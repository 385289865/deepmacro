"""stats.gov.cn 最新发布（zxfb）报告页扫描基础设施。

国家统计局月度发布稿（工业增加值/社零/固投/PMI）为 HTML 报告页，索引位于
``https://www.stats.gov.cn/sj/zxfb/``（分页 ``index_{n}.html``）。发布稿
表格为行式附表，PC/mobile 重复（幂等覆盖）。
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from deepmacro._http import fetch_html_robust, new_stats_session

_log = logging.getLogger(__name__)

ZXB_INDEX_URL = 'https://www.stats.gov.cn/sj/zxfb/'
ZXB_INDEX_URL_TEMPLATE = 'https://www.stats.gov.cn/sj/zxfb/index_{n}.html'


def normalize_stats_url(href: str, page_url: str) -> str:
    """索引页相对链接 → 绝对 URL。"""
    return urljoin(page_url, href)


def extract_month_from_title(text: str) -> str | None:
    """'2026年1—2月份…' → '2026-02'（1—2 月合并发布归到 02）。"""
    match = re.search(r'(20\d{2})年\d{1,2}[—\-]\s*(\d{1,2})月', text)
    if match:
        return f'{match.group(1)}-{int(match.group(2)):02d}'
    match = re.search(r'(20\d{2})年(\d{1,2})月', text)
    if match:
        return f'{match.group(1)}-{int(match.group(2)):02d}'
    return None


def scan_index_pages(
    base_url: str,
    matcher: Callable[[str, str], bool],
    max_pages: int = 6,
    first_url: str | None = None,
) -> list[tuple[str, str]]:
    """扫描分页索引，返回所有匹配的 (link_text, absolute_url)（最新在前）。

    先扫 first_url（默认为 base_url 去 index_{n}.html 后缀的首页，最新月在首页），
    再扫 index_{n}.html 分页；连续 2 页失败提前终止。
    """
    if '{n}' not in base_url:
        raise ValueError(f"base_url must contain '{{n}}' placeholder, got: {base_url}")
    if first_url is None:
        first_url = base_url.replace('index_{n}.html', '')

    session = new_stats_session()
    results: list[tuple[str, str]] = []

    def _scan_page(url: str) -> bool:
        """扫描单页；返回 False 表示页面不可用（空/异常）。"""
        try:
            html = fetch_html_robust(url, session=session)
        except Exception as e:
            _log.warning('scan page %s failed: %s', url, e)
            return False
        if not html:
            return False
        soup = BeautifulSoup(html, 'lxml')
        for link in soup.find_all('a', href=True):
            text = link.get_text(strip=True)
            href = link['href']
            if matcher(text, href):
                results.append((text, normalize_stats_url(href, url)))
        return True

    _scan_page(first_url)

    consecutive_failures = 0
    for page_num in range(1, max_pages + 1):
        url = base_url.format(n=page_num)
        ok = _scan_page(url)
        if not ok:
            consecutive_failures += 1
            if consecutive_failures >= 2:
                _log.info('2 consecutive failures, stopping at page %d', page_num)
                break
        else:
            consecutive_failures = 0
        if page_num < max_pages:
            time.sleep(random.uniform(3, 6))

    return results


def collect_report_urls(
    index_url: str,
    index_url_template: str,
    matcher: Callable[[str, str], bool],
    max_pages: int = 6,
) -> dict[str, str]:
    """扫描索引 → {month: report_url}（首页 + 分页，同月首见优先）。"""
    links = scan_index_pages(index_url_template, matcher=matcher,
                             max_pages=max_pages, first_url=index_url)
    url_map: dict[str, str] = {}
    for link_text, href in links:
        month = extract_month_from_title(link_text)
        if month and month not in url_map:
            url_map[month] = href
    _log.info('collected %d month report urls for %s', len(url_map), index_url)
    return url_map


def to_float(cell: str) -> float | None:
    """报告页单元格 → 数值；'(百分点)' 后缀剥离；非数值返回 None。"""
    m = re.fullmatch(r'\s*(-?\d+\.?\d*)(?:\s*\(百分点\))?\s*', cell)
    return float(m.group(1)) if m else None
