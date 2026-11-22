"""台湾 PPI — 主计总处 nstatdb 官方口径。"""
from __future__ import annotations

from typing import Any

from deepmacro.japan._sources import default_range, levels_to_frame
from deepmacro.taiwan._nstatdb import TW_PPI_FUNID, fetch_taiwan_levels


def macro_taiwan_ppi(
    start_month: str | None = None,
    end_month: str | None = None,
    metric: str = 'yoy',
) -> Any:
    """台湾生產者物價指數（PPI）— 主计总处官方口径（2021 新编制）。

    nstatdb A030701015 表，101 序列（基本分類），2021-01 起。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2021-01）
        end_month: 截止月 'YYYY-MM'（默认至今）
        metric: 'yoy' 同比% | 'mom' 环比% | 'level' 指数原值

    Returns:
        宽表 DataFrame：月份 + 總指數/大類/中類/小類科目。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_taiwan_ppi(start_month='2023-01').tail(3)
    """
    if metric not in ('yoy', 'mom', 'level'):
        raise ValueError("metric 必须为 'yoy' | 'mom' | 'level'")
    start, end = default_range(start_month, end_month, default_start='2021-01')
    levels = fetch_taiwan_levels(TW_PPI_FUNID)
    return levels_to_frame(levels, metric, start, end)
