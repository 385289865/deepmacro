"""中国工业增加值 — 国家统计局数据发布库 API（data.stats.gov.cn）。

覆盖规模以上工业增加值全量科目：主指标（增长速度）+ 按经济类型分 +
按三大门类分（2020 起）+ 分大类行业（2003 起三段节点，旧行业名对齐现行名）。

官方 1—2 月合并发布、归月 02；1 月无数据。输出：增长速度 %（API 原生即增速，
无需减 100）。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro.china import _nbs
from deepmacro._http import polite_sleep

_log = logging.getLogger(__name__)

# (cid, 节点起始年, 节点结束年|None=至今) — 分段节点按官方标注年代裁剪区间
_NODES: list[tuple[str, int, int | None]] = [
    ('3f2e14f0542348ed9fe02476eca3450b', 2010, None),  # 规上工业增加值增长速度（主指标）
    ('b1d8cee1b413494bab668fbae92d80ac', 2010, None),  # 按经济类型分
    ('2b06ed9af69a441684ef906a27d0faf8', 2020, None),  # 按三大门类分（2020 起）
    ('f581720fd0f04d81a77bccebb429b913', 2018, None),  # 分大类行业（2018-至今）
    ('41f883ee51194da893cb6b8d4186f32b', 2012, 2017),  # 分大类行业（2012-2017）
    ('240338c22ce6470d80754833eae62a73', 2003, 2011),  # 分大类行业（2003-2011）
]

# 指标名后缀 → metric（同比 → yoy / 累计 → cumulative）
_METRIC_SUFFIXES: tuple[tuple[str, str], ...] = (
    ('增加值同比增长(%)', 'yoy'),
    ('增加值累计增长(%)', 'cumulative'),
)

# NBS 去后缀名 → 统一科目名（GB/T 4754 行业分类改名对齐）
_ALIASES: dict[str, str] = {
    '规上工业': '规模以上工业增加值',
    '电力、热力的生产和供应业': '电力、热力生产和供应业',
    '化学原料及化学制品制造业': '化学原料和化学制品制造业',
    '有色金属冶炼及压延加工业': '有色金属冶炼和压延加工业',
    '黑色金属冶炼及压延加工业': '黑色金属冶炼和压延加工业',
    '电气机械及器材制造业': '电气机械和器材制造业',
    '通信设备、计算机及其他电子设备制造业': '计算机、通信和其他电子设备制造业',
}


def _subject_metric(raw: str) -> tuple[str, str] | None:
    """NBS 指标名 → (subject, metric)；无法识别返回 None。"""
    name = re.sub(r'\s+', '', raw)
    for suffix, metric in _METRIC_SUFFIXES:
        if name.endswith(suffix):
            base = name[:-len(suffix)]
            return _ALIASES.get(base, base), metric
    return None


def macro_china_iva(
    start_month: str | None = None,
    end_month: str | None = None,
    metric: str = 'yoy',
) -> Any:
    """中国规模以上工业增加值 — 国家统计局官方口径，全量科目。

    直连 data.stats.gov.cn 数据发布库接口。覆盖主指标、按经济类型
    （国有控股/股份制/外商及港澳台投资/私营）、按三大门类（采矿业/制造业/
    电力热力燃气及水生产供应业）与 40+ 个工业大类行业。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2010-01；行业分项最早 2003-02）
        end_month: 截止月 'YYYY-MM'（默认至今）
        metric: 'yoy' 当月同比 | 'cumulative' 累计同比

    Returns:
        宽表 DataFrame：月份 + 各科目（规模以上工业增加值/国有控股企业/
        采矿业/煤炭开采和洗选业/…），数值为增长速度 %。

    Note:
        官方 1—2 月数据合并发布（归月 02），每年 1 月无数据。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_china_iva(start_month='2024-01').tail(3)
    """
    if metric not in ('yoy', 'cumulative'):
        raise ValueError("metric 必须为 'yoy' | 'cumulative'")
    start = start_month or '2010-01'
    end = end_month or _current_month()

    by_month: dict[str, dict[str, dict[str, float]]] = {}
    start_year, end_year = int(start[:4]), int(end[:4])
    for cid, node_sy, node_ey in _NODES:
        sy, ey = max(start_year, node_sy), min(end_year, node_ey or 9999)
        if sy > ey:
            continue
        rng = (f'{_nbs.month_to_period(max(start, f"{sy}-01"))}-'
               f'{_nbs.month_to_period(min(end, f"{ey}-12"))}')
        try:
            iid_map: dict[str, tuple[str, str]] = {}
            for it in _nbs.query_indicators(cid):
                pair = _subject_metric(it['name'])
                if pair:
                    iid_map[it['iid']] = pair
            if not iid_map:
                continue
            polite_sleep()
            raw = _nbs.query_data(cid, list(iid_map), rng)
            for tcode, values in raw.items():
                month = _nbs.period_to_month(tcode)
                if not (start <= month <= end):
                    continue
                bucket = by_month.setdefault(month, {'yoy': {}, 'cumulative': {}})
                for iid, val in values.items():
                    pair = iid_map.get(iid)
                    if not pair:
                        continue
                    subj, m = pair
                    try:
                        bucket[m][subj] = float(val)
                    except (TypeError, ValueError):
                        continue
        except Exception as e:
            _log.warning('iva node %s failed: %s', cid, e)

    result = {m: buckets[metric] for m, buckets in by_month.items()
              if buckets[metric]}
    if not result:
        raise RuntimeError('国家统计局工业增加值接口返回无数据（可能触发反爬，稍后重试）')
    df = to_wide_frame(result)
    return clip_months(df, start, end)


def _current_month() -> str:
    from datetime import datetime
    now = datetime.now()
    return f'{now.year}-{now.month:02d}'
