"""中国 PMI — stats.gov.cn 采购经理指数月度发布稿解析。

发布稿 4 张表（PC + mobile 重复）：制造业主表（PMI + 5 分项）、制造业分项表
（8 项）、非制造业主表（商务活动 + 5 分项）、非制造业分项表（4 项）。表内
数据行按时间升序、最新月在末行——每张报告可回溯近 13 个月，逐报告合并可得
更长历史。索引：/sj/zxfbhjd/（2025-05 起）+ /sj/zxfb/（历史）。

输出：扩散指数原值（%）+ 衍生指标（库存差/供需差/备料差/穿越周期指数）。
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Any

from bs4 import BeautifulSoup

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro._http import fetch_html, polite_sleep
from deepmacro.china._report import (
    collect_report_urls, extract_month_from_title, scan_index_pages,
)

_log = logging.getLogger(__name__)

PMI_INDEX_URL = 'https://www.stats.gov.cn/sj/zxfbhjd/'
PMI_INDEX_URL_TEMPLATE = 'https://www.stats.gov.cn/sj/zxfbhjd/index_{n}.html'
PMI_OLD_INDEX_TEMPLATE = 'https://www.stats.gov.cn/sj/zxfb/index_{n}.html'

# 制造业 13 分项（表内列序）
_MFG_SUBJECTS = [
    '生产', '新订单', '原材料库存', '从业人员', '供应商配送时间',
    '新出口订单', '进口', '采购量', '购进价格', '出厂价格',
    '产成品库存', '在手订单', '生产经营活动预期',
]
# 非制造业分项（表内列序）
_NON_MFG_SUBJECTS = [
    '新订单', '投入品价格', '销售价格', '从业人员', '业务活动预期',
    '新出口订单', '在手订单', '存货', '供应商配送时间',
]

_MONTH_LABEL_RE = re.compile(
    r'(20\d{2})[-年./](\d{1,2})')


def _is_pmi_report(text: str, href: str) -> bool:
    if not text.startswith('20') or '月' not in text:
        return False
    return 'PMI' in text or '采购经理指数' in text


def _row_label_month(label: str) -> str | None:
    """表格行首月份标签 → 'YYYY-MM'（兼容 '2026-10'/'2026年10月' 等格式）。"""
    m = _MONTH_LABEL_RE.search(label)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return f'{y}-{mo:02d}'
    return None


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    if m == 1:
        return f'{y - 1}-12'
    return f'{y}-{m - 1:02d}'


def parse_pmi_report(html: str, report_month: str
                     ) -> dict[str, dict[str, float]]:
    """PMI 发布稿 → {month: {科目: 值}}。

    每张表的数据行按时间升序；末行锚定 report_month（标题提取），其余行
    依行序回推月份（行首标签可解析时以标签为准）。综合 PMI 从正文提取。
    """
    soup = BeautifulSoup(html, 'lxml')
    out: dict[str, dict[str, float]] = {}

    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) < 3:
            continue
        header_blob, header_idx = '', -1
        for ri in range(min(4, len(rows))):
            row_text = ' '.join(c.get_text(strip=True)
                                for c in rows[ri].find_all(['td', 'th']))
            if any(kw in row_text for kw in ('PMI', '商务活动', '采购量', '存货', '进口')):
                header_blob, header_idx = row_text, ri
                break
        if not header_blob:
            continue

        # 数据行（表头后），时间升序
        data_rows = rows[header_idx + 1:]
        # 行 → 月份（末行锚定 report_month，标签优先）
        months: list[str | None] = []
        anchor = report_month
        labels = [_row_label_month(r.find_all(['td', 'th'])[0].get_text(strip=True))
                  if r.find_all(['td', 'th']) else None
                  for r in reversed(data_rows)]
        for i, label in enumerate(labels):
            months.append(label or anchor)
            anchor = _prev_month(anchor)
        months.reverse()

        # 表类型路由
        def _fill(row, month):
            cells = [c.get_text(strip=True)
                     for c in row.find_all(['td', 'th'])]
            if len(cells) < 2 or month is None:
                return
            values: list[float | None] = []
            for txt in cells[1:]:
                try:
                    values.append(float(txt))
                except (ValueError, TypeError):
                    values.append(None)
            bucket = out.setdefault(month, {})
            if 'PMI' in header_blob:
                if values and values[0] is not None:
                    bucket['PMI'] = values[0]
                for i in range(min(5, max(0, len(values) - 1))):
                    if values[1 + i] is not None:
                        bucket[_MFG_SUBJECTS[i]] = values[1 + i]
            elif '商务活动' in header_blob:
                if values and values[0] is not None:
                    bucket['商务活动'] = values[0]
                for i in range(min(5, max(0, len(values) - 1))):
                    if values[1 + i] is not None:
                        bucket[f'非制造业·{_NON_MFG_SUBJECTS[i]}'] = values[1 + i]
            elif '存货' in header_blob:
                for i in range(min(4, len(values))):
                    if values[i] is not None:
                        bucket[f'非制造业·{_NON_MFG_SUBJECTS[5 + i]}'] = values[i]
            elif '采购量' in header_blob or '进口' in header_blob:
                for i in range(min(8, len(values))):
                    if values[i] is not None:
                        bucket[_MFG_SUBJECTS[5 + i]] = values[i]

        for row, month in zip(data_rows, months):
            _fill(row, month)

    # 综合 PMI 产出指数（正文句式）
    comp = re.search(r'综合PMI产出指数为\s*(\d+\.?\d*)', soup.get_text())
    if comp:
        out.setdefault(report_month, {})['综合PMI产出指数'] = float(comp.group(1))

    # 衍生指标（仅当月分项齐备时计算）
    bucket = out.get(report_month, {})
    _add_derivatives(bucket)
    return out


def _add_derivatives(bucket: dict[str, float]) -> None:
    """制造业分项齐备时计算衍生指标（库存差/供需差/备料差/穿越周期指数）。"""
    needed = ['生产', '新订单', '原材料库存', '采购量', '产成品库存']
    if any(bucket.get(k) is None for k in needed):
        return
    inv_diff = bucket['新订单'] - bucket['产成品库存']
    material_diff = bucket['采购量'] - bucket['原材料库存']
    bucket['库存差'] = round(inv_diff, 1)
    bucket['供需差'] = round(bucket['生产'] - bucket['新订单'], 1)
    bucket['备料差'] = round(material_diff, 1)
    bucket['穿越周期指数'] = round(material_diff + inv_diff, 1)


def macro_china_pmi(
    start_month: str | None = None,
    end_month: str | None = None,
    max_reports: int = 3,
) -> Any:
    """中国采购经理指数（PMI）— 国家统计局发布稿官方口径。

    制造业 PMI + 13 分项、非制造业商务活动 + 分项、综合 PMI 产出指数，
    附衍生指标（库存差/供需差/备料差/穿越周期指数）。每份发布稿内含近
    13 个月历史，多份报告自动合并去重。

    Args:
        start_month: 起始月 'YYYY-MM'（默认不限制）
        end_month: 截止月 'YYYY-MM'（默认至今）
        max_reports: 最多解析的报告份数（每份一个数据月 + 表内 13 月历史；
                     更多报告 = 更长历史但更慢）

    Returns:
        宽表 DataFrame：月份 + PMI/生产/新订单/…/商务活动/非制造业·新订单/
        …/综合PMI产出指数/衍生指标，数值为扩散指数 %。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_china_pmi().tail(3)
    """
    # 新索引（2025-05 起）+ 旧索引（历史），同月首见优先
    links = scan_index_pages(PMI_INDEX_URL_TEMPLATE, matcher=_is_pmi_report,
                             max_pages=max_reports + 1, first_url=PMI_INDEX_URL)
    old = scan_index_pages(PMI_OLD_INDEX_TEMPLATE, matcher=_is_pmi_report,
                           max_pages=max_reports + 1)
    url_map: dict[str, str] = {}
    for text, href in links + old:
        month = extract_month_from_title(text)
        if month and month not in url_map:
            url_map[month] = href

    if not url_map:
        raise RuntimeError('未在 stats.gov.cn 索引页找到 PMI 报告（可能触发反爬，稍后重试）')

    result: dict[str, dict[str, float]] = {}
    targets = sorted(url_map.keys(), reverse=True)[:max_reports]
    for month in targets:
        try:
            polite_sleep(1.0, 3.0)
            html = fetch_html(url_map[month])
            for m, subjects in parse_pmi_report(html, month).items():
                bucket = result.setdefault(m, {})
                bucket.update(subjects)
        except Exception as e:
            _log.warning('PMI report %s failed: %s', month, e)

    if not result:
        raise RuntimeError('PMI 发布稿解析为空')
    df = to_wide_frame(result)
    return clip_months(df, start_month, end_month)
