"""中国固定资产投资 — stats.gov.cn 月度发布稿解析。

官方仅发布累计同比（1—N 月）。发布稿附表单张行式表混含分产业/分构成/
分行业/分登记注册类型全部科目（2 列 [指标, 同比增长%]），正文补充核心口径
（民间投资/基础设施/制造业投资等）。

输出：累计同比 %。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro._http import fetch_html, polite_sleep
from deepmacro.china._report import (
    collect_report_urls, extract_month_from_title,
)

_log = logging.getLogger(__name__)

INDEX_URL = 'https://www.stats.gov.cn/sj/zxfb/'
INDEX_URL_TEMPLATE = 'https://www.stats.gov.cn/sj/zxfb/index_{n}.html'

# 正文核心口径句式科目
_CORE_SUBJECTS = [
    '民间固定资产投资', '基础设施投资', '制造业投资',
    '高技术产业投资', '社会领域投资', '房地产开发投资',
]
# 附表锚点（任一命中即解析整表）
_TABLE_ANCHORS: list[tuple[str, ...]] = [
    ('第一产业', '第二产业'),
    ('建筑安装工程', '设备工器具购置'),
    ('农林牧渔业', '采矿业'),
    ('内资企业', '港澳台投资企业'),
]


def _is_fai_report(text: str, href: str) -> bool:
    if not text.startswith('20') or '月' not in text:
        return False
    return '固定资产投资' in text


def _parse_row_table(table) -> dict[str, float]:
    """行式表 → {科目: 累计同比%}，取行内最后数值列。"""
    out: dict[str, float] = {}
    for tr in table.find_all('tr'):
        cells = [c.get_text(strip=True) for c in tr.find_all(['td', 'th'])]
        if len(cells) < 2:
            continue
        name = cells[0]
        if not name or re.fullmatch(r'指\s*标', name) or name in ('指标', '项目'):
            continue
        if name.startswith('注：'):
            continue
        if name.startswith('其中：'):
            name = name[len('其中：'):]
        non_empty = [c for c in cells[1:] if c != '']
        if not non_empty or not re.fullmatch(r'-?\d+\.?\d*', non_empty[-1]):
            continue
        values = [c for c in cells[1:] if re.fullmatch(r'-?\d+\.?\d*', c)]
        if values:
            out[name] = float(values[-1])
    return out


def parse_fai_report(html: str) -> dict[str, float]:
    """固投发布稿 → {科目: 累计同比%}（附表路由 + 正文核心口径兜底）。"""
    soup = BeautifulSoup(html, 'lxml')
    text = soup.get_text()
    flat = re.sub(r'\s+', '', text)
    data: dict[str, float] = {}

    for table in soup.find_all('table'):
        table_text = table.get_text()
        if any(all(a in table_text for a in anchors) for anchors in _TABLE_ANCHORS):
            data.update(_parse_row_table(table))

    for subject in _CORE_SUBJECTS:
        m = re.search(re.escape(subject) + r'(?:（[^）]*）)?'
                      r'(?:比去年同期|同比)?(增长|下降)(\d+\.?\d*)%', flat)
        if m:
            val = float(m.group(2))
            data.setdefault(subject, val if m.group(1) == '增长' else -val)

    m = re.search(r'全国固定资产投资（不含农户）[\d,]+亿元，同比(增长|下降)(\d+\.?\d*)%', flat)
    if m:
        val = float(m.group(2))
        data.setdefault('固定资产投资（不含农户）', val if m.group(1) == '增长' else -val)
    return data


def macro_china_fai(
    start_month: str | None = None,
    end_month: str | None = None,
    max_reports: int = 3,
) -> Any:
    """中国固定资产投资（不含农户）— 国家统计局发布稿官方口径，全量科目。

    覆盖分产业（一/二/三产）、分构成（建筑安装工程/设备工器具购置/其他费用）、
    分行业（农林牧渔/采矿/制造/…）、分登记注册类型（内资/外商投资/…）与
    核心口径（民间投资/基础设施投资/制造业投资/高技术产业投资等）。

    Args:
        start_month: 起始月 'YYYY-MM'（默认不限制）
        end_month: 截止月 'YYYY-MM'（默认至今）
        max_reports: 最多解析的报告份数

    Returns:
        宽表 DataFrame：月份 + 各科目，数值为累计同比 %（官方仅此口径）。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_china_fai().tail(3)
    """
    url_map = collect_report_urls(INDEX_URL, INDEX_URL_TEMPLATE,
                                  matcher=_is_fai_report,
                                  max_pages=max_reports + 1)
    if not url_map:
        raise RuntimeError('未在 stats.gov.cn 索引页找到固投报告（可能触发反爬，稍后重试）')

    result: dict[str, dict[str, float]] = {}
    targets = sorted(url_map.keys(), reverse=True)[:max_reports]
    for month in targets:
        try:
            polite_sleep(1.0, 3.0)
            html = fetch_html(url_map[month])
            data = parse_fai_report(html)
            if data:
                result[month] = data
        except Exception as e:
            _log.warning('fai report %s failed: %s', month, e)

    if not result:
        raise RuntimeError('固投发布稿解析为空')
    df = to_wide_frame(result)
    return clip_months(df, start_month, end_month)
