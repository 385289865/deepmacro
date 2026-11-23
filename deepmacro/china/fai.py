"""中国固定资产投资 — 国家统计局数据发布库 API（data.stats.gov.cn）。

父节点「固定资产投资 (不含农户)」下 10 个有效子节点合并：
概况/增速/按行业分（2018-、2012-2017、1998-2003 三段；2004-2011 节点
为空已跳过）/登记注册类型（-2023、2024- 两段）/民间投资/资金来源/计划总投资。
分年代节点按官方区间裁剪（模式同 PPI 行业分片）。

数值为官方累计同比 %（个别为绝对量，列名自带单位说明）。
"""
from __future__ import annotations

import logging
from typing import Any

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro._http import polite_sleep
from deepmacro.china import _nbs

_log = logging.getLogger(__name__)

# (标签, 区间起, 区间止, cid)。区间为该节点官方数据年代，请求时与用户窗口取交集。
_FAI_NODES: list[tuple[str, str, str, str]] = [
    ('概况', '1998-01', '2999-12', '5129067b149d4ddfbec1ffc478d35bfb'),
    ('增速', '1998-01', '2999-12', 'aac38c7aa152478ebea254ac412aa0a1'),
    ('按行业分2018', '2018-01', '2999-12', '9002859582574437a91914e0fd50166b'),
    ('按行业分2012-17', '2012-01', '2017-12', 'f4fc1914288b443a82dc9fb793e074c2'),
    ('按行业分1998-2003', '1998-01', '2003-12', '75b0c9fbfd044a019a879f4d42295c99'),
    ('登记注册-2023', '1998-01', '2023-12', '8f28f720ba374e61adba29e79829c31d'),
    ('登记注册2024-', '2024-01', '2999-12', '6158d027f4804bfd9b19e36ce4cb5eda'),
    ('民间投资', '1998-01', '2999-12', 'a3136f1cd88442efa209740d78bba410'),
    ('资金来源', '1998-01', '2999-12', '88077f9647ef499bac2e2e3350e3bab1'),
    ('计划总投资', '1998-01', '2999-12', '2bf47029b8f84dacaddb8f0e5b93583c'),
]

# 核心节点（概况）：拉取失败时硬抛而非软降级，防止 9/10 节点挂掉后
# 用户拿到无信号的部分宽表（失败语义同 ppi：核心必达，分段可选）。
_CORE_CID = '5129067b149d4ddfbec1ffc478d35bfb'


def _clean_name(api_name: str) -> str:
    name = api_name.strip()
    for suffix in (' (%)', '（%）'):
        if name.endswith(suffix):
            return name[:-len(suffix)].strip()
    return name


def _query_node(cid: str, period_range: str,
                result: dict[str, dict[str, float]]) -> None:
    indicators = _nbs.query_indicators(cid)
    iid_to_subject = {it['iid']: _clean_name(it['name'])
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


def macro_china_fai(
    start_month: str | None = None,
    end_month: str | None = None,
) -> Any:
    """中国固定资产投资（不含农户）— 国家统计局官方口径，全量科目。

    10 个 NBS 子节点合并：概况/增速/按行业分（三段拼接至 1998）/登记注册
    类型/民间投资/资金来源/计划总投资，同名科目跨年代节点自动衔接。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2011-01，行业分片最早 1998-01）
        end_month: 截止月 'YYYY-MM'（默认至今）

    Returns:
        宽表 DataFrame：月份 + 各科目（固定资产投资额累计增长/
        民间固定资产投资累计增长/农、林、牧、渔业…），数值为官方口径。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_china_fai(start_month='2024-01').tail(3)
    """
    start = start_month or '2011-01'
    end = end_month or _current_month()
    result: dict[str, dict[str, float]] = {}
    for _, seg_start, seg_end, cid in _FAI_NODES:
        req_start = max(start, seg_start)
        req_end = min(end, seg_end)
        if req_start > req_end:
            continue
        # 等区间（单月窗口）实测可用 'YYYYMMMM-YYYYMMMM' 格式（2024-06 单月
        # 验证通过），无需 ppi.py 的单 period 特判。
        sub_range = (f'{_nbs.month_to_period(req_start)}-'
                     f'{_nbs.month_to_period(req_end)}')
        try:
            _query_node(cid, sub_range, result)
        except Exception as e:
            if cid == _CORE_CID:
                raise RuntimeError(
                    f'FAI 核心节点（概况）{cid} 拉取失败: {e}'
                    '（可能触发反爬，稍后重试）') from e
            _log.warning('FAI 节点 %s 拉取失败: %s', cid, e)
    if not result:
        raise RuntimeError('国家统计局固投接口返回无数据（可能触发反爬，稍后重试）')
    return clip_months(to_wide_frame(result), start, end)


def _current_month() -> str:
    from datetime import datetime
    now = datetime.now()
    return f'{now.year}-{now.month:02d}'
