"""美国 PPI — BLS 原生 12 序列 + FRED 官方镜像 headline 4 序列。"""
from __future__ import annotations

import logging
from typing import Any

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro.us._sources import (
    fetch_bls_with_fred_fallback, fetch_fred_series,
)

_log = logging.getLogger(__name__)

US_PPI_BLS_SERIES: dict[str, str] = {
    '核心(剔食品能源贸易)': 'WPSFD49116',   # FD Less Foods, Energy, Trade Services (SA)
    '成品': 'WPSFD49201',                   # Total Finished (SA)
    '成品剔食品能源': 'WPSFD4131',
    'FD商品剔食品能源': 'WPUFD413',
    '贸易服务': 'WPSFD49114',
    '成品分销服务': 'WPUFD49212',
    '成品服务剔分销': 'WPUFD49214',
    '私人资本投资': 'WPUFD49215',
    '私人资本设备': 'WPUFD41312',
    '出口': 'WPUFD49301',
    '政府采购': 'WPUFD49401',
    '个人消费': 'WPUFD49501',
}

US_PPI_FRED_SERIES: dict[str, str] = {
    '最终需求': 'PPIFIS',
    '商品': 'PPIDGS',
    '服务': 'PPIDSS',
    '能源': 'PPIFDE',
}


def macro_us_ppi(
    start_month: str | None = None,
    end_month: str | None = None,
) -> Any:
    """美国 PPI — BLS + FRED 官方镜像，16 科目指数水平。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 5 年前）
        end_month: 截止月 'YYYY-MM'（默认至今）

    Returns:
        宽表 DataFrame：月份 + 最终需求/商品/服务/能源/核心/…（指数水平）。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_us_ppi(start_month='2024-01').tail(3)
    """
    from deepmacro.us.cpi import _default_range
    start, end = _default_range(start_month, end_month)
    all_series = {**US_PPI_BLS_SERIES, **US_PPI_FRED_SERIES}
    data = fetch_bls_with_fred_fallback(list(US_PPI_BLS_SERIES.values()),
                                        int(start[:4]), int(end[:4]))
    for name, sid in US_PPI_FRED_SERIES.items():
        try:
            data[sid] = fetch_fred_series(sid,
                                          observation_start=f'{int(start[:4]) - 1}-01-01')
        except Exception as e:
            _log.warning('US PPI FRED %s failed: %s', sid, e)
    by_month: dict[str, dict[str, float]] = {}
    for name, sid in all_series.items():
        for month, v in data.get(sid, {}).items():
            by_month.setdefault(month, {})[name] = v
    if not by_month:
        raise RuntimeError('BLS/FRED 返回无 PPI 数据（检查网络或代理设置）')
    return clip_months(to_wide_frame(by_month), start, end)
