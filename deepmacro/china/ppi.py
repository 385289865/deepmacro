"""中国 PPI — 国家统计局数据发布库 API（data.stats.gov.cn）。

覆盖工业生产者价格全量 49 科目：出厂价格（主指数 + 生产资料/生活资料细项）、
购进价格（9 细项）、30 个工业行业出厂价格。行业节点按官方年代分片
（2007/2014/2018/2026），旧行业名映射到现行名称。

输出：涨跌幅 %（官方指数值 - 100）。
"""
from __future__ import annotations

import logging
from typing import Any

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro.china import _nbs
from deepmacro._http import polite_sleep

_log = logging.getLogger(__name__)

# 出厂价格：主 + 生产资料 + 生活资料（各 1 cid/指标，覆盖全时间范围）
_FACTORY_MAIN_CIDS: dict[str, str] = {
    'yoy': '60e8b361f11c4a878c652a6487a25561',
    'cumulative': 'ddda8028eb234af1b4bdbac093e63bb0',
    'mom': '677cfbb4f06941af8c1761c4804e58cf',
}
_FACTORY_PROD_CIDS: dict[str, str] = {
    'yoy': '186bb90545374d5db94976a03c847632',
    'cumulative': 'e174f5cb6b564bbbb5cd719dc62621d5',
    'mom': '95de7bc49e9b4fecbe2ef17f5f2e6965',
}
_FACTORY_LIVING_CIDS: dict[str, str] = {
    'yoy': 'bb3b8eb571c64341990d21f7a85b7151',
    'cumulative': '583c993be68a9446f99fe10a0e02ae705',
    'mom': '6356e72f60414d07ac29d2747c15dd59',
}

# 购进价格
_PURCHASE_CIDS: dict[str, str] = {
    'yoy': '50f683df1f8b4da9861b7047d5091571',
    'cumulative': '49f9b8ff466743358094d51b8ef00e06',
    'mom': 'bb74f74647214f8baf75f66a30935f3c',
}

# 分行业出厂价格（官方按年代分片）
_INDUSTRY_CIDS: dict[str, list[tuple[str, str, str, str]]] = {
    'yoy': [
        ('2026', '2026-01', '2999-12', '8bc27b5fd28e46df9b8fda8a5d336306'),
        ('2018', '2018-01', '2025-12', 'a9a6bab463334e6c8dc2c66cf114a25e'),
        ('2014', '2014-01', '2017-12', '8c1e3ad3a3a449559f9447918450e193'),
        ('2007', '2007-01', '2013-12', '8c707b6da5df4703abd7278835e8e668'),
    ],
    'cumulative': [
        ('2026', '2026-01', '2999-12', '19965153b36240d8b9d65681858cb444'),
        ('2014', '2000-01', '2025-12', '623c1251a2ec4bf19065d0f2a7a1af95'),
    ],
    'mom': [
        ('2026', '2026-01', '2999-12', 'b69b3df625af46f49dc6898b070d7f43'),
        ('2014', '2000-01', '2025-12', '03d0e12f03c74b29a24a6775ce18a494'),
    ],
}

