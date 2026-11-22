"""台湾主计总处 nstatdb（总体统计资料库）客户端。

探测逆向（2026-11，源自 factorlab 生产验证）：

- 直连 403（Cloudflare）→ 必须 socks5 代理（``DEEPMACRO_HTTP_PROXY``）；
  会话链 = GET / → GET /dgbasall/webmain.aspx?k=defjsp 种 cookie，之后仅带
  cookie、无 token/签名；偶发 JS challenge → 重试退避 + 重种植。
- 表结构：GET sys=212&funid={funid} → JSON（fldCnt/fldName/cycleDef/unitNm）。
- 数值：GET sys=220&funid={funid}&cycle={cycle}&outkind=11&outmode=8&fldlst={位图}
  &ym={起}&ymt={迄} → JSON（orgdata 序列×月矩阵 / nrow 月标签 / ncol 序列名）。
- ym/ymt = 民国年×100+月（11510 = 2026-10）；省略则全区间（nrow 含年度行，
  解析时过滤）。

现役表：A030101025 消費者物價基本分類暨項目群指數（449 序列，1981 起）、
A030701015 生產者物價基本分類指數（101 序列，2021 起）。
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Any

import requests

from deepmacro._http import get_proxies

_log = logging.getLogger(__name__)

NSTATDB_BASE = 'https://nstatdb.dgbas.gov.tw'
NSTATDB_SOURCE_URL = NSTATDB_BASE + '/'
SEED_URL = NSTATDB_BASE + '/dgbasall/webmain.aspx?k=defjsp'
STRUCT_URL = (NSTATDB_BASE + '/dgbasall/webMain.aspx?sys=212&x=2100'
              '&funid={funid}&r={r}')
_VALUES_URL_BASE = (NSTATDB_BASE + '/dgbasall/webMain.aspx?sys=220'
                    '&funid={funid}&cycle={cycle}&outkind=11&outmode=8'
                    '&fldlst={fldlst}')

_BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json, text/javascript, */*',
    'Accept-Language': 'zh-TW,zh;q=0.9',
}

TW_CPI_FUNID = 'A030101025'   # 消費者物價基本分類暨項目群指數（449 序列）
TW_PPI_FUNID = 'A030701015'   # 生產者物價基本分類指數（101 序列）

_ROC_YM_RE = re.compile(r'^(\d{1,3})年(\d{1,2})月$')


def roc_ym_to_month(roc_ym: int | str | None) -> str | None:
    """民国年月 → 'YYYY-MM'（民国年 + 1911）；年度行/非法 → None。"""
    if isinstance(roc_ym, bool) or roc_ym is None:
        return None
    if isinstance(roc_ym, int):
        year, month = divmod(roc_ym, 100)
    elif isinstance(roc_ym, str):
        s = roc_ym.strip()
        m = _ROC_YM_RE.match(s)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
        elif s.isdigit():
            year, month = divmod(int(s), 100)
        else:
            return None
    else:
        return None
    if not (1 <= month <= 12) or year < 1:
        return None
    return f'{year + 1911:04d}-{month:02d}'


def month_to_roc_ym(month: str) -> int:
    """'YYYY-MM' → 民国年×100+月（roc_ym_to_month 反函数）。"""
    return (int(month[:4]) - 1911) * 100 + int(month[5:7])


def _to_level(v: Any) -> float | None:
    """数值清洗：缺失占位与 0 → None（指数表不存在真实 0 值）。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        s = v.strip().replace(',', '')
        if s in ('', '-', '—', '…', 'N/A'):
            return None
        try:
            f = float(s)
        except ValueError:
            return None
    else:
        return None
    return f if f != 0.0 else None


def parse_nstatdb_matrix(payload: dict[str, Any]
                         ) -> dict[str, dict[str, float]]:
    """sys=220 数值 JSON → {序列名: {'YYYY-MM': level}}。

    nrow 月标签（年度行过滤）/ ncol 序列名 / orgdata 序列×月矩阵。
    """
    def _head(x: Any) -> Any:
        return x[0] if isinstance(x, (list, tuple)) and x else x

    nrow = [_head(r) for r in payload.get('nrow') or []]
    names = [str(_head(c)) for c in payload.get('ncol') or []]
    org = payload.get('orgdata') or []
    if len(names) != len(org):
        raise RuntimeError(
            f'orgdata 行数 {len(org)} 与 ncol 序列数 {len(names)} 不一致（改版？）')
    cols: list[tuple[int, str]] = []
    for idx, label in enumerate(nrow):
        month = roc_ym_to_month(label)
        if month is not None:
            cols.append((idx, month))
    series: dict[str, dict[str, float]] = {}
    for si, name in enumerate(names):
        row = org[si]
        if len(row) != len(nrow):
            raise RuntimeError(
                f'orgdata[{si}]（{name}）列数 {len(row)} 与 nrow 行数 '
                f'{len(nrow)} 不一致（改版？）')
        series[name] = {month: lv for idx, month in cols
                        if (lv := _to_level(row[idx])) is not None}
    return series


