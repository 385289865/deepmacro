"""中国社会消费品零售总额 — stats.gov.cn 月度发布稿解析。

发布稿主附表 26 科目 × 5 列口径（当月绝对量/当月同比/累计绝对量/累计同比）
+ 环比表（近 13 个月）。1—2 月合并发布稿为 3 列变体（仅累计口径）。

输出：长表（月份 × 科目 × 口径）便于按需透视。
"""
from __future__ import annotations

import logging
import re
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup

from deepmacro._http import fetch_html, polite_sleep
from deepmacro.china._report import (
    collect_report_urls, extract_month_from_title, to_float,
)

_log = logging.getLogger(__name__)

INDEX_URL = 'https://www.stats.gov.cn/sj/zxfb/'
INDEX_URL_TEMPLATE = 'https://www.stats.gov.cn/sj/zxfb/index_{n}.html'

_MAIN_ANCHORS = ('社会消费品零售总额', '按经营地分')
_NUM = re.compile(r'^-?\d+\.?\d*$')

# 输出列名：官方口径
METRIC_COLUMNS = {
    'monthly_value': '当月（亿元）',
    'yoy': '当月同比（%）',
    'mom': '环比（%）',
    'cumulative_value': '累计（亿元）',
    'cumulative': '累计同比（%）',
}


def _is_retail_report(text: str, href: str) -> bool:
    if not text.startswith('20') or '月' not in text:
        return False
    return '社会消费品零售总额' in text


def _parse_main_table(table) -> dict[str, dict[str, float]]:
    """主附表 → {metric: {科目: 值}}，5 列按位置映射；1—2 月合并稿 3 列变体。"""
    out: dict[str, dict[str, float]] = {
        'monthly_value': {}, 'yoy': {}, 'cumulative_value': {}, 'cumulative': {}}
    for tr in table.find_all('tr'):
        cells = [c.get_text(strip=True) for c in tr.find_all(['td', 'th'])]
        if len(cells) < 3:
            continue
        name = cells[0]
        if not name or re.fullmatch(r'指\s*标', name):
            continue
        if name.startswith('注：') or name.startswith('按'):
            continue
        if name.startswith('其中：'):
            name = name[len('其中：'):]
        if len(cells) >= 5:
            vals = [c if _NUM.fullmatch(c) else None for c in cells[1:5]]
            if all(v is None for v in vals):
                continue
            mv, yy, cv, cu = vals
        else:  # 3—4 列：1—2 月合并稿（绝对量|同比 → 累计口径）
            cv = cells[1] if _NUM.fullmatch(cells[1]) else None
            cu = cells[2] if _NUM.fullmatch(cells[2]) else None
            if cv is None and cu is None:
                continue
            mv = yy = None
        if mv is not None:
            out['monthly_value'][name] = float(mv)
        if yy is not None:
            out['yoy'][name] = float(yy)
        if cv is not None:
            out['cumulative_value'][name] = float(cv)
        if cu is not None:
            out['cumulative'][name] = float(cu)
    return out


def _parse_mom_table(table) -> dict[str, float]:
    """环比表（年份|月份|环比%）→ {'YYYY-MM': 值}（年份 rowspan 跨行携带）。"""
    out: dict[str, float] = {}
    year: str | None = None
    for tr in table.find_all('tr'):
        cells = [c.get_text(strip=True) for c in tr.find_all(['td', 'th'])]
        if len(cells) < 2 or not _NUM.fullmatch(cells[-1]):
            continue
        if len(cells) >= 3:
            m = re.fullmatch(r'(20\d{2})年', cells[0])
            if m:
                year = m.group(1)
            month_cell = cells[1]
        else:
            month_cell = cells[0]
        mm = re.fullmatch(r'(\d{1,2})月', month_cell)
        if year and mm:
            out[f'{year}-{int(mm.group(1)):02d}'] = float(cells[-1])
    return out