# API 指标名（去后缀）→ 统一科目名。None = 跳过。
_NAME_MAP: dict[str, str | None] = {
    '': '工业生产者出厂价格',
    '生产资料': '生产资料',
    '采掘业生产资料': '采掘',
    '原料业生产资料': '原材料',
    '加工业生产资料': '加工',
    '生活资料': '生活资料',
    '食品类': '食品',
    '衣着类': '衣着',
    '一般日用品类': '一般日用品',
    '耐用消费品类': '耐用消费品',
    '工业生产者购进价格': '工业生产者购进价格',
    '燃料、动力类': '燃料、动力类',
    '黑色金属材料类': '黑色金属材料类',
    '有色金属材料和电线类': '有色金属材料及电线类',
    '有色金属材料及电线类': '有色金属材料及电线类',
    '化工原料类': '化工原料类',
    '木材及纸浆类': '木材及纸浆类',
    '建筑材料及非金属类': '建筑材料及非金属类',
    '其它工业原材料及半成品类': '其他工业原材料及半成品类',
    '其他工业原材料及半成品类': '其他工业原材料及半成品类',
    '农副产品类': '农副产品类',
    '纺织原料类': '纺织原料类',
    '煤炭开采和洗选业': '煤炭开采和洗选业',
    '石油和天然气开采业': '石油和天然气开采业',
    '黑色金属矿采选业': '黑色金属矿采选业',
    '有色金属矿采选业': '有色金属矿采选业',
    '非金属矿采选业': '非金属矿采选业',
    '开采专业及辅助性活动': None,
    '开采辅助活动': None,
    '其他采矿业': None,
    '农副食品加工业': '农副食品加工业',
    '食品制造业': '食品制造业',
    '酒、饮料及精制茶制造业': '酒、饮料和精制茶制造业',
    '酒、饮料和精制茶制造业': '酒、饮料和精制茶制造业',
    '烟草制品业': '烟草制品业',
    '纺织业': '纺织业',
    '纺织服装、服饰业': '纺织服装、服饰业',
    '皮革、毛皮、羽毛及其制品和制鞋业': '皮革、毛皮、羽毛及其制品和制鞋业',
    '木材加工和木、竹、藤、棕、草制品业': '木材加工和木、竹、藤、棕、草制品业',
    '家具制造业': '家具制造业',
    '造纸和纸制品业': '造纸和纸制品业',
    '印刷和记录媒介复制业': '印刷和记录媒介复制业',
    '文教、工美、体育和娱乐用品制造业': '文教、工美、体育和娱乐用品制造业',
    '石油、煤炭及其他燃料加工业': '石油、煤炭及其他燃料加工业',
    '化学原料和化学制品制造业': '化学原料和化学制品制造业',
    '医药制造业': '医药制造业',
    '化学纤维制造业': '化学纤维制造业',
    '橡胶和塑料制品业': '橡胶和塑料制品业',
    '非金属矿物制品业': '非金属矿物制品业',
    '黑色金属冶炼和压延加工业': '黑色金属冶炼和压延加工业',
    '有色金属冶炼和压延加工业': '有色金属冶炼和压延加工业',
    '金属制品业': '金属制品业',
    '通用设备制造业': '通用设备制造业',
    '专用设备制造业': '专用设备制造业',
    '汽车制造业': '汽车制造业',
    '铁路、船舶、航空航天和其他运输设备制造业': '铁路、船舶、航空航天和其他运输设备制造业',
    '电气机械和器材制造业': '电气机械和器材制造业',
    '计算机、通信和其他电子设备制造业': '计算机、通信和其他电子设备制造业',
    '仪器仪表制造业': '仪器仪表制造业',
    '其他制造业': '其他制造业',
    '废弃资源综合利用业': '废弃资源综合利用业',
    '金属制品、机械和设备修理业': '金属制品、机械和设备修理业',
    '电力、热力生产和供应业': '电力、热力生产和供应业',
    '燃气生产和供应业': '燃气生产和供应业',
    '水的生产和供应业': '水的生产和供应业',
    '饮料制造业': '酒、饮料和精制茶制造业',
    '纺织服装、鞋、帽制造业': '纺织服装、服饰业',
    '皮革、毛皮、羽毛(绒)及其制品业': '皮革、毛皮、羽毛及其制品和制鞋业',
    '木材加工及木、竹、藤、棕、草制品业': '木材加工和木、竹、藤、棕、草制品业',
    '造纸及纸制品业': '造纸和纸制品业',
    '印刷业和记录媒介的复制': '印刷和记录媒介复制业',
    '文教体育用品制造业': '文教、工美、体育和娱乐用品制造业',
    '石油加工、炼焦及核燃料加工业': '石油、煤炭及其他燃料加工业',
    '化学原料及化学制品制造业': '化学原料和化学制品制造业',
    '橡胶制品业': '橡胶和塑料制品业',
    '塑料制品业': '橡胶和塑料制品业',
    '黑色金属冶炼及压延加工业': '黑色金属冶炼和压延加工业',
    '有色金属冶炼及压延加工业': '有色金属冶炼和压延加工业',
    '交通运输设备制造业': '汽车制造业',
    '电气机械及器材制造业': '电气机械和器材制造业',
    '通信设备、计算机及其他电子设备制造': '计算机、通信和其他电子设备制造业',
    '仪器仪表及文化、办公用机械制造业': '仪器仪表制造业',
    '工艺品及其他制造业': '其他制造业',
    '废弃资源和废旧材料回收加工业': '废弃资源综合利用业',
    '电力、热力的生产和供应业': '电力、热力生产和供应业',
    '其他采矿业工业生产者出厂价格': None,
}


