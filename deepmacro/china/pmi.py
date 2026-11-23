"""中国采购经理指数 — 国家统计局数据发布库 API（data.stats.gov.cn）。

父节点下三子节点合并：制造业（14 指标）/非制造业（12）/综合 PMI 产出
指数（1）。制造业与非制造业的同名指标（新订单指数 等）以「组名·指标名」
消歧；制造业为核心节点（拉取失败硬报错），其余节点软降级。
"""
from __future__ import annotations

import logging
from typing import Any

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro._http import polite_sleep
from deepmacro.china import _nbs

_log = logging.getLogger(__name__)

# 组名 → cid
_PMI_NODES: dict[str, str] = {
    '制造业': '93ffbb1aa85740d3aa2618371508b606',
    '非制造业': '7a64a6e25aec4a8e9dde044ecd9e2cce',
    '综合': '455378e1c3264a32875768a35ba5de76',
}
# 核心组（制造业）：拉取失败时硬抛而非软降级，防止非制造业/综合挂掉后
# 用户拿到无制造业主指数的部分宽表（失败语义同 fai/ppi：核心必达）。
_CORE_GROUP = '制造业'

# 两组共有名 → 加组前缀；组内独有名保持原名。联网实跑时刷新。
_DUP_CACHE: set[str] | None = None


def _clean_name(api_name: str) -> str:
    name = api_name.strip()
    for suffix in (' (%)', '（%）'):
        if name.endswith(suffix):
            return name[:-len(suffix)].strip()
    return name


def _prefixed(group: str, name: str) -> str:
    dup = _DUP_CACHE or set()
    return f'{group}·{name}' if name in dup else name


def _refresh_dup_names() -> set[str]:
    """联网刷新同名指标缓存（指标列表进程内缓存，主循环零二次请求）。"""
    global _DUP_CACHE
    names: dict[str, int] = {}
    for group, cid in _PMI_NODES.items():
        try:
            for it in _nbs.query_indicators(cid):
                n = _clean_name(it['name'])
                names[n] = names.get(n, 0) + 1
        except Exception as e:
            if group == _CORE_GROUP:
                raise RuntimeError(
                    f'国家统计局 PMI {group} 节点拉取失败'
                    f'（cid={cid}，可能触发反爬，稍后重试）') from e
            _log.warning('PMI %s 节点指标拉取失败（cid=%s）: %s', group, cid, e)
    _DUP_CACHE = {n for n, c in names.items() if c > 1}
    return _DUP_CACHE


def _query_node(group: str, cid: str, period_range: str,
                result: dict[str, dict[str, float]]) -> None:
    indicators = _nbs.query_indicators(cid)
    iid_to_subject = {it['iid']: _prefixed(group, _clean_name(it['name']))
                      for it in indicators if it.get('iid')}
    if not iid_to_subject:
        return
    polite_sleep()
    raw = _nbs.query_data(cid, list(iid_to_subject), period_range)
    for time_code, values in raw.items():
        month = _nbs.period_to_month(time_code)
        bucket = result.setdefault(month, {})
        for iid, val_str in values.items():
            subject = iid_to_subject.get(iid)
            if subject:
                try:
                    bucket[subject] = float(val_str)
                except (ValueError, TypeError):
                    pass


def macro_china_pmi(
    start_month: str | None = None,
    end_month: str | None = None,
) -> Any:
    """中国采购经理指数 — 国家统计局官方口径。

    制造业 PMI + 14 分项、非制造业商务活动 + 12 分项、综合 PMI 产出指数，
    同名分项以「制造业·」「非制造业·」前缀区分。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2020-01，实跑最早月以 head(1) 为准）
        end_month: 截止月 'YYYY-MM'（默认至今）

    Returns:
        宽表 DataFrame：月份 + 各指数列（制造业采购经理指数/
        生产指数/新订单指数/…/综合PMI产出指数），数值为官方 % 。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_china_pmi(start_month='2024-01').tail(3)
    """
    start = start_month or '2020-01'
    end = end_month or _current_month()
    _refresh_dup_names()
    # 等区间（起止同月）实测可用 'YYYYMMMM-YYYYMMMM'（fai 单月验证通过），
    # 无需 ppi 的单 period 特判。
    period_range = f'{_nbs.month_to_period(start)}-{_nbs.month_to_period(end)}'
    result: dict[str, dict[str, float]] = {}
    for group, cid in _PMI_NODES.items():
        try:
            _query_node(group, cid, period_range, result)
        except Exception as e:
            if group == _CORE_GROUP:
                raise RuntimeError(
                    f'国家统计局 PMI {group} 节点拉取失败'
                    f'（cid={cid}，可能触发反爬，稍后重试）') from e
            _log.warning('PMI %s 节点拉取失败（cid=%s）: %s', group, cid, e)
    if not result:
        raise RuntimeError('国家统计局 PMI 接口返回无数据（可能触发反爬，稍后重试）')
    return clip_months(to_wide_frame(result), start, end)


def _current_month() -> str:
    from datetime import datetime
    now = datetime.now()
    return f'{now.year}-{now.month:02d}'