def parse_retail_report(html: str) -> dict[str, dict[str, Any]]:
    """社零发布稿 → {metric: {科目或月份: 值}}（mom 键为月份）。"""
    soup = BeautifulSoup(html, 'lxml')
    buckets: dict[str, dict[str, float]] = {
        'monthly_value': {}, 'yoy': {}, 'cumulative_value': {},
        'cumulative': {}, 'mom': {}}
    for table in soup.find_all('table'):
        text = table.get_text()
        if all(a in text for a in _MAIN_ANCHORS):
            for metric, data in _parse_main_table(table).items():
                buckets[metric].update(data)
        elif '环比增速' in text and '年份' in text:
            buckets['mom'].update(_parse_mom_table(table))
    return buckets


def macro_china_retail(
    start_month: str | None = None,
    end_month: str | None = None,
    max_reports: int = 3,
) -> Any:
    """中国社会消费品零售总额 — 国家统计局发布稿官方口径，全量科目。

    覆盖总量（社零/商品零售/餐饮收入）× 经营地（城镇/乡村）× 消费类型
    （商品零售/餐饮）× 零售业态（限上/网上零售等）26 科目、5 列官方口径；
    环比表提供近 13 个月总量环比。

    Args:
        start_month: 起始月 'YYYY-MM'（默认不限制）
        end_month: 截止月 'YYYY-MM'（默认至今）
        max_reports: 最多解析的报告份数（发布稿窗口外更早历史不在官方免费源内）

    Returns:
        长表 DataFrame：[月份, 科目, 当月（亿元）, 当月同比（%）, 累计（亿元）,
        累计同比（%）]，环比仅总量科目有值（环比（%）列）。

    Note:
        官方 1—2 月合并发布（无单月数），该月仅累计口径有值。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_china_retail().tail(5)
    """
    url_map = collect_report_urls(INDEX_URL, INDEX_URL_TEMPLATE,
                                  matcher=_is_retail_report,
                                  max_pages=max_reports + 1)
    if not url_map:
        raise RuntimeError('未在 stats.gov.cn 索引页找到社零报告（可能触发反爬，稍后重试）')

    # {month: {metric: {subject: value}}}
    by_month: dict[str, dict[str, dict[str, float]]] = {}
    targets = sorted(url_map.keys(), reverse=True)[:max_reports]
    for month in targets:
        try:
            polite_sleep(1.0, 3.0)
            html = fetch_html(url_map[month])
            buckets = parse_retail_report(html)
            bucket = by_month.setdefault(month, {})
            for metric in ('monthly_value', 'yoy', 'cumulative_value', 'cumulative'):
                for s, v in buckets[metric].items():
                    bucket.setdefault(metric, {})[s] = v
            # 环比表逐月落（subject 固定总量）
            for m, v in buckets['mom'].items():
                by_month.setdefault(m, {}).setdefault('mom', {})['社会消费品零售总额'] = v
        except Exception as e:
            _log.warning('retail report %s failed: %s', month, e)

    if not by_month:
        raise RuntimeError('社零发布稿解析为空')

    # 长表组装
    rows: list[dict[str, Any]] = []
    subjects: list[str] = []
    seen: set[str] = set()
    for m in sorted(by_month):
        for s in by_month[m].get('cumulative', by_month[m].get('yoy', {})):
            if s not in seen:
                seen.add(s)
                subjects.append(s)
    for m in sorted(by_month):
        data = by_month[m]
        for s in subjects:
            row: dict[str, Any] = {'月份': m, '科目': s}
            hit = False
            for metric, col in METRIC_COLUMNS.items():
                v = data.get(metric, {}).get(s)
                if v is not None:
                    row[col] = v
                    hit = True
            if hit:
                rows.append(row)
    df = pd.DataFrame(rows)
    if start_month is not None:
        df = df[df['月份'] >= start_month]
    if end_month is not None:
        df = df[df['月份'] <= end_month]
    return df.reset_index(drop=True)