def _strip_indicator_name(api_name: str) -> str:
    name = api_name
    if name == '工业生产者购进价格指数':
        return '工业生产者购进价格'
    for suffix in ('工业生产者出厂价格指数', '购进价格指数'):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def _map_name(api_name: str) -> str | None:
    clean = _strip_indicator_name(api_name)
    return _NAME_MAP.get(clean, clean)


def _query_cid(cid: str, period_range: str,
               result: dict[str, dict[str, float]]) -> None:
    indicators = _nbs.query_indicators(cid)
    iids = [it['iid'] for it in indicators if it.get('iid')]
    if not iids:
        return
    polite_sleep()
    raw = _nbs.query_data(cid, iids, period_range)
    iid_to_subject = {}
    for it in indicators:
        subject = _map_name(it['name'])
        if subject is not None:
            iid_to_subject[it['iid']] = subject
    for time_code, values in raw.items():
        month = _nbs.period_to_month(time_code)
        bucket = result.setdefault(month, {})
        for iid, val_str in values.items():
            subject = iid_to_subject.get(iid)
            if subject:
                try:
                    bucket[subject] = float(val_str) - 100.0
                except (ValueError, TypeError):
                    pass


def macro_china_ppi(
    start_month: str | None = None,
    end_month: str | None = None,
    metric: str = 'yoy',
) -> Any:
    """中国工业生产者价格指数（PPI）— 国家统计局官方口径，全量 49 科目。

    覆盖出厂价格（主指数/生产资料/生活资料）、购进价格、30 个工业行业；
    行业节点按官方年代分片（2007/2014/2018/2026）自动拼接。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2011-01，行业分片最早 2007-01）
        end_month: 截止月 'YYYY-MM'（默认至今）
        metric: 'yoy' 同比 | 'mom' 环比 | 'cumulative' 累计同比

    Returns:
        宽表 DataFrame：月份 + 各科目（工业生产者出厂价格/生产资料/采掘/
        煤炭开采和洗选业/…），数值为涨跌幅 %。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_china_ppi(start_month='2024-01').tail(3)
    """
    if metric not in ('yoy', 'mom', 'cumulative'):
        raise ValueError("metric 必须为 'yoy' | 'mom' | 'cumulative'")
    start = start_month or '2011-01'
    end = end_month or _current_month()
    period_range = f'{_nbs.month_to_period(start)}-{_nbs.month_to_period(end)}'

    result: dict[str, dict[str, float]] = {}
    _query_cid(_FACTORY_MAIN_CIDS[metric], period_range, result)
    _query_cid(_FACTORY_PROD_CIDS[metric], period_range, result)
    _query_cid(_FACTORY_LIVING_CIDS[metric], period_range, result)
    _query_cid(_PURCHASE_CIDS[metric], period_range, result)

    for _, slice_start, slice_end, cid in _INDUSTRY_CIDS.get(metric, []):
        parts = period_range.split('-')
        req_start = max(parts[0][:6], slice_start.replace('-', '')[:6])
        req_end = min(parts[-1][:6], slice_end.replace('-', '')[:6])
        if req_start > req_end:
            continue
        sub_range = (f'{req_start}MM-{req_end}MM' if req_start != req_end
                     else f'{req_start}MM')
        try:
            _query_cid(cid, sub_range, result)
        except Exception as e:
            _log.warning('PPI industry %s failed: %s', cid, e)

    if not result:
        raise RuntimeError('国家统计局 PPI 接口返回无数据（可能触发反爬，稍后重试）')
    df = to_wide_frame(result)
    return clip_months(df, start, end)


def _current_month() -> str:
    from datetime import datetime
    now = datetime.now()
    return f'{now.year}-{now.month:02d}'
