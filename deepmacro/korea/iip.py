"""韩国工业生产指数 — KOSIS 官方源（统计厅 광공업생산지수，Playwright 管线）。"""
from __future__ import annotations

from typing import Any

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro.japan._sources import default_range, levels_to_frame
from deepmacro.korea._parse import _extend_start, fetch_korea_iip


def macro_korea_iip(
    start_month: str | None = None,
    end_month: str | None = None,
    metric: str = 'level',
) -> Any:
    """韩国矿业工业生产指数 — KOSIS 统计厅官方口径（2020=100）。

    DT_1F02001（시도·산업별 광공업생산지수，전국），115 산업 3 级分类，
    생산지수 원지수/계절조정双口径。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2018-01，最早 2018-01）
        end_month: 截止月 'YYYY-MM'（默认至今）
        metric: 'level' 原指数（默认）| 'sa' 季调指数 | 'yoy' 同比% | 'mom' 环比%

    Returns:
        宽表 DataFrame：月份 + 광공업생산지수/제조업/반도체 제조업 等
        산업科目（韩文官方原名）。

    Note:
        可选依赖同 ``macro_korea_cpi``（deepmacro[korea] + chromium）。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_korea_iip(start_month='2024-01').tail(3)
    """
    if metric not in ('level', 'sa', 'yoy', 'mom'):
        raise ValueError("metric 必须为 'level' | 'sa' | 'yoy' | 'mom'")
    start, end = default_range(start_month, end_month, default_start='2018-01')
    by_metric = fetch_korea_iip(_extend_start(start, metric), end)
    key = 'level_sa' if metric == 'sa' else 'level'
    levels = by_metric.get(key) or {}
    if not levels:
        raise RuntimeError('KOSIS DT_1F02001 导出无数据')
    if metric in ('yoy', 'mom'):
        from deepmacro.japan._sources import _derive
        by_month: dict[str, dict[str, float]] = {}
        for name, d in levels.items():
            for month, v in d.items():
                by_month.setdefault(month, {})[name] = v
        by_month = _derive(by_month, metric)
        return clip_months(to_wide_frame(by_month), start, end)
    return levels_to_frame(levels, 'level', start, end)