class NstatdbSession:
    """cookie 种植会话：一次种植可查多表（表结构按 funid 缓存）。

    直连 403 → 一次性切换 ``DEEPMACRO_HTTP_PROXY`` 代理；代理未配置 →
    RuntimeError。用法：``with NstatdbSession() as sess: sess.values(funid)``。
    """

    RETRIES = 2
    BACKOFF = 3.0

    def __init__(self) -> None:
        self._http = requests.Session()
        self._http.headers.update(_BROWSER_HEADERS)
        self._proxies: dict[str, str] | None = None
        self._seeded = False
        self._info: dict[str, dict[str, Any]] = {}
        self._direct_reason: str | None = None

    def __enter__(self) -> 'NstatdbSession':
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _request_once(self, url: str, timeout: float) -> requests.Response:
        """单次 GET：直连优先；403/连接失败一次性切换代理。"""
        if self._proxies is None:
            resp = None
            direct_exc: Exception | None = None
            try:
                resp = self._http.get(url, timeout=timeout)
                if resp.status_code != 403:
                    return resp
            except requests.RequestException as e:
                direct_exc = e
            reason = (str(direct_exc) if direct_exc
                      else f'HTTP {resp.status_code}' if resp is not None
                      else '未知')
            proxies = get_proxies()
            if not proxies:
                raise RuntimeError(
                    f'直连失败（{reason}）且未设置 DEEPMACRO_HTTP_PROXY——'
                    f'nstatdb 直连被 Cloudflare 拦截，必须配置代理')
            self._direct_reason = reason
            self._proxies = proxies
            self._seeded = False
        return self._http.get(url, timeout=timeout, proxies=self._proxies)

    def _seed(self) -> None:
        """种植会话 cookie：GET / → GET webmain.aspx?k=defjsp。"""
        if self._seeded:
            return
        self._request_once(NSTATDB_BASE + '/', timeout=30)
        resp = self._request_once(SEED_URL, timeout=30)
        if resp.status_code != 403:
            resp.raise_for_status()
            self._seeded = True

    def _get_json(self, url: str, timeout: float) -> Any:
        """GET → JSON；challenge/网络失败重试（退避 + 重种植）。"""
        last: Exception = RuntimeError('未执行')
        for attempt in range(self.RETRIES + 1):
            try:
                self._seed()
                resp = self._request_once(url, timeout=timeout)
            except RuntimeError:
                raise
            except requests.RequestException as e:
                last = e
            else:
                if resp.status_code != 403:
                    resp.raise_for_status()
                    return resp.json()
                last = RuntimeError('HTTP 403（Cloudflare challenge）')
            if attempt < self.RETRIES:
                _log.warning('nstatdb 请求失败（%s），%.0fs 后重试 %s/%s',
                             last, self.BACKOFF * (attempt + 1),
                             attempt + 1, self.RETRIES)
                time.sleep(self.BACKOFF * (attempt + 1))
                self._seeded = False
        detail = (f'直连={self._direct_reason}; 代理={last}'
                  if self._direct_reason else str(last))
        raise RuntimeError(f'nstatdb 请求失败（重试 {self.RETRIES} 次后）: {detail}')

    def table_info(self, funid: str) -> dict[str, Any]:
        """sys=212 表结构（按 funid 缓存）。"""
        if funid not in self._info:
            url = STRUCT_URL.format(funid=funid, r=f'{random.random():.6f}')
            self._info[funid] = self._get_json(url, timeout=60)
        return self._info[funid]

    def values(self, funid: str, ym: int | None = None,
               ymt: int | None = None, fldlst: str | None = None,
               ) -> dict[str, Any]:
        """sys=220 数值矩阵 → {序列名: {'YYYY-MM': level}}。

        fldlst 省略 → 全选；ym/ymt 省略 → 默认全区间。
        """
        info = self.table_info(funid)
        if fldlst is None:
            fldlst = '1' * int(info['fldCnt'])
        url = _VALUES_URL_BASE.format(funid=funid, cycle=info['cycleDef'],
                                      fldlst=fldlst)
        if ym is not None:
            url += f'&ym={ym}'
        if ymt is not None:
            url += f'&ymt={ymt}'
        payload = self._get_json(url, timeout=120)
        return parse_nstatdb_matrix(payload)


def fetch_taiwan_levels(funid: str,
                        start_month: str | None = None,
                        end_month: str | None = None) -> dict[str, dict[str, float]]:
    """nstatdb 表 → {序列名: {'YYYY-MM': level}}（可选区间过滤）。"""
    with NstatdbSession() as sess:
        series = sess.values(funid)
    if start_month or end_month:
        series = {
            name: {m: v for m, v in d.items()
                   if (start_month is None or m >= start_month)
                   and (end_month is None or m <= end_month)}
            for name, d in series.items()}
        series = {k: v for k, v in series.items() if v}
    if not series:
        raise RuntimeError(f'nstatdb {funid} 返回无数据')
    return series
