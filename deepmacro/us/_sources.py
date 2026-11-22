"""美国数据源客户端 — BLS API v2 + FRED fredgraph.csv。

BLS API（api.bls.gov）配额：无 key 25 序列/次、10 年跨度/次、25 次/天；
可选环境变量 ``BLS_API_KEY``（https://api.bls.gov/developers/ 免费注册）
提升至 50 序列、20 年跨度。BLS 直连在中国大陆通常被 Akamai 拦截 → 需设
``DEEPMACRO_HTTP_PROXY``。

FRED（fred.stlouisfed.org）fredgraph.csv 无 key 可用；高频请求会临时封
IP → 固定限速 + 退避。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

from deepmacro._http import get_proxies

_log = logging.getLogger(__name__)

BLS_API_URL = 'https://api.bls.gov/publicAPI/v2/timeseries/data/'
FRED_CSV_URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv'

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
# FRED 的 Akamai 拦截自定义 bot UA（HTTP 000 连接重置，2026-11 实测），
# 浏览器 UA 挂起；不设 UA（requests 默认 python-requests/x.y）实测可用。
_FRED_UA = None

_BLS_KEY = os.environ.get('BLS_API_KEY', '')
_BLS_SERIES_PER_CALL = 50 if _BLS_KEY else 25
_BLS_YEAR_SPAN = 20 if _BLS_KEY else 10

_FRED_SLEEP = 2.0


def _bls_post(payload: dict[str, Any]) -> dict:
    """POST BLS API（代理必须时走 DEEPMACRO_HTTP_PROXY），3 次指数退避。"""
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.post(BLS_API_URL, json=payload, proxies=get_proxies(),
                              timeout=30, headers={'User-Agent': _UA})
            if r.status_code == 200:
                data = r.json()
                if data.get('status') not in (None, 'REQUEST_SUCCEEDED'):
                    raise RuntimeError(f"BLS REQUEST_FAILED: {data.get('message')}")
                return data
            last_err = RuntimeError(f'BLS HTTP {r.status_code}: {r.text[:200]}')
        except requests.RequestException as e:
            last_err = e
        _log.warning('BLS post attempt %d failed: %s', attempt + 1, last_err)
        if attempt < 2:
            time.sleep(2 ** (2 * attempt + 1))
    raise RuntimeError(f'BLS API 3 次重试后仍失败: {last_err}')


def fetch_bls_series(series_ids: list[str], start_year: int, end_year: int
                     ) -> dict[str, dict[str, float]]:
    """BLS 批量取月度序列 → {series_id: {'YYYY-MM': value}}。

    自动按配额分块（序列 × 年跨度）；period 'M13'（年度均值）忽略。
    """
    result: dict[str, dict[str, float]] = {sid: {} for sid in series_ids}
    for ci in range(0, len(series_ids), _BLS_SERIES_PER_CALL):
        chunk = series_ids[ci:ci + _BLS_SERIES_PER_CALL]
        y = start_year
        while y <= end_year:
            e_year = min(y + _BLS_YEAR_SPAN - 1, end_year)
            payload: dict[str, Any] = {
                'seriesid': chunk, 'startyear': str(y), 'endyear': str(e_year)}
            if _BLS_KEY:
                payload['registrationkey'] = _BLS_KEY
            data = _bls_post(payload)
            for s in data.get('Results', {}).get('series', []):
                sid = s['seriesID']
                for d in s.get('data') or []:
                    period = str(d.get('period', ''))
                    if not (period.startswith('M') and period[1:].isdigit()
                            and 1 <= int(period[1:]) <= 12):
                        continue
                    month = f"{d['year']}-{int(period[1:]):02d}"
                    try:
                        result.setdefault(sid, {})[month] = float(d['value'])
                    except (TypeError, ValueError):
                        pass
            y = e_year + 1
    return result


def fetch_fred_series(series_id: str, observation_start: str | None = None
                      ) -> dict[str, float]:
    """FRED fredgraph.csv 单序列 → {'YYYY-MM': value}（月度）。"""
    time.sleep(_FRED_SLEEP)
    params = {'id': series_id}
    if observation_start:
        params['cosd'] = observation_start
    last_err: Exception | None = None
    text = ''
    for attempt in range(3):
        try:
            proxies = get_proxies() if attempt == 2 else None
            headers = {'User-Agent': _FRED_UA} if _FRED_UA else {}
            r = requests.get(FRED_CSV_URL, params=params, proxies=proxies,
                             timeout=30, headers=headers)
            if r.status_code == 200 and 'date' in r.text[:200].lower():
                text = r.text
                break
            last_err = RuntimeError(f'FRED HTTP {r.status_code}: {r.text[:200]}')
        except requests.RequestException as e:
            last_err = e
        _log.warning('FRED fetch %s attempt %d failed: %s',
                     series_id, attempt + 1, last_err)
        if attempt < 2:
            time.sleep(_FRED_SLEEP * (attempt + 1))
    if not text:
        raise RuntimeError(f'FRED 取数失败（{series_id}）: {last_err}')

    result: dict[str, float] = {}
    for line in text.strip().splitlines()[1:]:
        parts = line.split(',')
        if len(parts) != 2 or parts[1] in ('.', ''):
            continue
        try:
            result[parts[0][:7]] = float(parts[1])
        except ValueError:
            continue
    return result


def fetch_bls_with_fred_fallback(series_ids: list[str], start_year: int,
                                 end_year: int) -> dict[str, dict[str, float]]:
    """BLS 批量取数；BLS 整体失败时降级 FRED 逐序列取。"""
    try:
        return fetch_bls_series(series_ids, start_year, end_year)
    except Exception as e:
        _log.warning('BLS failed (%s), falling back to FRED per-series', e)
        result: dict[str, dict[str, float]] = {sid: {} for sid in series_ids}
        start_str = f'{start_year}-01-01'
        for sid in series_ids:
            try:
                result[sid] = fetch_fred_series(sid, observation_start=start_str)
            except Exception as e2:
                _log.warning('FRED fallback %s failed: %s', sid, e2)
        if not any(result.values()):
            raise RuntimeError(f'BLS 与 FRED 均失败: {e}')
        return result
