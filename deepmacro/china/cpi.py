"""中国 CPI — 国家统计局数据发布库 API（data.stats.gov.cn）。

覆盖居民消费价格指数全量科目：主指数 + 8 大类 + 食品烟酒/衣着/居住等细项
+ 城市/农村。基期每 5 年轮换（2011/2016/2021/2026），各基期对应独立 cid
节点，跨基期科目名映射到统一口径。

输出：涨跌幅 %（官方指数值 - 100，如 99.9 → -0.1）。
"""
from __future__ import annotations

import logging
from typing import Any

from deepmacro._frame import clip_months, to_wide_frame
from deepmacro.china import _nbs
from deepmacro._http import polite_sleep

_log = logging.getLogger(__name__)

# ===== NBS API cid 映射（源自 factorlab 生产逆向） =====
# 主 cid：主指数 + 8 大类 + 汇总指标（非食品/消费品/服务/核心）
_MAIN_CIDS: dict[str, dict[str, str]] = {
    'yoy': {
        '2026': '5c7452825c7c4dcba391db5ca7f335c5',
        '2021': '809d2522b0fe4be89142650341b19083',
        '2016': '9d4eec43537742a7ab5d63db97fa2f51',
        '2011': '954cfd7597e34b919ec71caf6aeead51',
    },
    'cumulative': {
        '2026': '8bdfe475d35f49b9aa7c7afe66d86136',
        '2021': 'b0fa5fe955734cd8ad0edf111f0f5aa2',
        '2016': '1fc05dd677a84f75b94e7f4e904a2b4f',
        '2011': '3179c6cb9e754109a5841cf936869a69',
    },
    'mom': {
        '2026': 'b4fad2cf9e0e4af7815b7e9e2e95c5c7',
        '2021': 'e6664817f0cd427783cd397770695634',
        '2016': '42132fae9f2244818f0480b4c422615c',
        '2011': 'bc985d1741a94451880c606022a8fe00',
    },
}

# 城市/农村 cid（提取主指数）
_CITY_CIDS: dict[str, dict[str, str]] = {
    'yoy': {
        '2026': '00e2069e2f344a0d8ac74f36e503a163',
        '2021': 'a1443cc6528747cd82966d96293c6488',
        '2016': '315e4e85263a45e3b3725e2d81262dc3',
        '2011': '02b8333b3f854379bf59d7191627451b',
    },
    'cumulative': {
        '2026': '8676ea44a664421b9bd5a02f45720e9d',
        '2021': 'c33f98b4f8df432aaee005bfbbfff631',
        '2016': '6d4dfe6ea9c34c18b2819149ebc61352',
        '2011': '8c36703858de4036a340c02055137727',
    },
    'mom': {
        '2026': '59980b085bda4192a02282f45439ab43',
        '2021': 'bbc94437c82247e68591874cf02a8969',
        '2016': '3064ad2c484f4a32b8917bf2661d3385',
        '2011': '5d767f323c1b427684637dc1a027cef1',
    },
}
_RURAL_CIDS: dict[str, dict[str, str]] = {
    'yoy': {
        '2026': '4f1933b176c14acda5bd8c63f16fe82d',
        '2021': '3aee0d7a51de476181ca7f329263ce7e',
        '2016': 'b7ec369d8c4a40949e29124407e81f89',
        '2011': '3d7d995b84154b2687426b7d091ce699',
    },
    'cumulative': {
        '2026': '609bc72ec6f8460ba3eb50c39f58b078',
        '2021': '6198747912cf4fdc85dad87eb187cd61',
        '2016': '7234721f30f7420e8a6100b28db234f5',
        '2011': '9b2fded4355345a793c628744a3df0b32',
    },
    'mom': {
        '2026': '34176def62714d3ba69a46e00b3b8586',
        '2021': '13569525937c4700ac1cccbb027c8910',
        '2016': '773345f5dfb24ec3a4fd9c1c9eb0254c',
        '2011': 'c1f61a377a334d87bf3c77d90c3fa14a',
    },
}

