"""国家统计局数据发布库 API 客户端（data.stats.gov.cn 新版接口）。

接口结构（2026-10 逆向探测，源自 factorlab 项目生产验证）：

- 目录树：GET ``/new/queryIndexTreeAsync?pid=&code=1``（code: 1=月度 2=季度 3=年度）
- 指标列表：GET ``/new/queryIndicatorsByCid?cid={cid}&dt=&name=``
- 数值：POST ``/stream/esData``（JSON body，indicatorIds/das/dts 为 list）

WZWS 反爬：短时高频请求会返回 JS 挑战页（HTML 而非 JSON）。本客户端用
Session 复用 cookie + 挑战检测重试应对；仍失败时若配置了代理则代理兜底。

时间编码：'YYYY-MM' → 'YYYYMMMM'（如 2025-03 → '202503MM'）。
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

from deepmacro._http import get_proxies, is_wzws_challenge

_log = logging.getLogger(__name__)

NBS_API_BASE = 'https://data.stats.gov.cn/dg/website/publicrelease/web/external'
NBS_SOURCE_URL = 'https://data.stats.gov.cn/'

_NBS_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Referer': 'https://data.stats.gov.cn/dg/website/page.html',
}

_SESSION: requests.Session | None = None
_INDICATOR_CACHE: dict[str, list[dict]] = {}


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.headers.update(_NBS_HEADERS)
        _SESSION = s
    return _SESSION


def _is_challenge(resp: requests.Response) -> bool:
    ct = resp.headers.get('content-type', '')
    if 'json' in ct:
        return False
    return is_wzws_challenge(resp.text[:2048] if resp.text else '')


def _request(method: str, url: str, max_retries: int = 3,
             params: dict[str, Any] | None = None,
             json_body: dict[str, Any] | None = None) -> dict:
    """NBS API 请求（WZWS 检测 + 重试 + 代理兜底）→ JSON dict。"""
    session = _session()
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = session.request(method, url, timeout=30,
                                   params=params, json=json_body)
            if _is_challenge(resp):
                _log.warning('NBS WZWS challenge on %s (attempt %d/%d)',
                             url, attempt + 1, max_retries + 1)
            else:
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            last_exc = e
            _log.warning('NBS %s %s failed (attempt %d/%d): %s',
                         method, url, attempt + 1, max_retries + 1, e)
        if attempt < max_retries:
            time.sleep(random.uniform(5, 10))

    proxies = get_proxies()
    if proxies:
        try:
            resp = session.request(method, url, timeout=30,
                                   params=params, json=json_body, proxies=proxies)
            if not _is_challenge(resp):
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            _log.warning('NBS request via proxy failed: %s', e)
    if last_exc:
        raise last_exc
    raise RuntimeError(f'NBS WZWS challenge persisted on {url} after {max_retries} retries')


def query_tree(pid: str = '', code: str = '1') -> list[dict]:
    """查询 NBS 目录树子节点（code: 1=月度数据 2=季度 3=年度）。

    返回 [{_id, name, isLeaf, sdate, edate}, ...]。
    """
    data = _request('GET', f'{NBS_API_BASE}/new/queryIndexTreeAsync',
                    params={'pid': pid, 'code': code})
    return data.get('data', []) if isinstance(data, dict) else data or []


def query_indicators(cid: str, refresh: bool = False) -> list[dict]:
    """查询 cid 下的指标列表（进程内缓存）。

    返回 [{iid, name}, ...]，name 已去除 "(上年同月=100)" 等基期后缀。
    """
    if not refresh and cid in _INDICATOR_CACHE:
        return _INDICATOR_CACHE[cid]
    data = _request('GET', f'{NBS_API_BASE}/new/queryIndicatorsByCid',
                    params={'cid': cid, 'dt': '', 'name': ''})
    raw = data.get('data', {})
    raw_list = raw.get('list', []) if isinstance(raw, dict) else raw or []
    result = []
    for item in raw_list:
        name = (item.get('i_showname') or item.get('_name') or '').strip()
        for suffix in (' (上年同月=100)', '(上年同月=100)',
                      ' (上月=100)', '(上月=100)',
                      ' (上年同期=100)', '(上年同期=100)'):
            if name.endswith(suffix):
                name = name[:-len(suffix)].strip()
                break
        result.append({'iid': item.get('_id'), 'name': name})
    _INDICATOR_CACHE[cid] = result
    return result


def query_data(cid: str, indicator_ids: list[str], period_range: str,
               region: str = '000000000000') -> dict[str, dict[str, str]]:
    """按时间范围批量查询 NBS 数据。

    Args:
        cid: 目录节点 ID
        indicator_ids: 指标 ID 列表（来自 query_indicators）
        period_range: 'YYYYMMMM-YYYYMMMM'（如 '202101MM-202512MM'）或单月
        region: 地区代码（默认全国）

    Returns:
        {time_code: {indicator_id: value_str}}，如
        ``{'202503MM': {'4ae90476...': '99.9', ...}, ...}``

    Note:
        /stream/esData 必须 JSON POST；指数类 value 为指数值（上年同月=100），
        涨跌幅需减 100。
    """
    body = {
        'cid': cid,
        'indicatorIds': indicator_ids,
        'das': [{'value': region}],
        'dts': [period_range],
        'showType': '1',
    }
    data = _request('POST', f'{NBS_API_BASE}/stream/esData', json_body=body)
    raw_list = data.get('data', []) if isinstance(data, dict) else data or []
    result: dict[str, dict[str, str]] = {}
    for time_entry in raw_list:
        time_code = time_entry.get('code', '')
        if not time_code:
            continue
        values: dict[str, str] = {}
        for v in time_entry.get('values', []):
            iid = v.get('_id', '')
            val = v.get('value', '')
            if iid and val:
                values[iid] = val
        if values:
            result[time_code] = values
    return result


def month_to_period(month: str) -> str:
    """'2025-03' → '202503MM'。"""
    parts = month.split('-')
    return f'{parts[0]}{int(parts[1]):02d}MM'


def period_to_month(period: str) -> str:
    """'202503MM' → '2025-03'。"""
    return f'{period[:4]}-{period[4:6]}'


def detect_base_period(month: str) -> str:
    """根据月份判断统计局 5 年基期版本（影响 cid 选择）。"""
    if month >= '2026-01':
        return '2026'
    elif month >= '2021-01':
        return '2021'
    elif month >= '2016-01':
        return '2016'
    return '2011'
