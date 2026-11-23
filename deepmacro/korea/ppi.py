"""韩国 PPI — KOSIS 官方源（韩国银行 생산자물가지수，Playwright 管线）。"""
from __future__ import annotations

from typing import Any

from deepmacro.japan._sources import default_range, levels_to_frame
from deepmacro.korea._parse import _extend_start, fetch_korea_ppi


def macro_korea_ppi(
    start_month: str | None = None,
    end_month: str | None = None,
    metric: str = 'yoy',
) -> Any:
    """韩国生产者物价指数（PPI）— KOSIS 韩国银行官方口径（2020=100）。

    DT_404Y014（생산자물가지수 기본분류），526 科目 6 级分类
    （총지수→대5→중27→소69→세138→품목286）。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2015-01；最早 2013-01）
        end_month: 截止月 'YYYY-MM'（默认至今）
        metric: 'yoy' 同比% | 'mom' 环比% | 'level' 指数原值

    Returns:
        宽表 DataFrame：月份 + 총지수/대중소세류/품목科目（韩文官方原名）。

    Note:
        可选依赖同 ``macro_korea_cpi``（deepmacro[korea] + chromium）。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_korea_ppi(start_month='2024-01').tail(3)
    """
    if metric not in ('yoy', 'mom', 'level'):
        raise ValueError("metric 必须为 'yoy' | 'mom' | 'level'")
    start, end = default_range(start_month, end_month, default_start='2015-01')
    levels = fetch_korea_ppi(_extend_start(start, metric), end)
    return levels_to_frame(levels, metric, start, end)