# 细项 cid（各大类下的子科目，全国）
_SUBITEM_CIDS: dict[str, dict[str, str]] = {
    'yoy': {
        '食品类': 'deccae3d6ecb4967809ab21a3c679c7f',
        '居住类': '25f1110e11d945f9b27950768e4e0fa7',
        '衣着类': '5afd500b19204853b1f9118c0caaf0a4',
        '生活用品及服务': '774bfe8ce68a427dab17a35d121289b4',
        '交通通信': '53b231f9bb5349399fcb510adbac2008',
        '教育文化娱乐': 'f3e298b5c1c54a36aedc7113fe825af6',
        '医疗保健': '6ff134dd65764528a9e91dd4b5279fe6',
    },
    'cumulative': {
        '食品类': 'a060ca4910d542ee9e341b5597b1b394',
        '居住类': '902812961f3a4c208f176a2271545018',
        '衣着类': '059d952ca58d40bbaab479daec52e3be',
        '生活用品及服务': '0bf41484921349eaad6ac7e4b9358fbd',
        '交通通信': '6959ebb22e3a4ace95d568265e9d2044',
        '教育文化娱乐': '20ef8a6613874e39a34ae57dd754f91b',
        '医疗保健': 'c289be64d4d6454b95212c3c04239b4b',
    },
    'mom': {
        '食品类': '5e9835d543fb483f891ee928931b9bb1',
        '居住类': '6e2cd91668b84d9794dfecb4607f6643',
        '衣着类': '75a4a811a1a74f77848cb74f777fde70',
        '生活用品及服务': 'fa48f116c0c6149c18a658c303f8d73a2',
        '交通通信': '2125c668ef3a43e4ae1bd71ad0e8f92b',
        '教育文化娱乐': 'a6de76a48e094c859cc3dd5085595d0d',
        '医疗保健': '7ed9a31641274dbe98eadd1db5795a01',
    },
}

# 基期边界（各基期 cid 节点的数据覆盖区间，跨基期查询按此切片）
_BASE_PERIOD_RANGES: list[tuple[str, str, str]] = [
    # (基期, 起始月, 结束月|None=开放)
    ('2011', '1990-01', '2015-12'),
    ('2016', '2016-01', '2020-12'),
    ('2021', '2021-01', '2025-12'),
    ('2026', '2026-01', None),
]

# API 指标名（去基期后缀）→ 统一科目名。None = 跳过。
_NAME_MAP: dict[str, str | None] = {
    '': '居民消费价格',
    '非食品': '非食品',
    '消费品': '其中：消费品',
    '服务': '服务',
    '不包括食品和能源': '其中：不包括食品和能源',
    '食品烟酒': '一、食品烟酒及在外餐饮',
    '食品烟酒及在外餐饮': '一、食品烟酒及在外餐饮',
    '食品': '一、食品烟酒及在外餐饮',
    '衣着': '二、衣着',
    '居住': '三、居住',
    '生活用品及服务': '四、生活用品及服务',
    '交通通信': '五、交通通信',
    '交通和通信': '五、交通通信',
    '教育文化娱乐': '六、教育文化娱乐',
    '教育文化和娱乐': '六、教育文化娱乐',
    '医疗保健': '七、医疗保健',
    '其他用品及服务': '八、其他用品及服务',
    '其他用品和服务': '八、其他用品及服务',
    '烟酒及用品': None,
    '家庭设备用品及维修服务': '四、生活用品及服务',
    '医疗保健和个人用品': '七、医疗保健',
    '交通和通讯': '五、交通通信',
    '娱乐教育文化用品及服务': '六、教育文化娱乐',
    '食品类': None,
    '粮食': '粮食',
    '粮食类': '粮食',
    '畜肉类': None,
    '畜肉': None,
    '肉禽及其制品': None,
    '肉禽及其制品类': None,
    '蛋': '蛋类',
    '蛋类': '蛋类',
    '水产品': '水产品',
    '水产品类': '水产品',
    '鲜菜': '鲜菜',
    '鲜菜类': '鲜菜',
    '鲜果': '鲜果',
    '鲜果类': '鲜果',
    '食用油': '食用油',
    '油脂': '食用油',
    '猪肉': '其中：猪肉',
    '牛肉': '牛肉',
    '羊肉': '羊肉',
    '奶类': '奶类',
    '奶': '奶类',
    '液体乳及乳制品': '奶类',
    '卷烟': '卷烟',
    '烟草': '卷烟',
    '酒': '酒类',
    '酒类': '酒类',
    '服装': '服装',
    '鞋': '鞋类',
    '鞋类': '鞋类',
    '租赁房房租': '租赁房房租',
    '住房租金': '租赁房房租',
    '水电燃料': '水电燃料',
    '家用器具': '家用器具',
    '耐用消费品': '家用器具',
    '家庭服务': '家庭服务',
    '家庭服务及加工维修服务': '家庭服务',
    '小汽车': '小汽车',
    '交通工具': '小汽车',
    '交通工具用能源': '交通工具用能源',
    '交通工具用燃料': '交通工具用能源',
    '车用燃料及零配件': '交通工具用能源',
    '交通工具使用和维修': '交通工具使用和维修',
    '通信工具': '通信工具',
    '通信服务': '通信服务',
    '邮递服务': '邮递服务',
    '教育服务': '教育服务',
    '旅行社及其他旅游服务': '旅行社及其他旅游服务',
    '旅游': '旅行社及其他旅游服务',
    '中药': '中药',
    '中药材及中成药': '中药',
    '西药': '西药',
    '医疗服务': '医疗服务',
    '医疗保健服务': '医疗服务',
}


