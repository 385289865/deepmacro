"""韩国 CPI — KOSIS 官方源（统计厅 소비자물가지수，Playwright 管线）。"""
from __future__ import annotations

from typing import Any

from deepmacro.japan._sources import default_range, levels_to_frame
from deepmacro.korea._parse import KR_CPI_EARLIEST, fetch_korea_cpi


def macro_korea_cpi(
    start_month: str | None = None,
    end_month: str | None = None,
    metric: str = 'yoy',
) -> Any:
    """韩国消费者物价指数（CPI）— KOSIS 统计厅官方口径（2020=100）。

    DT_1J22001（지출목적별，전국），지출目的分类全科目（约 460+ 科目：
    총지수/식료품·비주류음료/주류·담배/…/품목 포함）。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2015-01；最早 2013-01）
        end_month: 截止月 'YYYY-MM'（默认至今）
        metric: 'yoy' 同比% | 'mom' 环比% | 'level' 指数原值

    Returns:
        宽表 DataFrame：月份 + 총지수/대중소류/품목科目（韩文官方原名）。

    Note:
        可选依赖：``pip install "deepmacro[korea]"`` 且首次使用前执行
        ``playwright install chromium``。KOSIS 按 IP 对浏览器会话限速，
        大陆网络建议设 ``DEEPMACRO_HTTP_PROXY``；单表首次加载 1-4 分钟。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_korea_cpi(start_month='2024-01').tail(3)
    """
    if metric not in ('yoy', 'mom', 'level'):
        raise ValueError("metric 必须为 'yoy' | 'mom' | 'level'")
    start, end = default_range(start_month, end_month, default_start='2015-01')
    levels = fetch_korea_cpi(start, end)
    return levels_to_frame(levels, metric, start, end)
