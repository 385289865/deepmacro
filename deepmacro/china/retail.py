"""中国社会消费品零售总额 — 国家统计局数据发布库 API（data.stats.gov.cn）。

总量口径 8 指标：当期值(亿元)/累计值(亿元)/同比增长(%)/累计增长(%) +
限上单位对应 4 项。宽表输出（v0.1.0 发布稿长表已随发布稿方案移除）。
"""
from __future__ import annotations

from typing import Any

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro._http import polite_sleep
from deepmacro.china import _nbs

_RETAIL_CID = 'd0cb882c7f27443ab6b3ef9421901961'


def _clean_name(api_name: str) -> str:
    name = api_name.strip()
    for suffix in (' (%)', ' (亿元)', '（%）', '（亿元）'):
        if name.endswith(suffix):
            return name[:-len(suffix)].strip()
    return name


def macro_china_retail(
    start_month: str | None = None,
    end_month: str | None = None,
) -> Any:
    """中国社会消费品零售总额 — 国家统计局官方口径，总量 8 指标。

    社零总额与限上单位的当期值/累计值/同比/累计增长。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2021-02，官方 1—2 月合并发布，
            各年无 1 月单月数，实跑最早月以 head(1) 为准）
        end_month: 截止月 'YYYY-MM'（默认至今）

    Returns:
        宽表 DataFrame：月份 + 8 列（社会消费品零售总额当期值/
        同比增长/…/限上单位消费品零售额当期值/…）。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_china_retail(start_month='2024-01').tail(3)
    """
    start = start_month or '2021-02'
    end = end_month or _current_month()
    indicators = _nbs.query_indicators(_RETAIL_CID)
    iid_to_subject = {it['iid']: _clean_name(it['name'])
                      for it in indicators if it.get('iid')}
    if not iid_to_subject:
        raise RuntimeError('国家统计局社零接口返回无指标（可能触发反爬，稍后重试）')
    polite_sleep()
    # 等区间（起止同月）实测可用 'YYYYMMMM-YYYYMMMM'（fai 单月验证通过），
    # 无需 ppi 的单 period 特判。
    period_range = (f'{_nbs.month_to_period(start)}-'
                    f'{_nbs.month_to_period(end)}')
    try:
        raw = _nbs.query_data(_RETAIL_CID, list(iid_to_subject), period_range)
    except Exception as e:
        raise RuntimeError(
            f'国家统计局社零节点拉取失败（cid={_RETAIL_CID}，'
            '可能触发反爬，稍后重试）') from e
    result: dict[str, dict[str, float]] = {}
    for time_code, values in raw.items():
        month = _nbs.period_to_month(time_code)
        bucket = result.setdefault(month, {})
        for iid, val_str in values.items():
            subject = iid_to_subject.get(iid)
            if subject:
                try:
                    bucket[subject] = float(val_str)
                except (ValueError, TypeError):
                    pass
    if not result:
        raise RuntimeError('国家统计局社零接口返回无数据（可能触发反爬，稍后重试）')
    return clip_months(to_wide_frame(result), start, end)


def _current_month() -> str:
    from datetime import datetime
    now = datetime.now()
    return f'{now.year}-{now.month:02d}'
