"""美国 CPI — BLS 官方 API（SA 指数水平，1982-84=100）。"""
from __future__ import annotations

import logging
from typing import Any

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro.us._sources import fetch_bls_with_fred_fallback

_log = logging.getLogger(__name__)

# 科目名 → BLS series ID（CUSR 季调；2026-10 实测验证，源自 factorlab）
US_CPI_SERIES: dict[str, str] = {
    '所有项目': 'CUSR0000SA0',
    '核心': 'CUSR0000SA0L1E',           # 所有项目剔除食品和能源
    '商品(剔食品能源)': 'CUSR0000SACL1E',
    '食品': 'CUSR0000SAF1',
    '能源': 'CUSR0000SA0E',
    '汽油': 'CUSR0000SETB01',
    '食品与饮料': 'CUSR0000SAF11',
    '住房': 'CUSR0000SAH',
    '服装': 'CUSR0000SAA',
    '交通运输': 'CUSR0000SAT',
    '医疗保健': 'CUSR0000SAM',
    '娱乐': 'CUSR0000SAR',
    '教育与通信': 'CUSR0000SAE',
    '其他商品与服务': 'CUSR0000SAS',
    '住所': 'CUSR0000SAH1',
    '外食': 'CUSR0000SEFV',
    '新车': 'CUSR0000SETA01',
    '电力': 'CUSR0000SEHF01',
    '管道天然气': 'CUSR0000SEHG01',
}


def macro_us_cpi(
    start_month: str | None = None,
    end_month: str | None = None,
) -> Any:
    """美国 CPI — BLS 官方 API，19 科目指数水平（SA，1982-84=100）。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 5 年前）
        end_month: 截止月 'YYYY-MM'（默认至今）

    Returns:
        宽表 DataFrame：月份 + 所有项目/核心/食品/能源/住房/…（指数水平）。

    Note:
        BLS 直连在中国大陆通常被拦，需设 ``DEEPMACRO_HTTP_PROXY``；
        失败时自动降级 FRED 官方镜像逐序列取数。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_us_cpi(start_month='2024-01').tail(3)
    """
    start, end = _default_range(start_month, end_month)
    data = fetch_bls_with_fred_fallback(list(US_CPI_SERIES.values()),
                                        int(start[:4]), int(end[:4]))
    by_month: dict[str, dict[str, float]] = {}
    for name, sid in US_CPI_SERIES.items():
        for month, v in data.get(sid, {}).items():
            by_month.setdefault(month, {})[name] = v
    if not by_month:
        raise RuntimeError('BLS/FRED 返回无 CPI 数据（检查网络或代理设置）')
    return clip_months(to_wide_frame(by_month), start, end)


def _default_range(start: str | None, end: str | None,
                   years: int = 5) -> tuple[str, str]:
    from datetime import datetime
    now = datetime.now()
    end = end or f'{now.year}-{now.month:02d}'
    if start is None:
        start = f'{now.year - years}-{now.month:02d}'
    return start, end
