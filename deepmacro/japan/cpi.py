"""日本 CPI — e-Stat 総務省官方口径。"""
from __future__ import annotations

from typing import Any

from deepmacro.japan._sources import (
    default_range, fetch_japan_cpi_levels, levels_to_frame,
)


def macro_japan_cpi(
    start_month: str | None = None,
    end_month: str | None = None,
    metric: str = 'yoy',
) -> Any:
    """日本消費者物価指数（CPI）— e-Stat 総務省官方口径。

    当月最新发布文件（表1-1 中分類指数 + 表4-1 品目別価格指数，全国），
    文件内含 1970 年起全史（基準年链回溯），一次调用取全史。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2015-01）
        end_month: 截止月 'YYYY-MM'（默认至今）
        metric: 'yoy' 同比% | 'mom' 环比% | 'level' 指数原值

    Returns:
        宽表 DataFrame：月份 + 総合/食品/住居/交通・通信/…（数百品目科目）。

    Note:
        e-Stat 直连在中国大陆通常不可达，需设 ``DEEPMACRO_HTTP_PROXY``。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_japan_cpi(start_month='2024-01').tail(3)
    """
    if metric not in ('yoy', 'mom', 'level'):
        raise ValueError("metric 必须为 'yoy' | 'mom' | 'level'")
    start, end = default_range(start_month, end_month, default_start='2015-01')
    levels = fetch_japan_cpi_levels()
    return levels_to_frame(levels, metric, start, end)