def _strip_indicator_name(api_name: str) -> str:
    """'食品烟酒类居民消费价格指数' → '食品烟酒'（去指数后缀 + '类' 后缀）。"""
    name = api_name
    if name.endswith('居民消费价格指数'):
        name = name[:-len('居民消费价格指数')]
    if name.endswith('类'):
        name = name[:-1]
    return name


def _map_name(api_name: str) -> str | None:
    clean = _strip_indicator_name(api_name)
    return _NAME_MAP.get(clean, clean)


def _query_cid(cid: str, period_range: str,
               result: dict[str, dict[str, float]]) -> None:
    """单 cid 查询并入 result（原地填充 {month: {subject: pct}}）。"""
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
        if month not in result:
            result[month] = {}
        for iid, val_str in values.items():
            subject = iid_to_subject.get(iid)
            if subject:
                try:
                    result[month][subject] = float(val_str) - 100.0
                except (ValueError, TypeError):
                    pass


def _query_region(cid_map: dict[str, str], subject: str,
                  period_range: str, result: dict[str, dict[str, float]]) -> None:
    """城市/农村 cid：提取主指数 → 指定科目名。"""
    for base_period, cid in cid_map.items():
        sub_range = _clip_range(period_range, base_period)
        if not sub_range:
            continue
        try:
            indicators = _nbs.query_indicators(cid)
            main_iid = next((it['iid'] for it in indicators
                             if _strip_indicator_name(it['name'])
                             in (subject, subject.replace('其中：', ''))), None)
            if not main_iid:
                continue
            polite_sleep()
            raw = _nbs.query_data(cid, [main_iid], sub_range)
            for time_code, values in raw.items():
                month = _nbs.period_to_month(time_code)
                try:
                    result.setdefault(month, {})[subject] = \
                        float(values[main_iid]) - 100.0
                except (ValueError, TypeError):
                    pass
        except Exception as e:
            _log.warning('CPI region %s %s failed: %s', subject, base_period, e)


def _clip_range(period_range: str, base_period: str) -> str | None:
    """将请求区间裁剪到该基期的数据覆盖区间；无交集返回 None。"""
    bp = next((b for b in _BASE_PERIOD_RANGES if b[0] == base_period), None)
    if bp is None:
        return period_range
    _, bp_start, bp_end = bp
    parts = period_range.split('-')
    req_start, req_end = parts[0], parts[-1]
    start = max(req_start[:6], bp_start.replace('-', '')[:6])
    end = min(req_end[:6], (bp_end or '2999-12').replace('-', '')[:6])
    if start > end:
        return None
    return f'{start}MM-{end}MM' if start != end else f'{start}MM'


def macro_china_cpi(
    start_month: str | None = None,
    end_month: str | None = None,
    metric: str = 'yoy',
) -> Any:
    """中国居民消费价格指数（CPI）— 国家统计局官方口径，全量科目。

    直连 data.stats.gov.cn 数据发布库接口，跨 2011/2016/2021/2026 四个基期
    自动切片拼接，科目名跨基期对齐。

    Args:
        start_month: 起始月 'YYYY-MM'（默认 2011-01；接口数据可回溯至 1990 年代）
        end_month: 截止月 'YYYY-MM'（默认至今）
        metric: 'yoy' 同比 | 'mom' 环比 | 'cumulative' 累计同比

    Returns:
        宽表 DataFrame：月份 + 各科目（居民消费价格/一、食品烟酒及在外餐饮/
        其中：猪肉/…），数值为涨跌幅 %。

    Example:
        >>> import deepmacro as dm
        >>> dm.macro_china_cpi(start_month='2024-01').tail(3)
    """
    if metric not in ('yoy', 'mom', 'cumulative'):
        raise ValueError("metric 必须为 'yoy' | 'mom' | 'cumulative'")
    start = start_month or '2011-01'
    end = end_month or _current_month()
    period_range = f'{_nbs.month_to_period(start)}-{_nbs.month_to_period(end)}'

    result: dict[str, dict[str, float]] = {}
    # 1. 主 cid + 细项 cid（按基期切片查询）
    for base_period, _, _ in _BASE_PERIOD_RANGES:
        sub_range = _clip_range(period_range, base_period)
        if not sub_range:
            continue
        _query_cid(_MAIN_CIDS[metric][base_period], sub_range, result)
        for category, cid in _SUBITEM_CIDS.get(metric, {}).items():
            try:
                _query_cid(cid, sub_range, result)
            except Exception as e:
                _log.warning('CPI %s subitem %s failed: %s', metric, category, e)
    # 2. 城市/农村主指数
    _query_region(_CITY_CIDS[metric], '城市', period_range, result)
    _query_region(_RURAL_CIDS[metric], '农村', period_range, result)

    if not result:
        raise RuntimeError('国家统计局 CPI 接口返回无数据（可能触发反爬，稍后重试）')
    df = to_wide_frame(result)
    return clip_months(df, start, end)


def _current_month() -> str:
    from datetime import datetime
    now = datetime.now()
    return f'{now.year}-{now.month:02d}'
