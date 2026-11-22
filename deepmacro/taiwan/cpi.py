"""台湾 CPI — 主计总处 nstatdb 官方口径。"""
from __future__ import annotations

from typing import Any

from deepmacro.japan._sources import default_range, levels_to_frame
from deepmacro.taiwan._nstatdb import TW_CPI_FUNID, fetch_taiwan_levels


def macro_taiwan_cpi(
    start_month: str | None = None,
    end_month: str | None = None,
    metric: str = 'yoy',
) -> Any:
    """台湾消費者物價指數（CPI）— 主计总处官方口径（民國110年=100）。

    nstatdb A030101025 表，449 序列（基本分類 + 項目群），1981-01 起全史。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2015-01）
        end_month: 截止月 'YYYY-MM'（默认至今）
        metric: 'yoy' 同比% | 'mom' 环比% | 'level' 指数原值

    Returns:
        宽表 DataFrame：月份 + 總指數/基本分類/項目群科目。

    Note:
        nstatdb 直连被 Cloudflare 拦截，需设 ``DEEPMACRO_HTTP_PROXY``。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_taiwan_cpi(start_month='2024-01').tail(3)
    """
    if metric not in ('yoy', 'mom', 'level'):
        raise ValueError("metric 必须为 'yoy' | 'mom' | 'level'")
    start, end = default_range(start_month, end_month, default_start='2015-01')
    levels = fetch_taiwan_levels(TW_CPI_FUNID)
    return levels_to_frame(levels, metric, start, end)
