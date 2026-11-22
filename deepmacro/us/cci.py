"""美国消费者信心指数 — 密歇根大学 UMCSENT（FRED 官方托管，1952 起）。"""
from __future__ import annotations

from typing import Any

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro.us._sources import fetch_fred_series


def macro_us_cci(
    start_month: str | None = None,
    end_month: str | None = None,
) -> Any:
    """美国消费者信心指数 — 密歇根大学 UMCSENT（FRED 托管，1952-11 起月度）。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 5 年前）
        end_month: 截止月 'YYYY-MM'（默认至今）

    Returns:
        宽表 DataFrame：月份 + 密歇根大学消费者信心指数（SA 水平值）。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_us_cci(start_month='2024-01').tail(3)
    """
    from deepmacro.us.cpi import _default_range
    start, end = _default_range(start_month, end_month)
    data = fetch_fred_series('UMCSENT',
                             observation_start=f'{int(start[:4]) - 1}-01-01')
    by_month = {m: {'密歇根大学消费者信心指数': v} for m, v in data.items()}
    if not by_month:
        raise RuntimeError('FRED 返回无消费者信心数据（检查网络或代理设置）')
    return clip_months(to_wide_frame(by_month), start, end)
