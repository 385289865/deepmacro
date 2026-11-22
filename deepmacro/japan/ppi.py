"""日本 PPI — 日本银行国内企業物価指数官方口径。"""
from __future__ import annotations

from typing import Any

from deepmacro.japan._sources import (
    default_range, fetch_japan_ppi_levels, levels_to_frame,
)


def macro_japan_ppi(
    start_month: str | None = None,
    end_month: str | None = None,
    metric: str = 'yoy',
) -> Any:
    """日本国内企業物価指数（PPI）— 日本银行官方口径（2020 基準）。

    BOJ cgpi 月次 zip（約 2020-01 起全史），全系列含分類細項。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2020-01）
        end_month: 截止月 'YYYY-MM'（默认至今）
        metric: 'yoy' 同比% | 'mom' 环比% | 'level' 指数原值

    Returns:
        宽表 DataFrame：月份 + 総平均/大類別/品目細項（数百科目）。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_japan_ppi(start_month='2023-01').tail(3)
    """
    if metric not in ('yoy', 'mom', 'level'):
        raise ValueError("metric 必须为 'yoy' | 'mom' | 'level'")
    start, end = default_range(start_month, end_month, default_start='2020-01')
    levels = fetch_japan_ppi_levels()
    return levels_to_frame(levels, metric, start, end)
